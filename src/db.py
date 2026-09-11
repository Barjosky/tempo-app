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

-- Indisponibilites de production publiees par RTE, UNE LIGNE PAR PALIER de puissance :
-- un arret ne retire pas la meme puissance du premier au dernier jour, et le palier est
-- la maille ou RTE le dit.
--
-- La cle porte la VERSION, et c'est tout l'interet de la table. Un arret est republie a
-- chaque revision ; ne garder que la derniere donnerait le savoir d'AUJOURD'HUI sur un
-- jour passe, y compris les avaries declarees apres coup. En conservant les versions
-- avec leur `publication_date`, on peut ne retenir que ce qui etait publie le jour ou la
-- prediction aurait ete faite -- meme exigence que le `lead` de la table weather.
CREATE TABLE IF NOT EXISTS unavailabilities (
    identifier TEXT NOT NULL,
    version INTEGER NOT NULL,
    palier_start TEXT NOT NULL,
    palier_end TEXT NOT NULL,
    publication_date TEXT NOT NULL,
    fuel_type TEXT,
    unavailability_type TEXT,
    event_status TEXT,
    unit_name TEXT,
    installed_mw REAL,
    unavailable_mw REAL,
    fetched_at TEXT,
    PRIMARY KEY (identifier, version, palier_start)
);

CREATE INDEX IF NOT EXISTS idx_indispo_pub ON unavailabilities(publication_date);
CREATE INDEX IF NOT EXISTS idx_indispo_fin ON unavailabilities(palier_end);

-- Heure REELLE de chaque passage de la collecte. Un cron GitHub est un horaire
-- SOUHAITE : mesure faite sur quatre passages, il part avec 2 h 30 a 4 h de retard, et
-- de facon reproductible. Le compte a rebours de la page ne peut donc pas se calculer
-- sur l'horaire demande -- il tomberait a zero des heures avant que quoi que ce soit
-- ne bouge. Cette table est la mesure a partir de laquelle il s'annonce.
--
-- `trigger` separe les passages REGULIERS des essais manuels : un dispatch lance a
-- 15 h n'a rien a dire sur la cadence quotidienne et fausserait la mediane.
-- Jamais reecrite : c'est une serie de mesures, pas un etat.
CREATE TABLE IF NOT EXISTS runs (
    run_datetime TEXT PRIMARY KEY,
    run_date TEXT NOT NULL,
    trigger TEXT
);

-- Les predictions A JOUR, une seule par (jour de calcul, jour cible).
--
-- La table, elle, porte `model_version` dans sa cle : reentrainer le modele un jour ou
-- des predictions existent deja n'ecrase donc pas les anciennes, il en AJOUTE. Le
-- 11 septembre 2026, la page affichait ainsi 21 cartes pour 10 jours, chaque date deux
-- fois -- et, plus grave, l'historique comptait six paires (date, echeance) en double
-- sur seize : le taux de reussite publie portait sur des lignes dedoublees.
--
-- Filtrer a la lecture aurait demande d'y penser dans les quatre requetes, et dans
-- celles a venir. La vue le fait une fois pour toutes : c'est elle qu'on lit, jamais la
-- table. Elle est RECREEE a chaque init_db (drop puis create) pour qu'une base venant
-- du cache ne conserve pas une definition perimee.
--
-- Live et backtest sont dedoublonnes separement : ce sont deux series independantes,
-- et un backtest rejoue ne doit pas masquer la prediction reellement faite ce jour-la.
DROP VIEW IF EXISTS predictions_a_jour;
CREATE VIEW predictions_a_jour AS
SELECT * FROM (
    SELECT p.*, ROW_NUMBER() OVER (
        PARTITION BY run_date, target_date, (model_version LIKE 'backtest%')
        ORDER BY run_datetime DESC, model_version DESC) AS rang
    FROM predictions p)
WHERE rang = 1;

CREATE INDEX IF NOT EXISTS idx_pred_run ON predictions(run_date, target_date);

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


def upsert_unavailabilities(conn, rows):
    """Ecrit les paliers d'indisponibilite. Une version deja connue est reecrite a
    l'identique : RTE ne revise pas une version, il en publie une nouvelle."""
    conn.executemany(
        """INSERT INTO unavailabilities
           (identifier, version, palier_start, palier_end, publication_date, fuel_type,
            unavailability_type, event_status, unit_name, installed_mw, unavailable_mw,
            fetched_at)
           VALUES (:identifier, :version, :palier_start, :palier_end, :publication_date,
                   :fuel_type, :unavailability_type, :event_status, :unit_name,
                   :installed_mw, :unavailable_mw, :fetched_at)
           ON CONFLICT(identifier, version, palier_start) DO UPDATE SET
             palier_end = excluded.palier_end,
             publication_date = excluded.publication_date,
             event_status = excluded.event_status,
             unavailable_mw = excluded.unavailable_mw,
             fetched_at = excluded.fetched_at""",
        rows,
    )
    conn.commit()


def enregistrer_passage(conn, run_datetime, run_date, declencheur):
    conn.execute("""INSERT INTO runs (run_datetime, run_date, trigger)
                    VALUES (?, ?, ?) ON CONFLICT(run_datetime) DO NOTHING""",
                 (run_datetime, run_date, declencheur))
    conn.commit()


def passages_reguliers(conn, limite=40):
    """Les derniers passages PROGRAMMES, du plus recent au plus ancien."""
    tables = {r["name"] for r in
              conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "runs" not in tables:
        return []
    return [r["run_datetime"] for r in conn.execute(
        """SELECT run_datetime FROM runs WHERE trigger = 'schedule'
           ORDER BY run_datetime DESC LIMIT ?""", (limite,))]


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
