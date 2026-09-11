"""Collecte les indisponibilites de production publiees par RTE.

    python ingest_rte.py            # rattrape depuis la derniere publication connue
    python ingest_rte.py --complet  # rebalaye tout l'historique depuis FIRST_SEASON

La fenetre est balayee en dates de PUBLICATION, pas en dates d'arret, et toutes les
versions sont conservees. C'est ce qui permettra de demander plus tard « que savait-on
le 12 janvier ? » sans y glisser ce qu'on a appris le 13.

Sans identifiants RTE le script ne fait rien et sort en silence : la source est
facultative, le reste du projet tourne sans.
"""
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config
from src import db
from src.sources import rte

# Fenetres de 30 jours : assez larges pour ne pas multiplier les appels, assez etroites
# pour que la pagination reste courte et qu'un echec ne coute qu'un mois a refaire.
PAS = 30

# Marge de recouvrement du rattrapage quotidien. Une publication peut arriver apres le
# passage de la veille ; reprendre trois jours en arriere coute trois appels et evite
# un trou permanent dans la serie.
RECOUVREMENT = 3


def debut_a_rattraper(conn, complet):
    if complet:
        return date(config.FIRST_SEASON, 1, 1)
    row = conn.execute("SELECT MAX(publication_date) d FROM unavailabilities").fetchone()
    if not row or not row["d"]:
        return date(config.FIRST_SEASON, 1, 1)
    return date.fromisoformat(row["d"]) - timedelta(days=RECOUVREMENT)


def main():
    complet = "--complet" in sys.argv
    if not rte.disponible():
        print("Aucun identifiant RTE (RTE_BASIC_AUTH) : collecte ignoree.")
        print("Le modele tourne sans -- les colonnes d'indisponibilite sortiront a NaN.")
        return
    conn = db.connect()
    db.init_db(conn)

    maintenant = datetime.now(timezone.utc).isoformat(timespec="seconds")
    debut = debut_a_rattraper(conn, complet)
    fin = date.today() + timedelta(days=1)
    print(f"Publications du {debut} au {fin} (fenetres de {PAS} jours)")

    total_lignes, total_arrets, echecs = 0, 0, []
    curseur = debut
    while curseur < fin:
        stop = min(fin, curseur + timedelta(days=PAS))
        try:
            evts = rte.arrets(curseur, stop, date_type="PUBLICATION_DATE",
                              derniere_version=False)
        except rte.ErreurRTE as e:
            # Un refus sur une fenetre ne doit pas emporter les suivantes : on le note
            # et on continue, le rattrapage du lendemain repassera dessus.
            echecs.append((curseur, e.code, e.corps[:120]))
            curseur = stop
            continue
        lignes = []
        for e in evts:
            lignes += rte.paliers(e, maintenant)
        if lignes:
            db.upsert_unavailabilities(conn, lignes)
        total_arrets += len(evts)
        total_lignes += len(lignes)
        print(f"  {curseur} -> {stop} : {len(evts)} arrets, {len(lignes)} paliers")
        curseur = stop
        time.sleep(1)

    for quand, code, corps in echecs:
        print(f"  ECHEC {quand} — HTTP {code} — {corps}")

    row = conn.execute("""SELECT COUNT(*) n, COUNT(DISTINCT identifier) a,
                                 MIN(publication_date) p0, MAX(publication_date) p1
                          FROM unavailabilities""").fetchone()
    nuc = conn.execute("""SELECT COUNT(DISTINCT identifier) n FROM unavailabilities
                          WHERE fuel_type = 'NUCLEAR'""").fetchone()["n"]
    print(f"\nRecu : {total_arrets} arrets, {total_lignes} paliers")
    print(f"Base : {row['n']} paliers, {row['a']} arrets distincts "
          f"(dont {nuc} nucleaires), publies du {row['p0']} au {row['p1']}")
    if echecs:
        raise SystemExit(f"{len(echecs)} fenetre(s) en echec — voir ci-dessus")


if __name__ == "__main__":
    main()
