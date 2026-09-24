"""Couleurs Tempo officielles via api-couleur-tempo.fr (gratuite, sans cle)."""
import json
import sys
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import config
from src import reseau
from src.sources import calendrier

BASE = "https://www.api-couleur-tempo.fr/api"


def _get(url):
    """Le quatrieme client HTTP du projet, oublie lors du correctif du 20 septembre.

    Il n'avait AUCUN reessai : le 24 septembre, une alerte TLS du serveur
    (`TLSV1_ALERT_INTERNAL_ERROR`) a tue le passage en une seconde et demie. Le
    correctif precedent annoncait « les trois clients » ; il y en avait quatre. D'ou
    le test qui les retrouve lui-meme au lieu de compter sur la memoire.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "tempo-predictor/1.0"})

    def tentative():
        with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as r:
            return json.loads(r.read().decode())

    def transitoire(exc):
        if isinstance(exc, urllib.error.HTTPError):
            return reseau.transitoire_http(exc.code)
        return reseau.transitoire_reseau(exc)

    return reseau.reessayer(tentative, transitoire)


def fetch_season(season):
    """Toutes les couleurs connues d'une saison ('2024-2025'). code 0 = non encore defini."""
    return _get(f"{BASE}/joursTempo?periode={season}")


def fetch_day(day):
    """Couleur d'une date precise (aujourd'hui / demain inclus)."""
    return _get(f"{BASE}/jourTempo/{day.isoformat()}")


def to_day_rows(raw_days):
    now = datetime.now().isoformat(timespec="seconds")
    rows = []
    for item in raw_days:
        d = date.fromisoformat(item["dateJour"])
        code = item.get("codeJour") or 0
        rows.append({
            "date": d.isoformat(),
            "season": calendrier.season_of(d),
            "color": code if code in (1, 2, 3) else None,
            "weekday": d.weekday(),
            "is_holiday": int(calendrier.is_holiday(d)),
            "source": "api-couleur-tempo",
            "fetched_at": now,
        })
    return rows
