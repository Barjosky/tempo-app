"""Alimente l'onglet Historique avec les predictions du backtest walk-forward.

Ces lignes sont marquees `backtest-v1` : ce sont des predictions rejouees sur le
passe (modele entraine uniquement sur les saisons anterieures), pas des
predictions emises en temps reel. L'interface les distingue explicitement.
"""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config
from src import backtest, db

VERSION = "backtest-v1"


def main():
    conn = db.connect()
    db.init_db(conn)
    print("Rejeu walk-forward des 4 dernieres saisons...")
    results = backtest.run(conn)
    now = datetime.now().isoformat(timespec="seconds")
    rows = []
    for r in results["rows"]:
        target = date.fromisoformat(r["target"])
        rows.append({
            "run_datetime": now,
            "run_date": (target - timedelta(days=r["horizon"])).isoformat(),
            "target_date": r["target"], "horizon": r["horizon"],
            "p_bleu": r["p"][0], "p_blanc": r["p"][1], "p_rouge": r["p"][2],
            "predicted_color": r["pred"], "is_official": 0, "model_version": VERSION,
        })
    conn.execute("DELETE FROM predictions WHERE model_version = ?", (VERSION,))
    db.insert_predictions(conn, rows)
    ok = sum(1 for r in results["rows"] if r["pred"] == r["y"])
    print(f"{len(rows)} predictions rejouees inserees ({ok / len(rows):.1%} correctes)")


if __name__ == "__main__":
    main()
