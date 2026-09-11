"""Fige les reponses de l'API en fichiers JSON, pour un hebergement statique (Netlify).

On interroge le serveur Flask via son client de test plutot que de reecrire les
requetes SQL : la page statique sert donc exactement les memes donnees que la
version locale, sans risque de divergence entre les deux implementations.
"""
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config
from app import app
from src import db

OUT = config.SITE_DIR / "data"
SOURCES = ["all", "live", "backtest"]
PERIODS = list(config.PERIODS)
# Le tableau est croise avec les periodes, donc son nombre de fichiers triple. On
# compense en montrant 200 lignes plutot que 400 : c'est un tableau de consultation,
# et les statistiques -- elles -- sont calculees sur la totalite des predictions.
HISTORY_LIMIT = 200


# Un navigateur garde la feuille de style et le script en cache bien plus longtemps
# que le HTML. Une mise en ligne peut donc servir la nouvelle page avec l'ancien
# script -- et l'utilisateur voit une version melangee sans qu'aucun outil ne signale
# quoi que ce soit. Suffixer chaque lien d'une empreinte du contenu change l'URL des
# que le fichier change, ce qui force le rechargement, et seulement quand il le faut.
ASSETS = ("styles.css", "app.js")


def estampiller(racine=None):
    """Reecrit index.html avec l'empreinte des assets. Rend True s'il a change."""
    racine = racine or Path(__file__).parent
    page = racine / "index.html"
    html = page.read_text(encoding="utf-8")
    avant = html
    for nom in ASSETS:
        fichier = racine / nom
        if not fichier.exists():
            continue
        empreinte = hashlib.sha256(fichier.read_bytes()).hexdigest()[:8]
        html = re.sub(rf'(["\'])({re.escape(nom)})(\?v=[0-9a-f]+)?\1',
                      rf'\g<1>\g<2>?v={empreinte}\g<1>', html)
    if html != avant:
        page.write_text(html, encoding="utf-8")
        return True
    return False


def dump(client, name, url):
    resp = client.get(url)
    if resp.status_code != 200:
        raise SystemExit(f"{url} a repondu {resp.status_code}")
    (OUT / f"{name}.json").write_text(
        json.dumps(resp.get_json(), separators=(",", ":")), encoding="utf-8")
    return len(resp.data)


def sante_indisponibilites():
    """Sur quoi reposent vraiment les cinq colonnes d'offre ?

    Elles sont arrivees avec une question non tranchee : 135 913 arrets pour 135 934
    paliers, soit un pour un. Ou bien chaque version d'arret ne porte qu'un palier, ou
    bien `values` est absent de la reponse et le repli sur la puissance INSTALLEE
    surestime tout arret partiel. Le backtest aime ces colonnes ; ca ne dit pas ce
    qu'elles contiennent.

    Le discriminant est dans la base, pas dans les logs de collecte : le repli pose
    `unavailable_mw = installed_mw`, un palier publie ne le fait qu'exceptionnellement.
    La reponse s'affiche donc a CHAQUE passage, en fin de run, la ou on la lit sans
    fouiller -- plutot que dans une ligne noyee au milieu de quatre-vingts fenetres.
    """
    conn = db.connect()
    tables = {r["name"] for r in
              conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "unavailabilities" not in tables:
        return
    r = conn.execute("""SELECT COUNT(*) n,
                               COUNT(DISTINCT identifier) arrets,
                               SUM(unavailable_mw IS NULL) sans_mw,
                               SUM(installed_mw IS NOT NULL
                                   AND unavailable_mw = installed_mw) pleine_puissance
                        FROM unavailabilities""").fetchone()
    if not r["n"]:
        print("Indisponibilites RTE : table vide (pas de cle ?) — colonnes d'offre a NaN")
        return
    multi = conn.execute("""SELECT COUNT(*) n FROM (
                              SELECT identifier, version FROM unavailabilities
                              GROUP BY identifier, version HAVING COUNT(*) > 1)""").fetchone()["n"]
    part = (r["pleine_puissance"] or 0) / r["n"]
    print(f"Indisponibilites RTE : {r['n']} paliers, {r['arrets']} arrets, "
          f"{multi} versions a plusieurs paliers, "
          f"{part:.0%} a la puissance installee entiere, {r['sans_mw'] or 0} sans puissance")


def main():
    # On ne vide que les JSON : le dossier peut contenir autre chose (sur le depot
    # publie, il est a la racine du site).
    OUT.mkdir(parents=True, exist_ok=True)
    for ancien in OUT.glob("*.json"):
        ancien.unlink()
    total = 0
    with app.test_client() as client:
        total += dump(client, "forecast", "/api/forecast")
        for source in SOURCES:
            # Tout est filtre par le serveur, y compris le tableau : la troncature du
            # tableau s'applique APRES le filtre. Le filtrer dans la page donnerait un
            # tableau vide en mode hiver, les lignes les plus recentes etant estivales.
            for period in PERIODS:
                suffix = "" if period == "all" else f"_{period}"
                base = f"source={source}&period={period}"
                total += dump(client, f"history_{source}{suffix}",
                              f"/api/history?{base}&limit={HISTORY_LIMIT}")
                total += dump(client, f"accuracy_{source}{suffix}", f"/api/accuracy?{base}")
                for h in range(1, config.MAX_HORIZON + 1):
                    total += dump(client, f"history_{source}{suffix}_h{h}",
                                  f"/api/history?{base}&horizon={h}&limit={HISTORY_LIMIT}")
                    total += dump(client, f"accuracy_{source}{suffix}_h{h}",
                                  f"/api/accuracy?{base}&horizon={h}")
        try:
            total += dump(client, "backtest", "/api/backtest")
        except SystemExit:
            pass  # aucun rapport de backtest publie : la page s'en passe

    (OUT / "meta.json").write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "periods": config.PERIODS,
    }), encoding="utf-8")

    sante_indisponibilites()

    files = len(list(OUT.glob("*.json")))
    print(f"{files} fichiers ecrits dans {OUT} ({total / 1024:.0f} Ko)")
    if estampiller():
        print("index.html reestampille : le cache des navigateurs sera invalide")


if __name__ == "__main__":
    main()
