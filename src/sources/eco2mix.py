"""Consommation et production nationales via eCO2mix (ODRE / OpenDataSoft, sans cle).

Deux jeux complementaires :
  - eco2mix-national-cons-def : historique consolide (2012 -> ~M-2), definitif
  - eco2mix-national-tr       : temps reel, ~70 derniers jours, et surtout la
                                PREVISION DE CONSOMMATION DE RTE pour le lendemain
L'agregation journaliere est faite cote serveur pour ne pas rapatrier 500 000 lignes.
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import config

BASE = "https://odre.opendatasoft.com/api/explore/v2.1/catalog/datasets"
CONSOLIDE = "eco2mix-national-cons-def"
TEMPS_REEL = "eco2mix-national-tr"

# `max(nucleaire)` sert de proxy de disponibilite du parc : la puissance nucleaire
# reellement appelee dans la journee minore la puissance disponible, et son
# effondrement (hiver 2022-2023, corrosion sous contrainte) est precisement ce qui
# a multiplie les jours Rouge sans qu'il fasse plus froid pour autant.
DAILY_SELECT = ("max(consommation) as peak, avg(consommation) as moyenne, "
                "avg(eolien) as eolien, avg(solaire) as solaire, "
                "max(prevision_j1) as prevision_j1_peak, "
                "max(nucleaire) as nucleaire")


def _get(dataset, params, retries=3):
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{BASE}/{dataset}/records?{qs}",
                                 headers={"User-Agent": "tempo-predictor/1.0"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as r:
                return json.loads(r.read().decode())
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(3 * (attempt + 1))


def fetch_daily(dataset, start, end):
    """Agregats journaliers (MW) entre deux dates, par tranches de 90 jours."""
    out = {}
    cur = start
    while cur <= end:
        stop = min(end, cur + timedelta(days=89))
        payload = _get(dataset, {
            "select": DAILY_SELECT,
            "group_by": "date_format(date_heure,'yyyy-MM-dd') as jour",
            "where": f"date_heure>=date'{cur}' and date_heure<date'{stop + timedelta(days=1)}'",
            "limit": 100,
        })
        for row in payload.get("results", []):
            if row.get("peak") is None:
                continue
            out[row["jour"]] = {
                "peak_mw": row["peak"],
                "mean_mw": row["moyenne"],
                "eolien_mw": row["eolien"],
                "solaire_mw": row["solaire"],
                "prevision_j1_peak_mw": row.get("prevision_j1_peak"),
                "nucleaire_mw": row.get("nucleaire"),
            }
        cur = stop + timedelta(days=1)
        time.sleep(0.3)
    return out


def fetch_history(start, end):
    return fetch_daily(CONSOLIDE, start, end)


def fetch_recent(start, end):
    """Temps reel : jours recents + prevision RTE pour demain."""
    return fetch_daily(TEMPS_REEL, start, end)


def to_rows(series, source):
    now = datetime.now().isoformat(timespec="seconds")
    return [{
        "date": day, "peak_mw": v["peak_mw"], "mean_mw": v["mean_mw"],
        "eolien_mw": v["eolien_mw"], "solaire_mw": v["solaire_mw"],
        "prevision_j1_peak_mw": v["prevision_j1_peak_mw"],
        "nucleaire_mw": v.get("nucleaire_mw"),
        "source": source, "fetched_at": now,
    } for day, v in sorted(series.items())]
