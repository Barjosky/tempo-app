"""Pipeline quotidien : couleurs officielles + meteo + predictions figees.

A lancer une fois par jour apres la publication RTE (~11h).
    python collector.py            # mise a jour du jour
    python collector.py --train    # re-entraine le modele avant de predire
"""
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config
from src import db, features, predict
from src.sources import calendrier, eco2mix, meteo, tempo_api


# Ce fichier n'existe que le temps d'un passage : il dit a l'atelier, APRES la
# publication, qu'une source a manque. Il est dans l'espace de travail et non dans
# `store/`, qui est mis en cache : un marqueur perime y survivrait d'un run a l'autre
# et signalerait une panne deja passee.
DEGRADATIONS = Path(__file__).parent / ".degradations"
_degradees = []


def optionnelle(nom, fonction, *args):
    """Une source dont la panne ne doit PAS emporter le passage entier.

    Le 14 septembre 2026, Open-Meteo a rendu un corps vide sur l'archive ERA5 et la
    collecte est morte -- alors que les couleurs officielles etaient a jour, que RTE
    avait livre ses 218 arrets, et que la prevision du jour ne demandait rien de plus.
    ERA5 a SIX JOURS de retard par construction (`run_date - 6`) : c'est la donnee la
    moins urgente de la chaine, et elle a empeche de publier la plus urgente.

    Le repli n'est pas le silence pour autant. Une source peut tomber une fois -- ca
    se rattrape au passage suivant -- ou rester morte des semaines, et de l'exterieur
    les deux se ressemblent. La panne est donc consignee, et l'atelier rougit le
    passage APRES avoir publie : la page est fraiche ET l'alarme est visible.
    """
    try:
        return fonction(*args)
    except Exception as exc:
        _degradees.append(f"{nom} : {exc}")
        print(f"Source degradee, on continue sans elle — {nom} : {exc}")
        return None


def chiffre(n):
    """« aucune » plutot que « None » : ces lignes se lisent en cherchant une panne."""
    return "aucune (source degradee)" if n is None else n


def refresh_colors(conn):
    """Couleurs officielles : saison en cours (inclut le J+1 annonce par RTE)."""
    season = calendrier.season_of(date.today())
    rows = tempo_api.to_day_rows(tempo_api.fetch_season(season))
    db.upsert_days(conn, rows)
    known = [r for r in rows if r["color"]]
    return known[-1]["date"] if known else None


def ensure_calendar(conn, run_date):
    """Cree les lignes calendaires de la fenetre de prevision.

    L'API des couleurs ne publie que les jours qu'elle connait ; sans ces lignes,
    le dernier jour de la fenetre est silencieusement absent de la page.
    """
    now = datetime.now().isoformat(timespec="seconds")
    rows = []
    for h in range(1, config.MAX_HORIZON + 1):
        d = run_date + timedelta(days=h)
        rows.append({
            "date": d.isoformat(), "season": calendrier.season_of(d), "color": None,
            "weekday": d.weekday(), "is_holiday": int(calendrier.is_holiday(d)),
            "source": "calendrier", "fetched_at": now,
        })
    db.upsert_days(conn, rows)
    return len(rows)


def refresh_forecast_weather(conn, run_date):
    """Prevision a venir, rangee avec lead = jours d'avance (plafonne a l'echeance max
    archivee, pour que les features soient lues exactement comme a l'entrainement).
    Les jours passes recents sont ranges en lead 0 : ils comblent le retard d'ERA5."""
    series = meteo.fetch_forecast()
    era5 = {r[0] for r in conn.execute("SELECT date FROM weather WHERE lead = 0 AND source = 'era5'")}
    rows = []
    for day, vals in series.items():
        target = date.fromisoformat(day)
        delta = (target - run_date).days
        if delta < 0:
            if day in era5:  # la reanalyse fait foi, ne pas l'ecraser
                continue
            lead = 0
        elif delta <= config.MAX_HORIZON:
            lead = min(delta, config.MAX_ARCHIVED_LEAD)
        else:
            continue
        rows += meteo.to_weather_rows({day: vals}, lead, "forecast")
    db.upsert_weather(conn, rows)
    return len(rows)


def refresh_observed_weather(conn, run_date):
    """Complete la reanalyse ERA5 (disponible avec ~5 jours de retard)."""
    last = conn.execute("SELECT MAX(date) FROM weather WHERE lead = 0 AND source = 'era5'").fetchone()[0]
    start = date.fromisoformat(last) + timedelta(days=1) if last else date(config.FIRST_SEASON, 9, 1)
    end = run_date - timedelta(days=6)
    if start > end:
        return 0
    series = meteo.fetch_archive(start, end)
    db.upsert_weather(conn, meteo.to_weather_rows(series, 0, "era5"))
    return len(series)


def refresh_renewables(conn, run_date):
    """Indices eolien/solaire a venir, meme convention de lead que la meteo."""
    series = meteo.fetch_renewables(run_date, run_date, forecast=True)[0]
    rows = []
    for day, vals in series.items():
        delta = (date.fromisoformat(day) - run_date).days
        if delta < 0:
            lead = 0
        elif delta <= config.MAX_HORIZON:
            lead = min(delta, config.MAX_ARCHIVED_LEAD)
        else:
            continue
        rows += meteo.to_renewable_rows({day: vals}, lead, "forecast")
    db.upsert_renewables(conn, rows)
    return len(rows)


def refresh_conso(conn, run_date):
    """Consommation et production reelles des jours recents (eCO2mix temps reel)."""
    series = eco2mix.fetch_recent(run_date - timedelta(days=20), run_date + timedelta(days=1))
    db.upsert_conso(conn, eco2mix.to_rows(series, "eco2mix-tr"))
    return len(series)


def main():
    run_date = date.today()
    # Toujours effacer avant de commencer : un marqueur qui survit a son passage
    # ferait rougir le suivant pour une panne deja reparee.
    DEGRADATIONS.unlink(missing_ok=True)
    _degradees.clear()
    conn = db.connect()
    db.init_db(conn)

    last_known = refresh_colors(conn)
    ensure_calendar(conn, run_date)
    print(f"Couleurs officielles a jour jusqu'au {last_known}")

    # Ce qui est OPTIONNEL porte sur le passe et se rattrape au passage suivant ;
    # ce qui est STRICT nourrit la prediction du jour. La prevision meteo reste donc
    # bloquante : sans elle on publierait les couleurs d'hier en les datant
    # d'aujourd'hui, ce qui est pire que ne pas publier.
    n_obs = optionnelle("meteo observee (ERA5)", refresh_observed_weather, conn, run_date)
    n_fc = refresh_forecast_weather(conn, run_date)
    print(f"Meteo : {chiffre(n_obs)} jours observes ajoutes, {n_fc} echeances de prevision")

    n_ren = optionnelle("eolien/solaire", refresh_renewables, conn, run_date)
    print(f"Eolien/solaire : {chiffre(n_ren)} echeances")

    n_conso = optionnelle("consommation (eCO2mix)", refresh_conso, conn, run_date)
    print(f"Consommation reelle (eCO2mix) : {chiffre(n_conso)} jours")

    store = features.FeatureStore(conn)
    if "--train" in sys.argv or predict.besoin_d_entrainement():
        gbm, version = predict.train(conn, store=store)
        print(f"Modele entraine : {version} (seuil alerte rouge {gbm.rouge_threshold:.2f})")

    # L'instant reel du passage, enregistre APRES la collecte : c'est le moment ou les
    # donnees sont pretes, donc celui que la page doit annoncer. `GITHUB_EVENT_NAME`
    # separe un passage programme d'un essai manuel, qui n'a rien a dire sur la cadence.
    db.enregistrer_passage(conn,
                           datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           run_date.isoformat(),
                           os.environ.get("GITHUB_EVENT_NAME", "local"))

    rows = predict.predict_next_days(conn, run_date, store=store)
    names = {1: "Bleu", 2: "Blanc", 3: "Rouge"}
    print(f"\nPredictions du {run_date} :")
    for r in rows:
        tag = " (officiel RTE)" if r["is_official"] else ""
        print(f"  J+{r['horizon']:<2} {r['target_date']}  {names[r['predicted_color']]:<5}"
              f"  bleu {r['p_bleu']:.0%} blanc {r['p_blanc']:.0%} rouge {r['p_rouge']:.0%}{tag}")

    if _degradees:
        DEGRADATIONS.write_text("\n".join(_degradees) + "\n", encoding="utf-8")
        print(f"\n{len(_degradees)} source(s) degradee(s) — la prevision est publiee, "
              "mais le passage sera signale en echec.")


if __name__ == "__main__":
    main()
