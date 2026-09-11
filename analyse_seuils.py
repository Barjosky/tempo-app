"""Choix du seuil d'alerte Rouge : balayage complet sur 5 saisons.

    python analyse_seuils.py            # relit les probabilites deja calculees
    python analyse_seuils.py --refit    # les recalcule (~10 min) puis balaye

Les probabilites sont mises en cache dans reports/probs_par_saison.npz, ce qui permet
de reevaluer n'importe quel seuil instantanement sans reentrainer le modele.
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import config
from src import backtest, db, features, model
from src.sources import calendrier

SEASONS = ["2021-2022", "2022-2023", "2023-2024", "2024-2025", "2025-2026"]


def signature():
    """Ce qui rend un cache de probabilites caduc.

    Un seuil se choisit sur des probabilites ; si le modele qui les a produites n'est
    plus celui qui tourne, le seuil retenu ne veut rien dire -- et rien ne le
    signalerait. Meme lecon que le format du modele : un controle qui repose sur le
    fait de penser a `--refit` n'est pas un controle.
    """
    return json.dumps({
        "features": len(features.FEATURE_NAMES),
        "exclues": sorted(config.EXCLUDED_FEATURES),
        "force_quota": config.FORCE_QUOTA_ROUGE,
        "graines": config.N_SEEDS,
        "deux_etages": config.TWO_STAGE,
        "saisons": SEASONS,
    }, sort_keys=True)


def cache_perime(path):
    """(perime ?, raison lisible)."""
    if not path.exists():
        return True, "aucun cache"
    data = np.load(path, allow_pickle=False)
    if "signature" not in data.files:
        return True, "cache d'une version anterieure a ce controle"
    ancienne = str(data["signature"])
    if ancienne != signature():
        return True, "la configuration du modele a change depuis le cache"
    return False, ""


def compute_probs(path):
    """Rejoue les 5 saisons (entrainement sur le passe uniquement) et met en cache."""
    conn = db.connect()
    store = features.FeatureStore(conn)
    dump = {}
    for season in SEASONS:
        train_end = calendrier.season_start(season) - timedelta(days=1)
        # Sans ce calage, demand_coef reste None et toutes les colonnes de charge
        # residuelle sortent en NaN : le balayage se faisait sur un modele ampute.
        # Le cutoff est la meme borne que dans le backtest, pour rester point-in-time.
        store.fit_demand_model(calendrier.season_start(season))
        Xtr, ytr, mtr = features.build_dataset(store, date(config.FIRST_SEASON, 9, 1), train_end)
        Xte, yte, mte = features.build_dataset(
            store, calendrier.season_start(season), calendrier.season_end(season))
        gbm = model.TempoModel().fit(Xtr, ytr, mtr)
        dump[f"{season}_probs"] = gbm.predict_proba(Xte, mte)
        dump[f"{season}_y"] = np.array(yte)
        dump[f"{season}_horizon"] = np.array([m["horizon"] for m in mte])
        # Le perimetre de notation voyage avec les probabilites : un seuil doit se
        # juger sur les jours ou le Rouge est possible, comme tout le reste du projet.
        # Ailleurs le masque contractuel met deja p_rouge a zero, et ces lignes ne
        # font que gonfler le denominateur d'une precision flatteuse.
        dump[f"{season}_eval"] = backtest.evaluables(mte)
        print(f"  {season} rejouee ({len(Xte)} predictions)", flush=True)
    dump["signature"] = np.array(signature())
    np.savez(path, **dump)


def _eval_mask(data, season):
    """Jours ou le Rouge est possible. Absent des caches d'avant : tout est garde."""
    cle = f"{season}_eval"
    return data[cle] if cle in data.files else slice(None)


def per_season(data, threshold):
    out = {}
    for season in SEASONS:
        ev = _eval_mask(data, season)
        y = data[f"{season}_y"][ev]
        p = data[f"{season}_probs"][ev][:, 2]
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
    path = config.reports_path("probs_par_saison.npz")
    perime, raison = cache_perime(path)
    if "--refit" in sys.argv or perime:
        if perime and "--refit" not in sys.argv:
            print(f"Recalcul force : {raison}.")
        print("Rejeu des 5 saisons :")
        compute_probs(path)
    data = np.load(path)
    perimetre = ("jours eligibles (lun-ven, nov-mars, hors feries)"
                 if f"{SEASONS[0]}_eval" in data.files else "TOUTES les predictions")
    print(f"\nSeuil actuellement retenu : {config.ROUGE_ALERT_THRESHOLD:.2f}")
    print(f"Mesure sur : {perimetre}\n")

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

    # Le Blanc s'arbitre separement : le manquer coute peu (heure pleine +16 % contre
    # +341 % pour un Rouge), mais l'argmax seul ne le sort jamais. On regarde donc ce
    # que chaque seuil rattrape, et ce qu'il abime au passage sur les jours Bleu.
    print(f"\n=== Seuil Blanc (configure : {config.BLANC_ALERT_THRESHOLD:.2f}) ===")
    print(f"{'seuil':>6} {'rappel Blanc':>13} {'precision Blanc':>16} {'Bleu abimes':>12}")
    for t in np.arange(0.20, 0.85, 0.05):
        rec, pre, abimes = [], [], []
        for season in SEASONS:
            ev = _eval_mask(data, season)
            y = data[f"{season}_y"][ev]
            p = data[f"{season}_probs"][ev]
            flag = p[:, 1] >= t
            actual = y == config.BLANC
            hits = (flag & actual).sum()
            rec.append(hits / actual.sum() if actual.sum() else np.nan)
            pre.append(hits / flag.sum() if flag.sum() else np.nan)
            abimes.append((flag & (y == config.BLEU)).sum() / config.MAX_HORIZON)
        print(f"{t:>6.2f} {np.nanmean(rec):>12.0%} {np.nanmean(pre):>15.0%} "
              f"{np.mean(abimes):>12.1f}")

    eligibles = [r for r in rows if r[4] >= 0.40]
    if eligibles:
        best = max(eligibles, key=lambda r: r[1])
        print(f"\n(Repere : le seuil {best[0]:.2f} maximiserait le rappel moyen parmi ceux "
              f"dont la pire saison garde >=40% de precision.)")


if __name__ == "__main__":
    main()
