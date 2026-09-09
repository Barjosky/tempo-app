"""Base synthetique : de quoi exercer tout le pipeline sans reseau ni collecte.

Les vraies sources demandent ~40 minutes de collecte et un acces reseau, ce qui rend
le pipeline intestable en local et en CI. On fabrique donc six saisons plausibles --
meteo saisonniere, renouvelables, consommation, et des couleurs posees selon les
regles Tempo reelles (quotas, fenetre Rouge, dimanches Bleu).

Ce n'est PAS un jeu de validation : les chiffres qui en sortent ne disent rien de la
qualite du modele. Ca sert a verifier que le code tourne, que les formes s'accordent
et que les features point-in-time ne regardent pas devant elles.
"""
import math
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
from src import db, rules
from src.sources import calendrier

SEASONS = ["2020-2021", "2021-2022", "2022-2023", "2023-2024", "2024-2025", "2025-2026"]
# Une saison ou le parc nucleaire s'effondre, comme en 2022-2023 : c'est le cas que
# la feature de disponibilite doit savoir distinguer d'un simple coup de froid.
CRISE_NUCLEAIRE = "2022-2023"


def _rng(seed):
    """Generateur congruentiel : reproductible, et sans dependance a numpy."""
    state = {"x": seed}

    def rand():
        state["x"] = (1103515245 * state["x"] + 12345) % (1 << 31)
        return state["x"] / (1 << 31)
    return rand


def _tmean(d, rand):
    """Temperature moyenne France : cycle annuel + bruit + episodes froids."""
    doy = d.timetuple().tm_yday
    base = 12.5 - 8.5 * math.cos(2 * math.pi * (doy - 15) / 365.25)
    return base + (rand() - 0.5) * 7.0


def build(path, seasons=SEASONS):
    """Ecrit une base complete a `path` et renvoie la connexion ouverte."""
    conn = db.connect(path)
    db.init_db(conn)
    rand = _rng(20260909)

    weather, renew, conso, days = [], [], [], []
    now = "2026-09-09T00:00:00"

    for season in seasons:
        start, end = calendrier.season_start(season), calendrier.season_end(season)
        temps = {}
        d = start
        while d <= end:
            temps[d] = _tmean(d, rand)
            d += timedelta(days=1)

        # Couleurs : les jours eligibles les plus froids prennent les Rouge, puis les
        # plus froids parmi les jours ou le Blanc est possible prennent les Blanc.
        eligibles = sorted((d for d in temps if rules.rouge_possible(d)),
                           key=lambda x: temps[x])
        rouges = set(eligibles[:config.QUOTA_ROUGE])
        blancs_possibles = sorted(
            (d for d in temps if d not in rouges and rules.blanc_possible(d)),
            key=lambda x: temps[x])
        blancs = set(blancs_possibles[:config.QUOTA_BLANC])

        crise = season == CRISE_NUCLEAIRE
        for d, tmean in temps.items():
            hdd = max(0.0, config.HDD_BASE - tmean)
            color = (config.ROUGE if d in rouges
                     else config.BLANC if d in blancs else config.BLEU)
            days.append({
                "date": d.isoformat(), "season": season, "color": color,
                "weekday": d.weekday(), "is_holiday": int(calendrier.is_holiday(d)),
                "source": "synthetique", "fetched_at": now,
            })

            wind = min(1.0, max(0.0, 0.35 + (rand() - 0.5) * 0.6))
            solar = max(0.0, 0.55 - 0.4 * math.cos(2 * math.pi * d.timetuple().tm_yday / 365.25))
            for lead in range(0, config.MAX_HORIZON + 1):
                # Plus l'echeance est lointaine, plus la prevision derive.
                drift = (rand() - 0.5) * 0.9 * lead
                weather.append({
                    "date": d.isoformat(), "lead": lead,
                    "tmean": tmean + drift, "tmin": tmean + drift - 4.0,
                    "tmax": tmean + drift + 4.5, "wind": 4.0 + 8.0 * wind,
                    "hdd": max(0.0, config.HDD_BASE - (tmean + drift)),
                    "source": "synthetique", "fetched_at": now,
                })
                renew.append({
                    "date": d.isoformat(), "lead": lead,
                    "wind_index": min(1.0, max(0.0, wind + (rand() - 0.5) * 0.12 * lead)),
                    "solar_index": solar, "source": "synthetique", "fetched_at": now,
                })

            peak = 46000 + 2400 * hdd + (rand() - 0.5) * 2500
            nucleaire = (30000 if crise else 45000) + (rand() - 0.5) * 4000
            conso.append({
                "date": d.isoformat(), "peak_mw": peak, "mean_mw": peak * 0.82,
                "eolien_mw": 21900 * wind, "solaire_mw": 9000 * solar,
                "prevision_j1_peak_mw": peak + (rand() - 0.5) * 1200,
                "nucleaire_mw": nucleaire,
                "source": "synthetique", "fetched_at": now,
            })

    db.upsert_days(conn, days)
    db.upsert_weather(conn, weather)
    db.upsert_renewables(conn, renew)
    db.upsert_conso(conn, conso)
    return conn


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "store/synthetique.db")
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    c = build(out)
    n = c.execute("SELECT COUNT(*) FROM days").fetchone()[0]
    print(f"{n} jours ecrits dans {out}")
