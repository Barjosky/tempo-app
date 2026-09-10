"""Pourquoi certains hivers sont-ils beaucoup plus durs a predire ?

    python diagnostic_saison.py

Point de depart : sur les jours ou le Rouge est possible, le modele fait 66,8 % de
reussite en 2024-2025 et 51,4 % en 2025-2026. Une moyenne calculee la-dessus ne veut
rien dire ; il faut savoir CE QUI change d'un hiver a l'autre.

Premiere piste, lue dans les predictions exportees : en 2025-2026, treize des
vingt-deux Rouge sont tombes en MARS, le dernier le 31 mars -- dernier jour possible
de la fenetre. En 2024-2025, tout etait consomme le 3 fevrier. Ce n'est pas un
phenomene meteo, c'est une date limite : EDF doit placer son quota avant la fermeture
de la fenetre, et le fait quel que soit le temps.

Ce script mesure ce regime sur toutes les saisons disponibles : quand les Rouge
tombent, a quel rythme le quota se consomme, et surtout combien de JOURS DE MARGE il
reste a chaque instant -- le nombre de jours eligibles restants moins le nombre de
Rouge encore a placer. Quand cette marge tombe a zero, tous les jours restants sont
Rouge par arithmetique, sans que la meteo ait son mot a dire.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config
from src import db, features, rules
from src.sources import calendrier


def saisons_completes(store):
    for s in sorted({info["season"] for info in store.days.values()}):
        jours = [d for d, i in store.days.items()
                 if i["season"] == s and i["color"] is not None]
        if len(jours) >= 360:
            yield s, sorted(jours)


def profil(store, saison, jours):
    couleurs = {d: store.days[d]["color"] for d in jours}
    rouges = sorted(d for d in jours if couleurs[d] == config.ROUGE)
    if not rouges:
        return None

    par_mois = {}
    for d in rouges:
        par_mois[f"{d:%b}"] = par_mois.get(f"{d:%b}", 0) + 1

    # Marge minimale atteinte dans la saison : jours eligibles restants moins Rouge
    # restants. A zero, tous les jours restants sont Rouge par arithmetique.
    marge_min, date_marge_min, restants_alors = 10**6, None, 0
    forces = 0
    for d in calendrier.daterange(calendrier.season_start(saison),
                                  date(int(saison.split("-")[1]), 3, 31)):
        if not rules.rouge_possible(d):
            continue
        deja = sum(1 for r in rouges if r < d)
        reste_quota = config.QUOTA_ROUGE - deja
        reste_jours = rules.remaining_rouge_days(d)
        marge = reste_jours - reste_quota
        if marge < marge_min:
            marge_min, date_marge_min, restants_alors = marge, d, reste_quota
        if marge <= 0:
            forces += 1
    return {
        "n": len(rouges), "premier": rouges[0], "dernier": rouges[-1],
        "par_mois": par_mois, "marge_min": marge_min,
        "date_marge_min": date_marge_min, "restants_alors": restants_alors,
        "forces": forces,
        "part_mars": sum(1 for d in rouges if d.month == 3) / len(rouges),
    }


def main():
    store = features.FeatureStore(db.connect())
    profils = {}
    for saison, jours in saisons_completes(store):
        p = profil(store, saison, jours)
        if p:
            profils[saison] = p
    if not profils:
        raise SystemExit("aucune saison complete : la base est-elle constituee ?")

    print("Quand les jours Rouge tombent, saison par saison :\n")
    print(f"{'saison':>12} {'n':>3} {'premier':>11} {'dernier':>11} "
          f"{'% en mars':>10}   repartition")
    for s, p in profils.items():
        mois = " ".join(f"{m}:{n}" for m, n in p["par_mois"].items())
        print(f"{s:>12} {p['n']:>3} {p['premier']:%d/%m/%y:>11} "
              f"{p['dernier']:%d/%m/%y:>11} {p['part_mars']:>9.0%}   {mois}")

    print("\nMarge de placement : jours eligibles restants moins Rouge restants.")
    print("A zero, tous les jours restants sont Rouge -- la meteo n'y peut plus rien.\n")
    print(f"{'saison':>12} {'marge min':>10} {'atteinte le':>12} "
          f"{'quota restant':>14} {'jours forces':>13}")
    for s, p in profils.items():
        print(f"{s:>12} {p['marge_min']:>10} {p['date_marge_min']:%d/%m/%y:>12} "
              f"{p['restants_alors']:>14} {p['forces']:>13}")

    serres = [s for s, p in profils.items() if p["marge_min"] <= 5]
    print(f"\n{len(serres)} saison(s) sur {len(profils)} finissent avec 5 jours de marge "
          f"ou moins : {', '.join(serres) if serres else 'aucune'}")
    print("Sur ces saisons-la, la fin d'hiver est arithmetique et non meteorologique.")
    print("Un modele entraine surtout sur des hivers confortables ne peut pas la")
    print("deviner : il n'a jamais vu de marge aussi serree dans ses donnees.")


if __name__ == "__main__":
    main()
