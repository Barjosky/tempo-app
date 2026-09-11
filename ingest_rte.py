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


def borne_de_fin(horloge=None):
    """Derniere date de publication demandable a RTE.

    En PUBLICATION_DATE, RTE refuse une borne de fin dans le futur : « Publication
    dates must be in the past » (UNADINFO_GENUN_F02). La minute de retrait absorbe le
    decalage d'horloge entre le runner et RTE. S'arreter a minuit AUJOURD'HUI serait
    l'autre facon d'eviter le refus, mais elle perdrait les declarations de la matinee
    -- donc les avaries fortuites, celles qui apportent le plus d'information.
    """
    return (horloge or datetime.now(timezone.utc)) - timedelta(minutes=1)


def main():
    complet = "--complet" in sys.argv
    if not rte.disponible():
        print("Aucun identifiant RTE (RTE_BASIC_AUTH) : collecte ignoree.")
        print("Le modele tourne sans -- les colonnes d'indisponibilite sortiront a NaN.")
        return
    conn = db.connect()
    db.init_db(conn)

    horloge = datetime.now(timezone.utc)
    maintenant = horloge.isoformat(timespec="seconds")
    debut = debut_a_rattraper(conn, complet)
    fin = borne_de_fin(horloge)
    print(f"Publications du {debut} au {fin:%Y-%m-%d %H:%M} UTC "
          f"(fenetres de {PAS} jours)")

    total_lignes, total_arrets, echecs = 0, 0, []
    detailles, sans_puissance = 0, 0
    curseur = debut
    while curseur < fin.date():
        stop = min(fin, datetime.combine(curseur + timedelta(days=PAS),
                                         datetime.min.time(), tzinfo=timezone.utc))
        try:
            evts = rte.arrets(curseur, stop, date_type="PUBLICATION_DATE",
                              derniere_version=False)
        except rte.ErreurRTE as e:
            # Un refus sur une fenetre ne doit pas emporter les suivantes : on le note
            # et on continue, le rattrapage du lendemain repassera dessus.
            echecs.append((curseur, e.code, e.corps[:120]))
            curseur = stop.date()
            continue
        lignes = []
        for e in evts:
            lignes += rte.paliers(e, maintenant)
        if lignes:
            db.upsert_unavailabilities(conn, lignes)
        total_arrets += len(evts)
        total_lignes += len(lignes)
        # Compte ce qui vient vraiment des paliers publies par RTE, et ce qui vient du
        # repli sur la fenetre entiere. Le repli prend la puissance INSTALLEE : un arret
        # partiel y compte pour toute la tranche, et la puissance perdue est surestimee.
        # Sans ce chiffre on ne saurait pas si la colonne mesure des arrets ou des
        # approximations -- et une colonne dont on ignore ce qu'elle contient ne vaut
        # rien, meme si le backtest l'aime bien.
        detailles += sum(1 for e in evts if e.get("values"))
        sans_puissance += sum(1 for l in lignes if l["unavailable_mw"] is None)
        print(f"  {curseur} -> {stop:%Y-%m-%d} : {len(evts)} arrets, {len(lignes)} paliers")
        curseur = stop.date()
        time.sleep(1)

    for quand, code, corps in echecs:
        print(f"::warning::Fenetre RTE en echec {quand} — HTTP {code} — {corps}")

    row = conn.execute("""SELECT COUNT(*) n, COUNT(DISTINCT identifier) a,
                                 MIN(publication_date) p0, MAX(publication_date) p1
                          FROM unavailabilities""").fetchone()
    nuc = conn.execute("""SELECT COUNT(DISTINCT identifier) n FROM unavailabilities
                          WHERE fuel_type = 'NUCLEAR'""").fetchone()["n"]
    part = detailles / total_arrets if total_arrets else 0.0
    print(f"\nRecu : {total_arrets} arrets, {total_lignes} paliers")
    print(f"  dont {detailles} ({part:.0%}) avec leurs paliers de puissance publies ; "
          f"les autres sont replies sur la puissance installee")
    print(f"  paliers sans puissance exploitable : {sans_puissance}")
    print(f"Base : {row['n']} paliers, {row['a']} arrets distincts "
          f"(dont {nuc} nucleaires), publies du {row['p0']} au {row['p1']}")
    if not row["n"]:
        # Rien du tout en base : la source est cassee, il faut le savoir tout de suite.
        raise SystemExit("Aucune indisponibilite collectee — la source est hors service")
    if echecs:
        # Une fenetre en echec ne doit pas emporter la prevision du jour : la source est
        # facultative, et le trou se rebouche tout seul. Le rattrapage repart de la
        # derniere publication EN BASE, donc il repassera sur la fenetre manquee demain
        # sans que personne ait a y penser.
        print(f"{len(echecs)} fenetre(s) en echec — le prochain passage les reprendra")


if __name__ == "__main__":
    main()
