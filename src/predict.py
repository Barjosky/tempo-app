"""Entrainement du modele de production et generation des predictions J+1..J+10."""
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import joblib
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
from src import db, features, model
from src.sources import calendrier

MODEL_PATH = config.DATA_DIR / "model.joblib"


def train(conn, store=None, class_weight=None, rouge_threshold=None):
    """Entraine sur tout l'historique etiquete disponible."""
    store = store or features.FeatureStore(conn)
    store.fit_demand_model(date.today() + timedelta(days=1))
    end = date.today() - timedelta(days=1)
    X, y, meta = features.build_dataset(store, date(config.FIRST_SEASON, 9, 1), end)
    gbm = model.TempoModel(class_weight=class_weight,
                           rouge_threshold=rouge_threshold).fit(X, y, meta)
    version = f"v1-{date.today():%Y%m%d}-n{len(X)}"
    joblib.dump({"model": gbm, "version": version, "features": features.FEATURE_NAMES}, MODEL_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO model_runs (version, trained_at, metrics_json) VALUES (?,?,?)",
        (version, datetime.now().isoformat(timespec="seconds"),
         json.dumps({"n_train": len(X), "rouge_threshold": gbm.rouge_threshold,
                     "class_weight": class_weight})),
    )
    conn.commit()
    return gbm, version


class ModeleObsolete(RuntimeError):
    """Le modele enregistre n'a pas ete entraine sur les features actuelles."""


def load(conn=None):
    if not MODEL_PATH.exists():
        raise FileNotFoundError("Modele absent : lancer `python collector.py --train`")
    bundle = joblib.load(MODEL_PATH)
    # Un modele entraine avant l'ajout d'une feature attend un nombre de colonnes qui
    # n'existe plus. Sans ce controle il predirait sur des colonnes decalees, en
    # silence : mieux vaut refuser de servir que servir n'importe quoi.
    connues = bundle.get("features")
    if connues != features.FEATURE_NAMES:
        manquantes = set(features.FEATURE_NAMES) - set(connues or [])
        raise ModeleObsolete(
            f"modele entraine sur {len(connues or [])} features, le code en produit "
            f"{len(features.FEATURE_NAMES)}"
            + (f" (nouvelles : {', '.join(sorted(manquantes))})" if manquantes else "")
            + " -- relancer `python collector.py --train`")
    return bundle["model"], bundle["version"]


def besoin_d_entrainement():
    """Vrai si aucun modele utilisable n'est disponible en l'etat."""
    try:
        load()
        return False
    except (FileNotFoundError, ModeleObsolete, KeyError):
        return True


def predict_next_days(conn, run_date=None, days=config.MAX_HORIZON, store=None):
    """Predictions figees pour J+1..J+N, en n'utilisant que ce qui est connu aujourd'hui."""
    run_date = run_date or date.today()
    store = store or features.FeatureStore(conn)
    if store.demand_coef is None:
        store.fit_demand_model(run_date + timedelta(days=1))
    gbm, version = load(conn)
    state = store.season_state(run_date)

    rows, X, meta = [], [], []
    for h in range(1, days + 1):
        target = run_date + timedelta(days=h)
        row = features.build_row(store, run_date, target, state, rng=np.random.default_rng(0))
        if row is None:
            continue
        X.append(row)
        meta.append({"target": target, "horizon": h,
                     "is_holiday": calendrier.is_holiday(target),
                     "rouge_left": state["rouge_left"], "blanc_left": state["blanc_left"]})
    if not X:
        return []

    probs = gbm.predict_proba(np.array(X), meta)
    colors = model.decide(probs, gbm.rouge_threshold, gbm.blanc_threshold)
    now = datetime.now().isoformat(timespec="seconds")

    for i, m in enumerate(meta):
        official = store.days.get(m["target"], {}).get("color")
        rows.append({
            "run_datetime": now, "run_date": run_date.isoformat(),
            "target_date": m["target"].isoformat(), "horizon": m["horizon"],
            "p_bleu": float(probs[i][0]), "p_blanc": float(probs[i][1]),
            "p_rouge": float(probs[i][2]),
            "predicted_color": int(official or colors[i]),
            "is_official": int(bool(official)),
            "model_version": version,
        })
    db.insert_predictions(conn, rows)
    return rows
