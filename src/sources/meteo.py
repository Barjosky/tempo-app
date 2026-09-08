"""Meteo France ponderee population via Open-Meteo (gratuit, sans cle).

Trois regimes, distingues par le champ `lead` :
  lead = 0  -> reanalyse ERA5 (ce qui s'est reellement passe)
  lead = N  -> prevision telle qu'elle etait disponible N jours avant la date cible
               (archive `previous-runs`, disponible jusqu'a N = 7)
Le backtest n'utilise que des `lead > 0` pour eviter de tricher avec une meteo parfaite.
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import config

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


def _get(url, params, retries=3):
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{qs}", headers={"User-Agent": "tempo-predictor/1.0"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as r:
                data = json.loads(r.read().decode())
            if data.get("error"):
                raise RuntimeError(data.get("reason"))
            return data
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(3 * (attempt + 1))


def _blend(per_city):
    """Moyenne ponderee population -> une serie nationale par date."""
    acc = defaultdict(lambda: defaultdict(float))
    wsum = defaultdict(float)
    for weight, series in per_city:
        for day, vals in series.items():
            if vals.get("tmean") is None:
                continue
            for k, v in vals.items():
                if v is not None:
                    acc[day][k] += weight * v
            wsum[day] += weight
    out = {}
    for day, vals in acc.items():
        w = wsum[day] or 1.0
        row = {k: v / w for k, v in vals.items()}
        row["hdd"] = max(0.0, config.HDD_BASE - row["tmean"])
        out[day] = row
    return out


def _daily_from_payload(payload):
    d = payload["daily"]
    out = {}
    for i, day in enumerate(d["time"]):
        out[day] = {
            "tmean": d["temperature_2m_mean"][i],
            "tmin": d["temperature_2m_min"][i],
            "tmax": d["temperature_2m_max"][i],
            "wind": d["wind_speed_10m_max"][i],
        }
    return out


def fetch_archive(start, end):
    """Meteo observee (ERA5), agregee France, lead = 0."""
    per_city = []
    for name, lat, lon, weight in config.CITIES:
        payload = _get(ARCHIVE_URL, {
            "latitude": lat, "longitude": lon,
            "start_date": start.isoformat(), "end_date": end.isoformat(),
            "daily": "temperature_2m_mean,temperature_2m_min,temperature_2m_max,wind_speed_10m_max",
            "timezone": "Europe/Paris",
        })
        per_city.append((weight, _daily_from_payload(payload)))
        time.sleep(0.5)
    return _blend(per_city)


def _hourly_to_daily(payload, leads):
    """Agrege les series horaires previous_dayN en min/moy/max par date locale."""
    h = payload["hourly"]
    buckets = {lead: defaultdict(list) for lead in leads}
    for i, ts in enumerate(h["time"]):
        day = ts[:10]
        for lead in leads:
            v = h[f"temperature_2m_previous_day{lead}"][i]
            if v is not None:
                buckets[lead][day].append(v)
    out = {}
    for lead in leads:
        series = {}
        for day, values in buckets[lead].items():
            if len(values) < 20:  # journee incomplete -> inexploitable
                continue
            series[day] = {
                "tmean": sum(values) / len(values),
                "tmin": min(values),
                "tmax": max(values),
                "wind": None,
            }
        out[lead] = series
    return out


def fetch_previous_runs(start, end, leads):
    """Previsions archivees : ce que la meteo annoncait N jours avant. -> {lead: {date: vals}}"""
    variables = ",".join(f"temperature_2m_previous_day{n}" for n in leads)
    per_lead = {lead: [] for lead in leads}
    for name, lat, lon, weight in config.CITIES:
        payload = _get(PREVIOUS_RUNS_URL, {
            "latitude": lat, "longitude": lon,
            "start_date": start.isoformat(), "end_date": end.isoformat(),
            "hourly": variables,
            "timezone": "Europe/Paris",
        })
        city_leads = _hourly_to_daily(payload, leads)
        for lead in leads:
            per_lead[lead].append((weight, city_leads[lead]))
        time.sleep(0.5)
    return {lead: _blend(per_lead[lead]) for lead in leads}


def fetch_forecast(days=16, past_days=7):
    """Prevision a venir + jours recents (ERA5 arrive avec ~5 jours de retard)."""
    per_city = []
    for name, lat, lon, weight in config.CITIES:
        payload = _get(FORECAST_URL, {
            "latitude": lat, "longitude": lon,
            "daily": "temperature_2m_mean,temperature_2m_min,temperature_2m_max,wind_speed_10m_max",
            "forecast_days": days, "past_days": past_days, "timezone": "Europe/Paris",
        })
        per_city.append((weight, _daily_from_payload(payload)))
        time.sleep(0.3)
    return _blend(per_city)


def _wind_power(speed_kmh):
    """Courbe de puissance d'eolienne : la production suit le cube du vent, pas le vent."""
    v = speed_kmh / 3.6
    if v < config.WIND_CUT_IN or v > config.WIND_CUT_OUT:
        return 0.0
    if v >= config.WIND_RATED:
        return 1.0
    return ((v - config.WIND_CUT_IN) / (config.WIND_RATED - config.WIND_CUT_IN)) ** 3


def _renewable_daily(payload, wind_key, solar_key):
    """Agrege les series horaires en indices journaliers de production (0-1)."""
    h = payload["hourly"]
    wind, solar = defaultdict(list), defaultdict(list)
    for i, ts in enumerate(h["time"]):
        day = ts[:10]
        w = h[wind_key][i]
        s = h[solar_key][i]
        if w is not None:
            wind[day].append(_wind_power(w))
        if s is not None:
            solar[day].append(s)
    out = {}
    for day in wind:
        if len(wind[day]) < 20:
            continue
        rad = solar.get(day, [])
        out[day] = {
            "wind_index": sum(wind[day]) / len(wind[day]),
            # Rapporte a 250 W/m2 moyen : ordre de grandeur d'une belle journee d'ete.
            "solar_index": (sum(rad) / len(rad) / 250.0) if rad else None,
        }
    return out


def _blend_renewables(per_city_wind, per_city_solar):
    wind_acc, solar_acc = defaultdict(float), defaultdict(float)
    wind_w, solar_w = defaultdict(float), defaultdict(float)
    for weight, series in per_city_wind:
        for day, v in series.items():
            wind_acc[day] += weight * v["wind_index"]
            wind_w[day] += weight
    for weight, series in per_city_solar:
        for day, v in series.items():
            if v["solar_index"] is not None:
                solar_acc[day] += weight * v["solar_index"]
                solar_w[day] += weight
    return {day: {
        "wind_index": wind_acc[day] / (wind_w[day] or 1),
        "solar_index": solar_acc[day] / solar_w[day] if solar_w.get(day) else None,
    } for day in wind_acc}


def fetch_renewables(start, end, leads=None, forecast=False, past_days=7):
    """Indices eolien/solaire nationaux. leads=None -> observe (lead 0).

    Avec `leads`, on lit l'archive des previsions : ce que la meteo annoncait N jours
    avant, pour que le backtest reste honnete comme pour la temperature.
    """
    leads = leads or [0]
    per_lead_wind = {l: [] for l in leads}
    per_lead_solar = {l: [] for l in leads}

    for name, lat, lon, _ in config.CITIES:
        w_weight = config.WIND_WEIGHTS.get(name, 0.0)
        s_weight = config.SOLAR_WEIGHTS.get(name, 0.0)
        if leads == [0]:
            variables = "wind_speed_100m,shortwave_radiation"
            url, extra = (FORECAST_URL, {"forecast_days": 16, "past_days": past_days}) \
                if forecast else (ARCHIVE_URL, {"start_date": start.isoformat(),
                                                "end_date": end.isoformat()})
            payload = _get(url, {"latitude": lat, "longitude": lon, "hourly": variables,
                                 "timezone": "Europe/Paris", **extra})
            daily = _renewable_daily(payload, "wind_speed_100m", "shortwave_radiation")
            per_lead_wind[0].append((w_weight, daily))
            per_lead_solar[0].append((s_weight, daily))
        else:
            variables = ",".join(
                f"wind_speed_100m_previous_day{n},shortwave_radiation_previous_day{n}"
                for n in leads)
            payload = _get(PREVIOUS_RUNS_URL, {
                "latitude": lat, "longitude": lon, "hourly": variables,
                "start_date": start.isoformat(), "end_date": end.isoformat(),
                "timezone": "Europe/Paris"})
            for lead in leads:
                daily = _renewable_daily(payload, f"wind_speed_100m_previous_day{lead}",
                                         f"shortwave_radiation_previous_day{lead}")
                per_lead_wind[lead].append((w_weight, daily))
                per_lead_solar[lead].append((s_weight, daily))
        time.sleep(0.6)

    return {lead: _blend_renewables(per_lead_wind[lead], per_lead_solar[lead]) for lead in leads}


def to_renewable_rows(series, lead, source):
    now = datetime.now().isoformat(timespec="seconds")
    return [{"date": day, "lead": lead, "wind_index": v["wind_index"],
             "solar_index": v["solar_index"], "source": source, "fetched_at": now}
            for day, v in sorted(series.items())]


def to_weather_rows(series, lead, source):
    now = datetime.now().isoformat(timespec="seconds")
    return [{
        "date": day, "lead": lead,
        "tmean": v["tmean"], "tmin": v["tmin"], "tmax": v["tmax"],
        "wind": v.get("wind"), "hdd": v["hdd"],
        "source": source, "fetched_at": now,
    } for day, v in sorted(series.items())]
