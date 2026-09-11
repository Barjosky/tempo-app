"""L'API RTE des indisponibilites est-elle exploitable pour ce projet ?

    python sonde_rte.py

A verifier AVANT de construire dessus : si l'API ne sert que les arrets recents, la
colonne serait vide sur toutes les saisons d'entrainement et le backtest ne mesurerait
rien. Autant le savoir en trois minutes plutot qu'apres deux cents lignes.

La premiere version de cette sonde envoyait tous les parametres d'un coup, recevait un
400, et concluait « API inexploitable ». Deux fautes. Un 400 dit « ta requete est mal
formee », pas « il n'y a pas de donnees » -- et elle jetait le corps de la reponse, ou
RTE explique precisement ce qu'il refuse. Elle monte donc maintenant une ECHELLE : la
requete la plus nue d'abord, puis un parametre a la fois, en affichant chaque refus
avec son explication. Le premier barreau qui casse designe le coupable.
"""
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from src.sources import rte

FMT = "%Y-%m-%dT%H:%M:%S%z"


def appel(titre, **params):
    """Un barreau de l'echelle. Rend la liste, ou None si l'API a refuse."""
    montre = {k: v for k, v in params.items() if k != "_suite"}
    print(f"\n  {titre}")
    print(f"    {montre}")
    try:
        payload, _ = rte._get("/generation_unavailabilities", params)
        n = len((payload or {}).get("generation_unavailabilities", []))
        print(f"    OK — {n} arrets")
        return (payload or {}).get("generation_unavailabilities", [])
    except rte.ErreurRTE as e:
        print(f"    REFUS {e.code} — {e.corps}")
        return None
    except Exception as e:
        print(f"    ECHEC {type(e).__name__} — {e}")
        return None


def iso(d, h=0):
    """Le seul format que RTE accepte : suffixe Z, pas de decalage explicite.

    Mesure par l'echelle de cette sonde : « +02:00 » est refuse (UNADINFO_GENUN_F03),
    « Z » passe, la date nue est refusee aussi.
    """
    return f"{d:%Y-%m-%d}T{h:02d}:00:00Z"


def main():
    if not rte.disponible():
        raise SystemExit("Aucun identifiant RTE. Attendu : RTE_BASIC_AUTH.")
    print(f"Jeton : {rte.TOKEN_URL}")
    try:
        rte.jeton()
        print("1. Authentification : OK")
    except Exception as e:
        raise SystemExit(f"1. Authentification : ECHEC — {e}")

    # 2. L'echelle : on part du plus nu et on ajoute un parametre a la fois.
    print("\n2. Quel parametre l'API refuse-t-elle ? (fenetre recente, 7 jours)")
    a, b = date.today(), date.today() + timedelta(days=7)
    appel("sans aucun parametre")
    base = {"start_date": iso(a), "end_date": iso(b)}
    ok = appel("dates seules", **base)
    for extra in ({"date_type": "EVENT_DATE"}, {"last_version": "true"},
                  {"fuel_type": "NUCLEAR"}, {"event_status": "ACTIVE"}):
        appel(f"dates + {list(extra)[0]}", **base, **extra)

    if ok is None:
        print("\n  Meme les dates seules sont refusees : le format de date est en cause.")
        for var, lib in (("%Y-%m-%dT%H:%M:%S+02:00", "decalage explicite"),
                         ("%Y-%m-%d", "date nue")):
            appel(f"format : {lib}",
                  start_date=a.strftime(var), end_date=b.strftime(var))
        return

    # 3. Profondeur d'historique, du plus ancien au plus recent.
    print("\n3. Jusqu'ou remonte l'historique ? (une semaine de janvier par annee)")
    plus_ancienne, volumes = None, {}
    for annee in range(2020, 2027):
        d = date(annee, 1, 10)
        r = appel(f"janvier {annee}", start_date=iso(d), end_date=iso(d + timedelta(days=7)))
        if r:
            volumes[annee] = len(r)
            plus_ancienne = plus_ancienne or annee
    if plus_ancienne:
        n = 2026 - plus_ancienne
        print(f"\n  Plus ancienne annee servie : {plus_ancienne} — {n} hiver(s)")
        print("  " + ("SUFFISANT pour un backtest." if n >= 4 else
                      "INSUFFISANT : le backtest ne pourrait pas mesurer l'apport."))
    else:
        print("\n  Aucune annee passee ne repond : API limitee au futur proche.")

    # 4. Le point qui decide de l'honnetete du backtest.
    ech = appel("echantillon 30 jours", start_date=iso(a - timedelta(days=15)),
                end_date=iso(b + timedelta(days=8)))
    if not ech:
        return
    print(f"\n4. Contenu : {len(ech)} arrets")
    print("   filieres :", dict(Counter(x.get("fuel_type") for x in ech).most_common(6)))
    print("   types    :", dict(Counter(x.get("unavailability_type") for x in ech)))
    avec_pub = sum(1 for x in ech if x.get("publication_date"))
    print(f"   publication_date rempli : {avec_pub}/{len(ech)}")
    if avec_pub < len(ech):
        print("   ATTENTION : sans cette date, impossible de savoir ce qui etait connu")
        print("   a une date passee — le backtest surestimerait le modele.")

    x = next((y for y in ech if y.get("fuel_type") == "NUCLEAR"), ech[0])
    print("\n   Exemple :")
    for k in ("affected_asset_or_unit_name", "fuel_type", "unavailability_type",
              "event_status", "publication_date", "start_date", "end_date",
              "affected_asset_or_unit_installed_capacity"):
        print(f"     {k:<42} {x.get(k)}")
    vals = x.get("values") or []
    print(f"     values : {len(vals)} paliers, ex. {vals[0] if vals else '—'}")


if __name__ == "__main__":
    main()
