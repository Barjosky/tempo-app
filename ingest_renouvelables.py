"""Collecte les indices eolien/solaire (observes et tels que prevus a l'epoque).

A lancer une fois pour completer une base deja constituee par ingest.py.
Requetes lourdes (14 series horaires par appel) : on decoupe par semestre.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config
from src import db
from src.sources import meteo

PREVIOUS_RUNS_START = date(2022, 1, 1)


def chunks(start, end, months=6):
    cur = start
    while cur <= end:
        stop = min(end, date(cur.year + (cur.month + months - 1) // 12,
                             (cur.month + months - 1) % 12 + 1, 1) - timedelta(days=1))
        yield cur, stop
        cur = stop + timedelta(days=1)


def have(conn, lead):
    return {r[0] for r in conn.execute("SELECT date FROM renewables WHERE lead = ?", (lead,))}


def main():
    conn = db.connect()
    db.init_db(conn)
    today = date.today()

    print("Observe (ERA5) :")
    known = have(conn, 0)
    for start, stop in chunks(date(config.FIRST_SEASON, 9, 1), today - timedelta(days=6)):
        if all((start + timedelta(days=i)).isoformat() in known
               for i in range((stop - start).days + 1)):
            continue
        series = meteo.fetch_renewables(start, stop)[0]
        db.upsert_renewables(conn, meteo.to_renewable_rows(series, 0, "era5"))
        print(f"  {start} -> {stop} : {len(series)} jours", flush=True)

    print("Previsions archivees :")
    leads = list(range(1, config.MAX_ARCHIVED_LEAD + 1))
    known = {l: have(conn, l) for l in leads}
    for start, stop in chunks(PREVIOUS_RUNS_START, today - timedelta(days=1)):
        if all(all((start + timedelta(days=i)).isoformat() in known[l]
                   for i in range((stop - start).days + 1)) for l in leads):
            continue
        per_lead = meteo.fetch_renewables(start, stop, leads=leads)
        for lead, series in per_lead.items():
            db.upsert_renewables(conn, meteo.to_renewable_rows(series, lead, "previous-runs"))
        print(f"  {start} -> {stop} : {len(per_lead[1])} jours x {len(leads)} echeances",
              flush=True)

    n0 = conn.execute("SELECT COUNT(*) FROM renewables WHERE lead = 0").fetchone()[0]
    np_ = conn.execute("SELECT COUNT(*) FROM renewables WHERE lead > 0").fetchone()[0]
    print(f"\n{n0} jours observes, {np_} lignes de previsions archivees")


if __name__ == "__main__":
    main()
