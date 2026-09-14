"""Serveur web : previsions 10 jours + historique de suivi."""
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

sys.path.insert(0, str(Path(__file__).parent))
import config
from src import cadence, db, rules
from src.sources import calendrier

app = Flask(__name__, static_folder=str(config.SITE_DIR), static_url_path="")
COLOR_NAMES = {1: "Bleu", 2: "Blanc", 3: "Rouge"}
LIVE_VERSIONS = "model_version NOT LIKE 'backtest%'"


def season_summary(conn, today=None):
    today = today or date.today()
    season = calendrier.season_of(today)
    rows = conn.execute(
        "SELECT color, COUNT(*) n FROM days WHERE season = ? AND color IS NOT NULL GROUP BY color",
        (season,)).fetchall()
    counts = {r["color"]: r["n"] for r in rows}
    # Le quota Bleu est le solde des deux autres sur la duree reelle de la saison :
    # 300 jours d'ordinaire, 301 quand un 29 fevrier tombe dedans.
    duree = (calendrier.season_end(season) - calendrier.season_start(season)).days + 1
    quota_bleu = duree - config.QUOTA_BLANC - config.QUOTA_ROUGE
    return {
        "season": season,
        "quota_bleu": quota_bleu,
        "quota_blanc": config.QUOTA_BLANC,
        "quota_rouge": config.QUOTA_ROUGE,
        "rouge_used": counts.get(3, 0), "rouge_left": config.QUOTA_ROUGE - counts.get(3, 0),
        "blanc_used": counts.get(2, 0), "blanc_left": config.QUOTA_BLANC - counts.get(2, 0),
        "bleu_used": counts.get(1, 0), "bleu_left": quota_bleu - counts.get(1, 0),
    }


ROUGE_MONTHS_SQL = ", ".join(str(m) for m in sorted(rules.ROUGE_MONTHS))
# En deca, une precision affichee serait une anecdote deguisee en mesure.
MIN_ANNONCES = 20


def period_clause(period):
    """Restreint l'evaluation a la periode demandee.

    Un taux de reussite sur l'annee entiere est flatte par les mois sans enjeu :
    d'avril a octobre la reponse est Bleu et le modele ne risque rien. 'hiver' garde
    la fenetre ou un Rouge est possible ; 'eligibles' va au bout en ne gardant que
    les jours ou il l'est vraiment (lundi-vendredi, hors feries) -- le denominateur
    honnete, celui ou le modele a reellement un choix a faire.
    """
    if period not in ("hiver", "eligibles"):
        return ""
    clause = f" AND CAST(strftime('%m', p.target_date) AS INTEGER) IN ({ROUGE_MONTHS_SQL})"
    if period == "eligibles":
        clause += " AND d.weekday < 5 AND d.is_holiday = 0"
    return clause


def dernier_passage(conn):
    """Instant du dernier passage, programme ou non.

    Toujours vrai, lui : meme sans cadence mesurable, la page peut annoncer « derniere
    mise a jour il y a X ». C'est le repli honnete quand on ne sait pas promettre la
    suivante.
    """
    tables = {r["name"] for r in
              conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "runs" not in tables:
        return None
    row = conn.execute("SELECT MAX(run_datetime) d FROM runs").fetchone()
    return row["d"] if row else None


def fiabilite_par_horizon(conn):
    """Ce que vaut une prediction A SON ECHEANCE, sur le perimetre honnete.

    La page vend des couleurs a dix jours, et la mesure de ce qu'elles valent vivait
    dans l'AUTRE onglet, derriere deux filtres a regler. Un « Rouge 34 % » a J+7
    n'apprend rien tant qu'on ignore combien d'alertes de cette echeance se
    confirment. On la sert donc avec la prevision elle-meme.

    Perimetre eligible (lun-ven, nov-mars, hors feries) et predictions non
    officielles : le denominateur ou le modele a reellement un choix a faire. Le
    mesurer ailleurs le flatterait de vingt points sans rien dire de plus.
    """
    rows = conn.execute(
        f"""SELECT p.horizon, p.predicted_color, d.color AS actual
            FROM predictions_a_jour p JOIN days d ON d.date = p.target_date
            WHERE d.color IS NOT NULL AND p.is_official = 0
            {period_clause("eligibles")}""").fetchall()
    par_h = {}
    for r in rows:
        v = par_h.setdefault(r["horizon"], {
            "n": 0, "ok": 0,
            "annonces": {1: 0, 2: 0, 3: 0},   # combien de fois cette couleur a ete dite
            "justes": {1: 0, 2: 0, 3: 0},     # ... et combien de fois elle est tombee
            "reels": {1: 0, 2: 0, 3: 0},      # combien de fois elle est vraiment tombee
        })
        v["n"] += 1
        v["ok"] += r["predicted_color"] == r["actual"]
        v["annonces"][r["predicted_color"]] += 1
        v["reels"][r["actual"]] += 1
        if r["predicted_color"] == r["actual"]:
            v["justes"][r["actual"]] += 1
    # Precision ET rappel, jamais l'un sans l'autre : annoncer Rouge tous les jours
    # rappelle 100 % des Rouges. Le lecteur a besoin des deux risques, celui de
    # subir un jour cher et celui de s'organiser pour rien.
    #
    # MIN_ANNONCES vaut pour CHAQUE couleur separement. Un horizon peut totaliser
    # quatre cents predictions et n'avoir annonce Blanc que quatre fois : « 3 sur 4 »
    # serait alors une anecdote presentee comme une mesure. Le seuil global ne protege
    # pas de ca -- c'est le denominateur de la couleur qui compte, pas celui du lot.
    def taux(num, den):
        return num / den if den >= MIN_ANNONCES else None

    return {str(h): {
        "n": v["n"],
        "accuracy": v["ok"] / v["n"],
        "precision": {c: taux(v["justes"][c], v["annonces"][c]) for c in (1, 2, 3)},
        "rappel": {c: taux(v["justes"][c], v["reels"][c]) for c in (1, 2, 3)},
        "annonces": v["annonces"],
    } for h, v in sorted(par_h.items()) if v["n"] >= 30}


def changements(conn, run_date):
    """Ce qui a bouge depuis le calcul precedent, par date cible.

    La page montrait un etat, jamais un mouvement -- alors que « jeudi est passe de
    Blanc a Rouge » est ce qui fait agir, et que c'est deja en base : chaque passage
    y laisse ses predictions.

    La comparaison porte sur le dernier run_date ANTERIEUR, donc la veille, et non
    sur le passage precedent : trois passages tombent dans la meme journee et l'ecart
    de quelques heures entre deux d'entre eux ne raconte rien.
    """
    veille = conn.execute(
        f"""SELECT MAX(run_date) FROM predictions_a_jour
            WHERE run_date < ? AND {LIVE_VERSIONS}""", (run_date,)).fetchone()[0]
    if not veille:
        return {}, None
    rows = conn.execute(
        f"""SELECT target_date, predicted_color FROM predictions_a_jour
            WHERE run_date = ? AND {LIVE_VERSIONS}""", (veille,)).fetchall()
    return {r["target_date"]: r["predicted_color"] for r in rows}, veille


# Chaque source est jugee sur la DERNIERE DATE QU'ELLE COUVRE, pas sur l'instant du
# dernier appel reussi. Une source qui repond 200 avec une charge utile vide passerait
# pour vivante au `fetched_at` alors qu'elle n'apporte plus rien depuis des semaines ;
# sa couverture, elle, cesse d'avancer dans les deux cas.
#
# `attendu` est le decalage NORMAL de la source, en jours : negatif pour celles qui
# regardent le passe (ERA5 arrive avec six jours de retard par construction), positif
# pour celles qui portent sur l'avenir. `tolerance` absorbe le fonctionnement ordinaire
# -- un passage du matin avant l'annonce RTE, un week-end de publication. Large
# volontairement : un faux « hors service » sur un site qui marche coute plus cher que
# le silence, parce qu'on cesse de croire l'indicateur.
SOURCES = [
    ("Couleurs officielles (RTE)",
     "SELECT MAX(date) FROM days WHERE color IS NOT NULL", 0, 1),
    ("Prévision météo",
     "SELECT MAX(date) FROM weather WHERE lead > 0", config.MAX_HORIZON, 2),
    ("Météo observée (ERA5)",
     "SELECT MAX(date) FROM weather WHERE lead = 0", -6, 4),
    ("Éolien / solaire",
     "SELECT MAX(date) FROM renewables WHERE lead > 0", config.MAX_HORIZON, 2),
    ("Consommation (eCO2mix)",
     "SELECT MAX(date) FROM conso", -1, 3),
    ("Indisponibilités (RTE)",
     "SELECT MAX(publication_date) FROM unavailabilities", -1, 4),
]


def sante_sources(conn, today=None):
    """L'etat de chaque source, en une ligne chacune.

    Rend `retard` = de combien de jours la couverture est EN DECA de ce qu'elle
    devrait etre, jamais negatif : une source en avance n'est pas un probleme, et
    afficher « -1 jour de retard » ferait douter du reste.

    Une table absente ou vide n'est pas une panne : c'est une source non configuree
    (RTE sans cle) ou une base neuve. On le dit, on ne crie pas.
    """
    today = today or date.today()
    tables = {r["name"] for r in
              conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    out = []
    for nom, requete, attendu, tolerance in SOURCES:
        table = requete.split(" FROM ")[1].split()[0]
        couvre = None
        if table in tables:
            couvre = conn.execute(requete).fetchone()[0]
        if not couvre:
            out.append({"nom": nom, "couvre": None, "retard": None,
                        "tolerance": tolerance, "etat": "absente"})
            continue
        retard = max(0, ((today + timedelta(days=attendu))
                         - date.fromisoformat(couvre)).days)
        out.append({"nom": nom, "couvre": couvre, "retard": retard,
                    "tolerance": tolerance,
                    "etat": "ok" if retard <= tolerance else "en retard"})
    return out


def tariff_payload():
    """Grille tarifaire servie a la page, pour afficher le prix de chaque journee."""
    return {
        "label": config.TARIFF_LABEL,
        "effective": config.TARIFF_EFFECTIVE,
        "offpeak_hours": config.TARIFF_OFFPEAK_HOURS,
        "by_color": {str(c): v for c, v in config.TARIFFS.items()},
    }


@app.get("/")
def index():
    return send_from_directory(str(config.SITE_DIR), "index.html")


@app.get("/api/forecast")
def forecast():
    conn = db.connect()
    last_run = conn.execute(
        f"SELECT MAX(run_date) FROM predictions_a_jour WHERE {LIVE_VERSIONS}").fetchone()[0]
    items = []
    avant, veille = changements(conn, last_run) if last_run else ({}, None)
    if last_run:
        # J+0 : la couleur du jour, connue et non predite. Sans elle la page
        # commence a demain, alors que c'est aujourd'hui qu'on consomme.
        today_row = conn.execute(
            "SELECT date, color FROM days WHERE date = ? AND color IS NOT NULL",
            (last_run,)).fetchone()
        if today_row:
            items.append({
                "date": today_row["date"], "horizon": 0,
                "weekday": date.fromisoformat(today_row["date"]).weekday(),
                "color": today_row["color"], "color_name": COLOR_NAMES[today_row["color"]],
                "p": None, "official": True, "confidence": 1.0,
            })
        rows = conn.execute(
            f"""SELECT p.*, d.color AS actual, d.is_holiday
                FROM predictions_a_jour p LEFT JOIN days d ON d.date = p.target_date
                WHERE p.run_date = ? AND {LIVE_VERSIONS} ORDER BY p.horizon""",
            (last_run,)).fetchall()
        for r in rows:
            official = r["actual"] is not None
            it = {
                "date": r["target_date"], "horizon": r["horizon"],
                "weekday": date.fromisoformat(r["target_date"]).weekday(),
                "color": r["actual"] if official else r["predicted_color"],
                "color_name": COLOR_NAMES[r["actual"] if official else r["predicted_color"]],
                "p": [r["p_bleu"], r["p_blanc"], r["p_rouge"]],
                "official": official,
                "confidence": max(r["p_bleu"], r["p_blanc"], r["p_rouge"]),
            }
            # Un jour devenu officiel n'a pas « change d'avis » : RTE a tranche. Le
            # signaler comme un revirement du modele serait un contresens.
            precedent = avant.get(r["target_date"])
            if not official and precedent and precedent != r["predicted_color"]:
                it["change"] = {"from": precedent, "from_name": COLOR_NAMES[precedent],
                                "since": veille}
            items.append(it)
    # 'forecast' d'abord (les jours a venir), puis l'observe pour J+0.
    weather = {}
    for source in ("era5", "forecast"):
        for r in conn.execute(
                "SELECT date, tmean, tmin, tmax FROM weather WHERE source = ?", (source,)):
            weather[r["date"]] = dict(r)
    for it in items:
        w = weather.get(it["date"])
        it["tmean"] = round(w["tmean"], 1) if w else None
        it["tmin"] = round(w["tmin"], 1) if w else None
        it["tmax"] = round(w["tmax"], 1) if w else None
    # `schedules_utc` reste publie pour dire ce qui est DEMANDE ; `cadence` dit ce qui
    # est OBTENU, mesure sur les passages reellement effectues. C'est la seconde que la
    # page affiche -- la premiere s'est revelee fausse de deux a quatre heures.
    return jsonify({"run_date": last_run, "days": items,
                    "season": season_summary(conn), "tariffs": tariff_payload(),
                    "schedules_utc": config.SCHEDULES_UTC,
                    "fiabilite": fiabilite_par_horizon(conn),
                    "sources": sante_sources(conn),
                    "cadence": cadence.observee(
                        cadence.avec_amorce(db.passages_reguliers(conn))),
                    "derniere_maj": dernier_passage(conn)})


@app.get("/api/history")
def history():
    """Predictions figees confrontees a la couleur reellement tombee."""
    conn = db.connect()
    horizon = request.args.get("horizon", type=int)
    limit = request.args.get("limit", default=400, type=int)
    source = request.args.get("source", default="all")
    period = request.args.get("period", default="all")
    where = ["d.color IS NOT NULL"]
    params = []
    if horizon:
        where.append("p.horizon = ?")
        params.append(horizon)
    if source == "live":
        where.append(LIVE_VERSIONS)
    elif source == "backtest":
        where.append("model_version LIKE 'backtest%'")
    rows = conn.execute(
        f"""SELECT p.*, d.color AS actual FROM predictions_a_jour p
            JOIN days d ON d.date = p.target_date
            WHERE {' AND '.join(where)}{period_clause(period)}
            ORDER BY p.target_date DESC, p.horizon LIMIT ?""",
        (*params, limit)).fetchall()
    return jsonify([{
        "target_date": r["target_date"], "horizon": r["horizon"],
        "predicted": r["predicted_color"], "predicted_name": COLOR_NAMES[r["predicted_color"]],
        "actual": r["actual"], "actual_name": COLOR_NAMES[r["actual"]],
        "correct": r["predicted_color"] == r["actual"],
        "p": [r["p_bleu"], r["p_blanc"], r["p_rouge"]],
        "official": bool(r["is_official"]),
        "backtest": r["model_version"].startswith("backtest"),
    } for r in rows])


@app.get("/api/accuracy")
def accuracy():
    """Precision par horizon + matrice de confusion, sur les predictions figees."""
    conn = db.connect()
    source = request.args.get("source", default="all")
    horizon = request.args.get("horizon", type=int)
    period = request.args.get("period", default="all")
    cond, params = "", []
    if source == "live":
        cond = f"AND {LIVE_VERSIONS}"
    elif source == "backtest":
        cond = "AND model_version LIKE 'backtest%'"
    if horizon:
        cond += " AND p.horizon = ?"
        params.append(horizon)
    cond += period_clause(period)
    rows = conn.execute(
        f"""SELECT p.horizon, p.predicted_color, p.p_rouge, d.color AS actual
            FROM predictions_a_jour p JOIN days d ON d.date = p.target_date
            WHERE d.color IS NOT NULL AND p.is_official = 0 {cond}""", params).fetchall()
    by_h, confusion = {}, [[0] * 3 for _ in range(3)]
    for r in rows:
        h = by_h.setdefault(r["horizon"], {"n": 0, "ok": 0, "rouge_total": 0,
                                           "rouge_found": 0, "rouge_flagged": 0})
        h["n"] += 1
        h["ok"] += r["predicted_color"] == r["actual"]
        h["rouge_flagged"] += r["predicted_color"] == 3
        if r["actual"] == 3:
            h["rouge_total"] += 1
            h["rouge_found"] += r["predicted_color"] == 3
        confusion[r["actual"] - 1][r["predicted_color"] - 1] += 1
    # Le rappel seul ne dit que la moitie de l'histoire : il mesure le risque de
    # rater un Rouge, jamais celui de s'organiser pour rien. Les deux se lisent
    # ensemble ou pas du tout -- on peut toujours rappeler 100 % en annonçant Rouge
    # tous les jours.
    out = [{
        "horizon": h, "n": v["n"], "accuracy": v["ok"] / v["n"] if v["n"] else None,
        "rouge_recall": v["rouge_found"] / v["rouge_total"] if v["rouge_total"] else None,
        "rouge_precision": (v["rouge_found"] / v["rouge_flagged"]
                            if v["rouge_flagged"] else None),
        "rouge_total": v["rouge_total"], "rouge_flagged": v["rouge_flagged"],
    } for h, v in sorted(by_h.items())]
    return jsonify({"by_horizon": out, "confusion": confusion, "n": len(rows),
                    "period": period, "reliability": reliability(rows)})


def reliability(rows, bins=10):
    """La probabilite annoncee tient-elle ses promesses ?

    La page vend des probabilites ; sans cette mesure, rien ne dit qu'un « 30 % de
    Rouge » tombe Rouge trois fois sur dix. On regroupe les predictions par tranche
    de probabilite annoncee et on compare a la frequence reellement observee.
    """
    buckets = [{"lo": i / bins, "hi": (i + 1) / bins, "n": 0, "sum_p": 0.0, "hits": 0}
               for i in range(bins)]
    for r in rows:
        p = r["p_rouge"]
        if p is None:
            continue
        b = buckets[min(bins - 1, int(p * bins))]
        b["n"] += 1
        b["sum_p"] += p
        b["hits"] += r["actual"] == 3
    return [{
        "bin": f"{b['lo']:.0%}-{b['hi']:.0%}", "n": b["n"],
        "predicted": b["sum_p"] / b["n"], "observed": b["hits"] / b["n"],
    } for b in buckets if b["n"] >= 20]


@app.get("/api/backtest")
def backtest_report():
    files = sorted(config.REPORTS_DIR.glob("backtest_*.json"))
    if not files:
        return jsonify({})
    return jsonify(json.loads(files[-1].read_text(encoding="utf-8")))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5173, debug=False)
