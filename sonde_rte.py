"""L'API RTE des indisponibilites est-elle exploitable pour ce projet ?

    python sonde_rte.py

A verifier AVANT de construire quoi que ce soit dessus, parce qu'une seule reponse
peut tout invalider : si l'API ne sert que les arrets recents, la colonne serait vide
sur toutes les saisons d'entrainement et le backtest ne pourrait rien mesurer. Autant
le savoir en trois minutes plutot qu'apres deux cents lignes de collecte.

Quatre questions, dans l'ordre ou elles peuvent faire echouer le projet :

  1. Les identifiants passent-ils, et l'URL du jeton est-elle la bonne ? (elle ne
     figure pas dans le Swagger de RTE, donc elle est supposee ici)
  2. Jusqu'ou remonte l'historique ? Il faut au moins cinq hivers.
  3. Les arrets NUCLEAIRE sont-ils servis, et en quel volume ?
  4. `publication_date` est-il rempli ? Sans lui, impossible de reconstituer ce qu'on
     savait a une date passee, et le backtest se mentirait.
"""
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from src.sources import rte


def essaie(libelle, fn):
    print(f"\n--- {libelle}")
    try:
        return fn()
    except Exception as e:
        print(f"  ECHEC : {type(e).__name__} — {e}")
        return None


def main():
    if not rte.disponible():
        raise SystemExit(
            "Aucun identifiant RTE dans l'environnement.\n"
            "Attendu : RTE_BASIC_AUTH, ou RTE_CLIENT_ID + RTE_CLIENT_SECRET.")
    print(f"Identifiants presents. Jeton : {rte.TOKEN_URL}")

    if essaie("1. Authentification", lambda: rte.jeton()) is None:
        raise SystemExit(
            "\nL'URL du jeton est probablement fausse : elle ne figure pas dans le\n"
            "Swagger et a ete supposee. Corriger rte.TOKEN_URL avant d'aller plus loin.")
    print("  OK — jeton obtenu")

    # 2. Profondeur d'historique : on sonde de la plus ancienne a la plus recente.
    print("\n--- 2. Profondeur de l'historique (arrets nucleaires, une semaine de janvier)")
    plus_ancienne = None
    for annee in range(2020, 2027):
        debut = date(annee, 1, 10)
        n = essaie(f"  janvier {annee}",
                   lambda d=debut: len(rte.arrets(d, d + timedelta(days=7), fuel="NUCLEAR")))
        if n is not None:
            print(f"    {n} arrets")
            if n and plus_ancienne is None:
                plus_ancienne = annee
    if plus_ancienne is None:
        raise SystemExit("\nAucune donnee sur aucune annee : API inexploitable ici.")
    print(f"\n  Plus ancienne annee servie : {plus_ancienne}")
    saisons = 2026 - plus_ancienne
    print(f"  Soit environ {saisons} hiver(s) exploitables."
          + ("  SUFFISANT." if saisons >= 4 else "  INSUFFISANT pour un backtest honnete."))

    # 3 et 4 : contenu reel d'un echantillon recent.
    ech = essaie("3. Echantillon recent (30 jours)",
                 lambda: rte.arrets(date.today() - timedelta(days=15),
                                    date.today() + timedelta(days=15)))
    if not ech:
        return
    print(f"  {len(ech)} arrets, toutes filieres")
    print("  par filiere :", dict(Counter(a.get("fuel_type") for a in ech).most_common(6)))
    print("  par type    :", dict(Counter(a.get("unavailability_type") for a in ech)))

    avec_pub = sum(1 for a in ech if a.get("publication_date"))
    print(f"\n--- 4. publication_date rempli : {avec_pub}/{len(ech)}")
    if avec_pub < len(ech):
        print("  ATTENTION : sans cette date, impossible de savoir ce qui etait connu")
        print("  a une date passee. Le backtest surestimerait le modele.")

    a = next((x for x in ech if x.get("fuel_type") == "NUCLEAR"), ech[0])
    print("\n--- Exemple")
    for k in ("affected_asset_or_unit_name", "fuel_type", "unavailability_type",
              "event_status", "publication_date", "start_date", "end_date",
              "affected_asset_or_unit_installed_capacity"):
        print(f"  {k:<42} {a.get(k)}")
    vals = a.get("values") or []
    print(f"  values : {len(vals)} paliers, ex. {vals[0] if vals else '—'}")


if __name__ == "__main__":
    main()
