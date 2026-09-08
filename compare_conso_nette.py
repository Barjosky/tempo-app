"""A/B : le modele gagne-t-il a connaitre l'eolien et le solaire ?

Evalue sur les seules saisons ou les PREVISIONS eolien/solaire sont reellement
archivees (a partir du 19/02/2024), sinon la comparaison serait faussee par une
connaissance parfaite du vent que le modele n'aurait jamais en production.
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from sklearn.metrics import f1_score, log_loss

sys.path.insert(0, str(Path(__file__).parent))
import config
from src import db, features, model
from src.sources import calendrier

SEASONS = ["2024-2025", "2025-2026"]


def metrics(y, probs, threshold):
    y = np.array(y)
    preds = model.decide(probs, threshold)
    actual, flagged = y == config.ROUGE, preds == config.ROUGE
    hits = (flagged & actual).sum()
    return {
        "accuracy": float((preds == y).mean()),
        "logloss": float(log_loss(y, probs, labels=model.CLASSES)),
        "f1_macro": float(f1_score(y, preds, average="macro", labels=model.CLASSES,
                                   zero_division=0)),
        "rappel_rouge": float(hits / actual.sum()) if actual.sum() else None,
        "precision_rouge": float(hits / flagged.sum()) if flagged.sum() else None,
        "rouges": int(actual.sum() / config.MAX_HORIZON),
        "detectes": float(hits / config.MAX_HORIZON),
        "fausses": float((flagged & ~actual).sum() / config.MAX_HORIZON),
    }


def main():
    conn = db.connect()
    store = features.FeatureStore(conn)
    out = {}
    for use in (False, True):
        label = "avec conso nette" if use else "sans (reference)"
        rows = []
        for season in SEASONS:
            train_end = calendrier.season_start(season) - timedelta(days=1)
            store.fit_demand_model(calendrier.season_start(season))
            Xtr, ytr, mtr = features.build_dataset(
                store, date(config.FIRST_SEASON, 9, 1), train_end, use_renewables=use)
            Xte, yte, mte = features.build_dataset(
                store, calendrier.season_start(season), calendrier.season_end(season),
                use_renewables=use)
            gbm = model.TempoModel().fit(Xtr, ytr, mtr)
            probs = gbm.predict_proba(Xte, mte)
            rows.append((yte, probs, mte, season))
            print(f"  {label} — {season} rejouee", flush=True)
        y = np.concatenate([r[0] for r in rows])
        p = np.vstack([r[1] for r in rows])
        out[label] = metrics(y, p, config.ROUGE_ALERT_THRESHOLD)
        out[label]["par_saison"] = {
            r[3]: metrics(r[0], r[1], config.ROUGE_ALERT_THRESHOLD) for r in rows}
        h = np.concatenate([[m["horizon"] for m in r[2]] for r in rows])
        out[label]["par_horizon"] = {
            int(hh): metrics(y[h == hh], p[h == hh], config.ROUGE_ALERT_THRESHOLD)
            for hh in sorted(set(h.tolist()))}

    print("\n| Variante | Exactitude | Log-loss | F1 | Rappel Rouge | Précision Rouge |")
    print("|---|---|---|---|---|---|")
    for label, m in out.items():
        print(f"| {label} | {m['accuracy']:.1%} | {m['logloss']:.3f} | {m['f1_macro']:.3f} | "
              f"{m['rappel_rouge']:.0%} | {m['precision_rouge']:.0%} |")

    print("\nPar échéance (rappel Rouge) :")
    print("| Éch. | sans | avec |")
    print("|---|---|---|")
    a, b = out["sans (reference)"], out["avec conso nette"]
    for hh in sorted(a["par_horizon"]):
        print(f"| J+{hh} | {a['par_horizon'][hh]['rappel_rouge']:.0%} | "
              f"{b['par_horizon'][hh]['rappel_rouge']:.0%} |")

    (config.REPORTS_DIR / "comparaison_conso_nette.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
