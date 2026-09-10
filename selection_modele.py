"""Quelle configuration de modele est la plus FIABLE ?

    python selection_modele.py            # compare les candidats
    python selection_modele.py --saisons 2024-2025,2025-2026   # plus rapide

Le probleme n'est pas la precision moyenne, c'est l'instabilite : sur les jours ou
le Rouge est possible, le modele fait 69 % de reussite un hiver et 51 % le suivant.
Une moyenne flatteuse batie sur une saison sauvee et une saison ratee ne vaut rien
pour quelqu'un qui doit decider demain matin.

C'est donc la PIRE SAISON qui departage ici, pas la moyenne. Un candidat ne gagne
que s'il releve le plancher.

Trois leviers sont compares, chacun repondant a une mesure :

  - retirer les features d'offre, que la permutation designe comme nuisibles dans
    toutes les saisons ;
  - ponderer l'entrainement vers l'hiver, parce que quatre lignes sur cinq sont des
    journees sans enjeu qui dominent la fonction de cout ;
  - moyenner plusieurs graines, parce que le hasard d'entrainement est precisement
    ce qui fait qu'un hiver passe et que le suivant casse.

Avertissement d'honnetete : quatre saisons evaluables, c'est peu. Comparer beaucoup
de variantes finirait par choisir celle qui colle le mieux a ces quatre-la. D'ou une
liste courte de candidats justifies d'avance, plutot qu'un balayage.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from sklearn.metrics import log_loss

sys.path.insert(0, str(Path(__file__).parent))
import config
from src import backtest, db, features, model
from src.sources import calendrier

NUCLEAIRE = ["nuclear_recent_mw", "nuclear_anomaly_mw", "margin_proxy_mw"]

CANDIDATS = [
    ("actuel (reference)", dict(excluded=[], winter_weight=1.0, n_seeds=1)),
    ("sans nucleaire", dict(excluded=NUCLEAIRE, winter_weight=1.0, n_seeds=1)),
    ("+ poids hiver", dict(excluded=NUCLEAIRE, winter_weight=5.0, n_seeds=1)),
    ("ensemble seul", dict(excluded=[], winter_weight=1.0, n_seeds=5)),
    ("les trois", dict(excluded=NUCLEAIRE, winter_weight=5.0, n_seeds=5)),
]


def evalue(gbm, X, y, meta):
    """Mesures sur les seuls jours ou le Rouge est possible."""
    probs = gbm.predict_proba(X, meta)
    preds = model.decide(probs, gbm.rouge_threshold, gbm.blanc_threshold)
    rouge = y == config.ROUGE
    signale = preds == config.ROUGE
    return {
        "logloss": float(log_loss(y, probs, labels=model.CLASSES)),
        "acc": float((preds == y).mean()),
        "rappel_r": float((signale & rouge).sum() / rouge.sum()) if rouge.sum() else 0.0,
        "prec_r": float((signale & rouge).sum() / signale.sum()) if signale.sum() else 0.0,
        # Ecart moyen entre la probabilite annoncee et la frequence observee, par
        # tranche de 10 points : c'est la mesure directe de « peut-on y croire ».
        "calibration": ecart_calibration(probs[:, 2], rouge),
    }


def ecart_calibration(p, reel, bins=10):
    ecarts, poids = [], []
    for i in range(bins):
        sel = (p >= i / bins) & (p < (i + 1) / bins if i < bins - 1 else p <= 1.0)
        if sel.sum() < 20:
            continue
        ecarts.append(abs(p[sel].mean() - reel[sel].mean()))
        poids.append(sel.sum())
    return float(np.average(ecarts, weights=poids)) if ecarts else float("nan")


def jeux(store, saisons):
    """(saison, Xtrain, ytrain, mtrain, Xtest, ytest, mtest) deja restreints."""
    for saison in saisons:
        store.fit_demand_model(calendrier.season_start(saison))
        fin = calendrier.season_start(saison) - timedelta(days=1)
        Xtr, ytr, mtr = features.build_dataset(
            store, date(config.FIRST_SEASON, 9, 1), fin)
        Xte, yte, mte = features.build_dataset(
            store, calendrier.season_start(saison), calendrier.season_end(saison))
        if not len(Xte):
            continue
        ev = backtest.evaluables(mte)
        yield saison, Xtr, ytr, mtr, Xte[ev], yte[ev], [m for m, k in zip(mte, ev) if k]


def main():
    saisons = backtest.HONEST_SEASONS
    if "--saisons" in sys.argv:
        saisons = sys.argv[sys.argv.index("--saisons") + 1].split(",")

    store = features.FeatureStore(db.connect())
    print(f"Saisons evaluees : {', '.join(saisons)}")
    print("Chaque saison est predite par un modele n'ayant appris que sur les "
          "precedentes.\n")

    # Les jeux sont construits une fois et reutilises par tous les candidats : la
    # comparaison porte alors sur la seule configuration, pas sur un alea de tirage.
    prepares = list(jeux(store, saisons))
    resultats = {}

    for nom, reglages in CANDIDATS:
        par_saison = []
        for saison, Xtr, ytr, mtr, Xte, yte, mte in prepares:
            gbm = model.TempoModel(**reglages).fit(Xtr, ytr, mtr)
            par_saison.append((saison, evalue(gbm, Xte, yte, mte)))
            print(f"  {nom:>20} · {saison} : logloss "
                  f"{par_saison[-1][1]['logloss']:.3f}", flush=True)
        resultats[nom] = par_saison

    print(f"\n{'candidat':>20} {'logloss moy':>12} {'PIRE logloss':>13} "
          f"{'precision':>10} {'rappel R':>9} {'ecart calib':>12}")
    for nom, _ in CANDIDATS:
        rs = [r for _, r in resultats[nom]]
        print(f"{nom:>20} {np.mean([r['logloss'] for r in rs]):>12.3f} "
              f"{max(r['logloss'] for r in rs):>13.3f} "
              f"{np.mean([r['acc'] for r in rs]):>9.1%} "
              f"{np.mean([r['rappel_r'] for r in rs]):>8.0%} "
              f"{np.mean([r['calibration'] for r in rs]):>11.1%}")

    ref = max(r["logloss"] for _, r in resultats[CANDIDATS[0][0]])
    print(f"\nDetail par saison (log-loss ; plus bas vaut mieux) :")
    entetes = [s for s, _ in resultats[CANDIDATS[0][0]]]
    print(f"{'candidat':>20} " + " ".join(f"{s:>11}" for s in entetes))
    for nom, _ in CANDIDATS:
        print(f"{nom:>20} " + " ".join(
            f"{r['logloss']:>11.3f}" for _, r in resultats[nom]))

    gagnant = min(CANDIDATS, key=lambda c: max(r["logloss"] for _, r in resultats[c[0]]))
    pire = max(r["logloss"] for _, r in resultats[gagnant[0]])
    print(f"\nMeilleur plancher : « {gagnant[0]} » ({pire:.3f} sur sa pire saison, "
          f"contre {ref:.3f} pour la reference).")
    if pire >= ref:
        print("Aucun candidat ne releve le plancher : garder la reference.")


if __name__ == "__main__":
    main()
