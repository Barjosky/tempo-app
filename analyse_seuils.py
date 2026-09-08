"""Choix du seuil d'alerte Rouge : balayage complet sur 5 saisons.

    python analyse_seuils.py            # relit les probabilites deja calculees
    python analyse_seuils.py --refit    # les recalcule (~10 min) puis balaye

Les probabilites sont mises en cache dans reports/probs_par_saison.npz, ce qui permet
de reevaluer n'importe quel seuil instantanement sans reentrainer le modele.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import config
from src import db, features, model
from src.sources import calendrier

SEASONS = ["2021-2022", "2022-2023", "2023-2024", "2024-2025", "2025-2026"]


def compute_probs(path):
    """Rejoue les 5 saisons (entrainement sur le passe uniquement) et met en cache."""
    conn = db.connect()
    store = features.FeatureStore(conn)
    dump = {}
    for season in SEASONS:
        train_end = calendrier.season_start(season) - timedelta(days=1)
        Xtr, ytr, mtr = features.build_dataset(store, date(config.FIRST_SEASON, 9, 1), train_end)
        Xte, yte, mte = features.build_dataset(
            store, calendrier.season_start(season), calendrier.season_end(season))
        gbm = model.TempoModel().fit(Xtr, ytr, mtr)
        dump[f"{season}_probs"] = gbm.predict_proba(Xte, mte)
        dump[f"{season}_y"] = np.array(yte)
        dump[f"{season}_horizon"] = np.array([m["horizon"] for m in mte])
        print(f"  {season} rejouee ({len(Xte)} predictions)", flush=True)
    np.savez(path, **dump)


def per_season(data, threshold):
    out = {}
    for season in SEASONS:
        y = data[f"{season}_y"]
        p = data[f"{season}_probs"][:, 2]
        actual = y == config.ROUGE
        flagged = p >= threshold
        hits = (flagged & actual).sum()
        n_h = config.MAX_HORIZON
        out[season] = {
            "rappel": hits / actual.sum() if actual.sum() else float("nan"),
            "precision": hits / flagged.sum() if flagged.sum() else float("nan"),
            "detectes": hits / n_h, "total": actual.sum() / n_h,
            "fausses": (flagged & ~actual).sum() / n_h,
        }
    return out


def main():
    path = config.REPORTS_DIR / "probs_par_saison.npz"
    if "--refit" in sys.argv or not path.exists():
        print("Rejeu des 5 saisons :")
        compute_probs(path)
    data = np.load(path)
    print(f"\nSeuil actuellement retenu : {config.ROUGE_ALERT_THRESHOLD:.2f}\n")

    print(f"{'seuil':>6} {'rappel moy':>11} {'prec moy':>9} {'ecart-type':>11} "
          f"{'pire prec':>10} {'pire rappel':>12} {'fausses/ech':>12}")
    rows = []
    for t in np.arange(0.05, 0.95, 0.05):
        res = per_season(data, t)
        rec = [r["rappel"] for r in res.values()]
        pre = [r["precision"] for r in res.values() if r["precision"] == r["precision"]]
        fa = [r["fausses"] for r in res.values()]
        rows.append((t, np.mean(rec), np.mean(pre), np.std(pre), min(pre), min(rec), np.mean(fa)))
        print(f"{t:>6.2f} {np.mean(rec):>10.0%} {np.mean(pre):>9.0%} {np.std(pre):>11.0%} "
              f"{min(pre):>10.0%} {min(rec):>12.0%} {np.mean(fa):>12.1f}")

    # Le seuil est un arbitrage assume, pas une optimisation : on detaille celui qui
    # est configure. La suggestion automatique n'est donnee qu'a titre de repere.
    seuil = config.ROUGE_ALERT_THRESHOLD
    detail = per_season(data, seuil)
    print(f"\n=== Seuil configure : {seuil:.2f} ===")
    print("| Saison | Détectés | Fausses alertes | Rappel | Précision |")
    print("|---|---|---|---|---|")
    for season, r in detail.items():
        print(f"| {season} | {r['detectes']:.1f}/{r['total']:.0f} | {r['fausses']:.1f} | "
              f"{r['rappel']:.0%} | {r['precision']:.0%} |")
    rappels = [r["rappel"] for r in detail.values()]
    precisions = [r["precision"] for r in detail.values()]
    detectes = sum(r["detectes"] for r in detail.values())
    total = sum(r["total"] for r in detail.values())
    print(f"| **Total** | **{detectes:.1f}/{total:.0f}** | "
          f"{np.mean([r['fausses'] for r in detail.values()]):.1f} | "
          f"**{np.mean(rappels):.0%}** | **{np.mean(precisions):.0%}** |")

    eligibles = [r for r in rows if r[4] >= 0.40]
    if eligibles:
        best = max(eligibles, key=lambda r: r[1])
        print(f"\n(Repere : le seuil {best[0]:.2f} maximiserait le rappel moyen parmi ceux "
              f"dont la pire saison garde >=40% de precision.)")


if __name__ == "__main__":
    main()
