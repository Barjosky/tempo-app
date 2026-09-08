"""Collecte historique complete : couleurs Tempo + meteo (observee et prevue a l'epoque).

Usage:
    python ingest.py            # complete ce qui manque
    python ingest.py --full     # recharge tout depuis 2020
"""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config
from src import db
from src.sources import calendrier, meteo, tempo_api

PREVIOUS_RUNS_START = date(2022, 1, 1)  # profondeur de l'archive de previsions


def seasons_to_load():
    today = date.today()
    last = int(calendrier.season_of(today).split("-")[0])
    return [f"{y}-{y + 1}" for y in range(config.FIRST_SEASON, last + 1)]


def ingest_colors(conn):
    total = 0
    for season in seasons_to_load():
        raw = tempo_api.fetch_season(season)
        rows = tempo_api.to_day_rows(raw)
        db.upsert_days(conn, rows)
        known = sum(1 for r in rows if r["color"])
        total += known
        print(f"  {season}: {len(rows)} jours, {known} couleurs connues")
    return total


def fill_calendar(conn, start, end):
    """Garantit une ligne par date (meme sans couleur) pour les features calendaires."""
    now = datetime.now().isoformat(timespec="seconds")
    rows = [{
        "date": d.isoformat(), "season": calendrier.season_of(d), "color": None,
        "weekday": d.weekday(), "is_holiday": int(calendrier.is_holiday(d)),
        "source": "calendrier", "fetched_at": now,
    } for d in calendrier.daterange(start, end)]
    conn.executemany(
        """INSERT OR IGNORE INTO days (date, season, color, weekday, is_holiday, source, fetched_at)
           VALUES (:date, :season, :color, :weekday, :is_holiday, :source, :fetched_at)""",
        rows,
    )
    conn.commit()


def existing_weather_dates(conn, lead):
    return {r[0] for r in conn.execute("SELECT date FROM weather WHERE lead = ?", (lead,))}


def ingest_archive(conn, start, end, force=False):
    have = set() if force else existing_weather_dates(conn, 0)
    year = start.year
    while date(year, 1, 1) <= end:
        chunk_start = max(start, date(year, 1, 1))
        chunk_end = min(end, date(year, 12, 31))
        wanted = [d.isoformat() for d in calendrier.daterange(chunk_start, chunk_end)]
        if not force and all(d in have for d in wanted):
            year += 1
            continue
        series = meteo.fetch_archive(chunk_start, chunk_end)
        db.upsert_weather(conn, meteo.to_weather_rows(series, 0, "era5"))
        print(f"  archive {year}: {len(series)} jours")
        year += 1


def ingest_previous_runs(conn, start, end, force=False):
    leads = list(range(1, config.MAX_ARCHIVED_LEAD + 1))
    have = {lead: (set() if force else existing_weather_dates(conn, lead)) for lead in leads}
    year = start.year
    while date(year, 1, 1) <= end:
        chunk_start = max(start, date(year, 1, 1))
        chunk_end = min(end, date(year, 12, 31))
        wanted = [d.isoformat() for d in calendrier.daterange(chunk_start, chunk_end)]
        missing = [l for l in leads if not all(d in have[l] for d in wanted)]
        if not missing:
            year += 1
            continue
        per_lead = meteo.fetch_previous_runs(chunk_start, chunk_end, leads)
        for lead, series in per_lead.items():
            db.upsert_weather(conn, meteo.to_weather_rows(series, lead, "previous-runs"))
        print(f"  previsions archivees {year}: {len(per_lead[1])} jours x {len(leads)} echeances")
        year += 1


def main():
    force = "--full" in sys.argv
    conn = db.connect()
    db.init_db(conn)

    today = date.today()
    start = date(config.FIRST_SEASON, 9, 1)

    print("Couleurs Tempo :")
    ingest_colors(conn)
    fill_calendar(conn, start, today + timedelta(days=config.MAX_HORIZON))

    print("Meteo observee (ERA5) :")
    ingest_archive(conn, start - timedelta(days=30), today - timedelta(days=6), force)

    print("Previsions meteo archivees :")
    ingest_previous_runs(conn, PREVIOUS_RUNS_START, today - timedelta(days=1), force)

    n_days = conn.execute("SELECT COUNT(*) FROM days WHERE color IS NOT NULL").fetchone()[0]
    n_w0 = conn.execute("SELECT COUNT(*) FROM weather WHERE lead = 0").fetchone()[0]
    n_wp = conn.execute("SELECT COUNT(*) FROM weather WHERE lead > 0").fetchone()[0]
    print(f"\nBase : {n_days} jours colores, {n_w0} jours meteo observee, {n_wp} lignes de previsions archivees")


if __name__ == "__main__":
    main()
