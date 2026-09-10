"""Schema SQLite et acces aux donnees."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS days (
    date TEXT PRIMARY KEY,
    season TEXT NOT NULL,
    color INTEGER,
    weekday INTEGER NOT NULL,
    is_holiday INTEGER NOT NULL,
    source TEXT,
    fetched_at TEXT
);

-- lead = 0 : observe (reanalyse ERA5). lead = N : prevision telle que
-- disponible N jours avant la date cible. C'est ce qui rend le backtest honnete.
CREATE TABLE IF NOT EXISTS weather (
    date TEXT NOT NULL,
    lead INTEGER NOT NULL,
    tmean REAL,
    tmin REAL,
    tmax REAL,
    wind REAL,
    hdd REAL,
    source TEXT,
    fetched_at TEXT,
    PRIMARY KEY (date, lead)
);

-- Indices de production renouvelable derives de la meteo (0-1), memes conventions
-- de `lead` que la table weather. Sert a estimer la consommation NETTE : un jour
-- froid mais venteux sollicite bien moins le parc pilotable qu'un jour froid sans vent.
CREATE TABLE IF NOT EXISTS renewables (
    date TEXT NOT NULL,
    lead INTEGER NOT NULL,
    wind_index REAL,
    solar_index REAL,
    source TEXT,
    fetched_at TEXT,
    PRIMARY KEY (date, lead)
);

-- Consommation et production nationales reelles (eCO2mix), en MW.
-- `prevision_j1_peak_mw` est la prevision de RTE elle-meme pour le lendemain.
-- `nucleaire_mw` est le cote OFFRE : un jour Rouge nait d'une marge tendue, pas
-- d'un froid absolu, et la puissance nucleaire disponible en est le premier terme.
CREATE TABLE IF NOT EXISTS conso (
    date TEXT PRIMARY KEY,
    peak_mw REAL,
    mean_mw REAL,
    eolien_mw REAL,
    solaire_mw REAL,
    prevision_j1_peak_mw REAL,
    nucleaire_mw REAL,
    source TEXT,
    fetched_at TEXT
);

-- Jamais reecrit : c'est la memoire des predictions figees, base de l'onglet Historique.
CREATE TABLE IF NOT EXISTS predictions (
    run_datetime TEXT NOT NULL,
    run_date TEXT NOT NULL,
    target_date TEXT NOT NULL,
    horizon INTEGER NOT NULL,
    p_bleu REAL NOT NULL,
    p_blanc REAL NOT NULL,
    p_rouge REAL NOT NULL,
    predicted_color INTEGER NOT NULL,
    is_official INTEGER NOT NULL DEFAULT 0,
    model_version TEXT NOT NULL,
    PRIMARY KEY (run_date, target_date, model_version)
);

CREATE TABLE IF NOT EXISTS model_runs (
    version TEXT PRIMARY KEY,
    trained_at TEXT NOT NULL,
    metrics_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_pred_target ON predictions(target_date);
CREATE INDEX IF NOT EXISTS idx_days_season ON days(season);
"""


def connect(path=None):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Colonnes ajoutees apres coup. `CREATE TABLE IF NOT EXISTS` ne touche pas une table
# existante : sans ce rattrapage, une base deja constituee (celle du cache Actions,
# par exemple) resterait sans les nouvelles colonnes et la collecte planterait.
MIGRATIONS = [
    ("conso", "nucleaire_mw", "REAL"),
]


def init_db(conn):
    conn.executescript(SCHEMA)
    for table, column, coltype in MIGRATIONS:
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if existing and column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
    conn.commit()


def upsert_days(conn, rows):
    conn.executemany(
        """INSERT INTO days (date, season, color, weekday, is_holiday, source, fetched_at)
           VALUES (:date, :season, :color, :weekday, :is_holiday, :source, :fetched_at)
           ON CONFLICT(date) DO UPDATE SET
             color = COALESCE(excluded.color, days.color),
             season = excluded.season,
             source = excluded.source,
             fetched_at = excluded.fetched_at""",
        rows,
    )
    conn.commit()


def upsert_weather(conn, rows):
    conn.executemany(
        """INSERT INTO weather (date, lead, tmean, tmin, tmax, wind, hdd, source, fetched_at)
           VALUES (:date, :lead, :tmean, :tmin, :tmax, :wind, :hdd, :source, :fetched_at)
           ON CONFLICT(date, lead) DO UPDATE SET
             tmean = excluded.tmean, tmin = excluded.tmin, tmax = excluded.tmax,
             wind = excluded.wind, hdd = excluded.hdd,
             source = excluded.source, fetched_at = excluded.fetched_at""",
        rows,
    )
    conn.commit()


def upsert_renewables(conn, rows):
    conn.executemany(
        """INSERT INTO renewables (date, lead, wind_index, solar_index, source, fetched_at)
           VALUES (:date, :lead, :wind_index, :solar_index, :source, :fetched_at)
           ON CONFLICT(date, lead) DO UPDATE SET
             wind_index = excluded.wind_index, solar_index = excluded.solar_index,
             source = excluded.source, fetched_at = excluded.fetched_at""",
        rows,
    )
    conn.commit()


def upsert_conso(conn, rows):
    conn.executemany(
        """INSERT INTO conso (date, peak_mw, mean_mw, eolien_mw, solaire_mw,
                              prevision_j1_peak_mw, nucleaire_mw, source, fetched_at)
           VALUES (:date, :peak_mw, :mean_mw, :eolien_mw, :solaire_mw,
                   :prevision_j1_peak_mw, :nucleaire_mw, :source, :fetched_at)
           ON CONFLICT(date) DO UPDATE SET
             peak_mw = COALESCE(excluded.peak_mw, conso.peak_mw),
             mean_mw = COALESCE(excluded.mean_mw, conso.mean_mw),
             eolien_mw = COALESCE(excluded.eolien_mw, conso.eolien_mw),
             solaire_mw = COALESCE(excluded.solaire_mw, conso.solaire_mw),
             prevision_j1_peak_mw = COALESCE(excluded.prevision_j1_peak_mw,
                                             conso.prevision_j1_peak_mw),
             nucleaire_mw = COALESCE(excluded.nucleaire_mw, conso.nucleaire_mw),
             source = excluded.source, fetched_at = excluded.fetched_at""",
        rows,
    )
    conn.commit()


def insert_predictions(conn, rows):
    conn.executemany(
        """INSERT INTO predictions
           (run_datetime, run_date, target_date, horizon, p_bleu, p_blanc, p_rouge,
            predicted_color, is_official, model_version)
           VALUES (:run_datetime, :run_date, :target_date, :horizon, :p_bleu, :p_blanc,
                   :p_rouge, :predicted_color, :is_official, :model_version)
           ON CONFLICT(run_date, target_date, model_version) DO UPDATE SET
             p_bleu = excluded.p_bleu, p_blanc = excluded.p_blanc, p_rouge = excluded.p_rouge,
             predicted_color = excluded.predicted_color, is_official = excluded.is_official,
             run_datetime = excluded.run_datetime""",
        rows,
    )
    conn.commit()


def load_days(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM days ORDER BY date")]


def load_weather(conn):
    out = {}
    for r in conn.execute("SELECT * FROM weather"):
        out[(r["date"], r["lead"])] = dict(r)
    return out
