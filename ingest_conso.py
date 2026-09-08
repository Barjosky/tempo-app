"""Collecte la consommation et la production nationales reelles (eCO2mix)."""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config
from src import db
from src.sources import eco2mix


def main():
    conn = db.connect()
    db.init_db(conn)
    today = date.today()
    start = date(config.FIRST_SEASON, 9, 1)

    print("Historique consolide :")
    series = eco2mix.fetch_history(start, today)
    db.upsert_conso(conn, eco2mix.to_rows(series, "eco2mix-def"))
    print(f"  {len(series)} jours")

    print("Temps reel (jours recents + prevision RTE de demain) :")
    recent = eco2mix.fetch_recent(today - timedelta(days=75), today + timedelta(days=1))
    db.upsert_conso(conn, eco2mix.to_rows(recent, "eco2mix-tr"))
    print(f"  {len(recent)} jours")

    row = conn.execute("""SELECT COUNT(*) n, MIN(date) a, MAX(date) b,
                                 COUNT(prevision_j1_peak_mw) p FROM conso""").fetchone()
    print(f"\nBase : {row['n']} jours de {row['a']} a {row['b']}, "
          f"dont {row['p']} avec la prevision RTE")


if __name__ == "__main__":
    main()
