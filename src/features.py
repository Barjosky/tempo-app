"""Construction des features, strictement point-in-time.

Pour une paire (run_date R, target_date T) avec horizon h = T - R :
seules les informations disponibles le jour R sont utilisees -- couleurs
connues jusqu'a R inclus, et meteo telle que PREVUE h jours a l'avance.
"""
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
from src import rules
from src.sources import calendrier

FEATURE_NAMES = [
    "horizon", "weekday", "is_saturday", "is_holiday", "holiday_adjacent", "month",
    "doy_sin", "doy_cos", "season_day", "in_rouge_window", "xmas_break",
    "tmean_f", "tmin_f", "tmax_f", "hdd_f", "weather_lead_used", "temp_anomaly",
    "hdd_prev", "hdd_next", "hdd_win3",
    "rouge_used", "rouge_left", "blanc_used", "blanc_left",
    "rouge_pressure", "blanc_pressure",
    "days_since_rouge", "days_since_blanc",
    "rouge_last7", "rouge_last14", "blanc_last7",
    "rouge_known_week", "blanc_known_week",
    "hdd_observed_7d",
    "hdd_rank_window", "hdd_gap_to_coldest", "is_coldest_window",
    "hdd_rank_week", "is_coldest_week", "hdd_pct_season",
    # Consommation nette : c'est le parc pilotable a solliciter qui declenche un Rouge,
    # donc la demande MOINS l'eolien et le solaire, pas la demande brute.
    "wind_index", "solar_index", "net_load", "net_load_rank_window",
    "is_peak_net_window", "net_load_pct_season", "wind_index_window_mean",
    # Charge residuelle en MW reels : consommation attendue (modele de demande appris
    # sur eCO2mix) moins l'eolien et le solaire attendus (proxys calibres en MW).
    "peak_mw_pred", "wind_mw_pred", "residual_mw", "residual_rank_window",
    "residual_pct_season", "residual_vs_season_max",
]


class FeatureStore:
    """Index en memoire des jours et de la meteo, + climatologie."""

    def __init__(self, conn, noise_seed=7):
        self.noise_seed = noise_seed
        self._noise_cache = {}
        self.days = {}
        for r in conn.execute("SELECT * FROM days ORDER BY date"):
            self.days[date.fromisoformat(r["date"])] = {
                "color": r["color"], "season": r["season"],
                "weekday": r["weekday"], "is_holiday": bool(r["is_holiday"]),
            }
        self.weather = {}
        for r in conn.execute("SELECT * FROM weather"):
            self.weather[(date.fromisoformat(r["date"]), r["lead"])] = {
                "tmean": r["tmean"], "tmin": r["tmin"], "tmax": r["tmax"], "hdd": r["hdd"],
            }
        self.renewables = {}
        for r in conn.execute("SELECT * FROM renewables"):
            self.renewables[(date.fromisoformat(r["date"]), r["lead"])] = {
                "wind_index": r["wind_index"], "solar_index": r["solar_index"],
            }
        self._state_cache = {}
        self._window_cache = {}
        self._season_hdd_cache = {}
        self._net_window_cache = {}
        self._net_season_cache = {}
        self._residual_season_cache = {}
        self._residual_window_cache = {}
        self.conso = {}
        for r in conn.execute("SELECT * FROM conso"):
            self.conso[date.fromisoformat(r["date"])] = {
                "peak_mw": r["peak_mw"], "eolien_mw": r["eolien_mw"],
                "solaire_mw": r["solaire_mw"],
            }
        self.demand_coef = self.wind_coef = self.solar_coef = None
        self.wind_sigma, self.solar_sigma = {}, {}
        self._climatology()
        self._forecast_bias()

    def _climatology(self):
        """Temperature normale par jour de l'annee (fenetre glissante +/- 7 j)."""
        by_doy = {}
        for (d, lead), v in self.weather.items():
            if lead != 0 or v["tmean"] is None:
                continue
            by_doy.setdefault(d.timetuple().tm_yday, []).append(v["tmean"])
        self.normal = {}
        for doy in range(1, 367):
            vals = []
            for off in range(-7, 8):
                vals += by_doy.get((doy + off - 1) % 366 + 1, [])
            self.normal[doy] = sum(vals) / len(vals) if vals else None

    def _forecast_bias(self):
        """Erreur type des previsions par echeance, mesuree sur l'archive reelle.

        Sert a degrader la meteo observee en pseudo-prevision pour les annees
        anterieures a 2022 (ou l'archive de previsions n'existe pas), plutot que
        d'entrainer le modele sur une meteo parfaite qu'il n'aura jamais.
        """
        self.lead_sigma = {}
        for lead in range(1, config.MAX_ARCHIVED_LEAD + 1):
            errs = []
            for (d, l), v in self.weather.items():
                if l != lead or v["tmean"] is None:
                    continue
                obs = self.weather.get((d, 0))
                if obs and obs["tmean"] is not None:
                    errs.append(v["tmean"] - obs["tmean"])
            self.lead_sigma[lead] = float(np.std(errs)) if len(errs) > 30 else 1.0 + 0.25 * lead

        # Meme mesure pour l'eolien et le solaire.
        self.wind_sigma, self.solar_sigma = {}, {}
        for lead in range(1, config.MAX_ARCHIVED_LEAD + 1):
            we, se = [], []
            for (d, l), v in self.renewables.items():
                if l != lead:
                    continue
                obs = self.renewables.get((d, 0))
                if not obs:
                    continue
                if v["wind_index"] is not None and obs["wind_index"] is not None:
                    we.append(v["wind_index"] - obs["wind_index"])
                if v["solar_index"] is not None and obs["solar_index"] is not None:
                    se.append(v["solar_index"] - obs["solar_index"])
            self.wind_sigma[lead] = float(np.std(we)) if len(we) > 30 else 0.05 + 0.02 * lead
            self.solar_sigma[lead] = float(np.std(se)) if len(se) > 30 else 0.03 + 0.01 * lead

    def _noise(self, target, lead):
        """Tirage normal deterministe : la meme (date, echeance) donne toujours la meme
        pseudo-prevision, sinon les features de froid relatif deviennent incoherentes."""
        key = (target.toordinal(), lead)
        if key not in self._noise_cache:
            self._noise_cache[key] = float(
                np.random.default_rng([target.toordinal(), lead, self.noise_seed]).normal())
        return self._noise_cache[key]

    def weather_for(self, target, horizon, rng=None):
        """Meteo telle que connue `horizon` jours avant `target`.

        - horizon <= 7 : prevision reellement archivee.
        - horizon > 7  : on retombe sur la prevision a 7 jours (conservateur).
        - avant 2022   : observe + bruit calibre sur l'erreur reelle de l'echeance.
        """
        lead = min(horizon, config.MAX_ARCHIVED_LEAD)
        for candidate in (lead, horizon):
            w = self.weather.get((target, candidate))
            if w and w["tmean"] is not None:
                return w, lead
        obs = self.weather.get((target, 0))
        if not obs or obs["tmean"] is None:
            return None, -1
        sigma = self.lead_sigma.get(lead, 1.5)
        noise = sigma * self._noise(target, lead) if rng is not None else 0.0
        tmean = obs["tmean"] + noise
        return ({
            "tmean": tmean,
            "tmin": obs["tmin"] + noise if obs["tmin"] is not None else None,
            "tmax": obs["tmax"] + noise if obs["tmax"] is not None else None,
            "hdd": max(0.0, config.HDD_BASE - tmean),
        }, -lead)  # lead negatif = pseudo-prevision

    def season_state(self, run_date):
        """Quotas consommes a la date R (couleurs connues jusqu'a R inclus)."""
        if run_date in self._state_cache:
            return self._state_cache[run_date]
        season = calendrier.season_of(run_date)
        start = calendrier.season_start(season)
        rouge = blanc = 0
        last_rouge = last_blanc = None
        r7 = r14 = b7 = 0
        d = start
        while d <= run_date:
            info = self.days.get(d)
            if info and info["color"]:
                delta = (run_date - d).days
                if info["color"] == config.ROUGE:
                    rouge += 1
                    last_rouge = d
                    r7 += delta < 7
                    r14 += delta < 14
                elif info["color"] == config.BLANC:
                    blanc += 1
                    last_blanc = d
                    b7 += delta < 7
            d += timedelta(days=1)
        state = {
            "season": season, "rouge_used": rouge, "blanc_used": blanc,
            "rouge_left": config.QUOTA_ROUGE - rouge, "blanc_left": config.QUOTA_BLANC - blanc,
            "last_rouge": last_rouge, "last_blanc": last_blanc,
            "rouge_last7": r7, "rouge_last14": r14, "blanc_last7": b7,
        }
        self._state_cache[run_date] = state
        return state

    def fit_demand_model(self, cutoff):
        """Apprend la reponse de la consommation au froid, sur les seules donnees < cutoff.

        C'est la reponse a « la consommation va monter avec le pic de froid » : au lieu
        d'un proxy de degres-jours, on estime la pointe nationale en MW, puis on en
        retranche l'eolien et le solaire attendus pour obtenir la charge residuelle --
        ce que le parc pilotable devra fournir, et donc ce qui declenche un Rouge.
        Le cutoff garantit qu'aucune donnee posterieure a la saison evaluee n'y entre.
        """
        rows = [(d, v) for d, v in self.conso.items()
                if d < cutoff and v["peak_mw"] is not None]
        if len(rows) < 200:
            self.demand_coef = None
            return
        X, y = [], []
        for d, v in rows:
            w = self.weather.get((d, 0))
            if not w or w["hdd"] is None:
                continue
            X.append(self._demand_features(d, w["hdd"]))
            y.append(v["peak_mw"])
        if len(X) < 200:
            self.demand_coef = None
            return
        self.demand_coef = np.linalg.lstsq(np.array(X), np.array(y), rcond=None)[0]

        # Conversion des indices meteo en MW, calibree sur la production reelle.
        self.wind_coef = self._fit_linear(
            [(self.renewables.get((d, 0)) or {}).get("wind_index") for d, _ in rows],
            [v["eolien_mw"] for _, v in rows])
        self.solar_coef = self._fit_linear(
            [(self.renewables.get((d, 0)) or {}).get("solar_index") for d, _ in rows],
            [v["solaire_mw"] for _, v in rows])

    @staticmethod
    def _fit_linear(xs, ys):
        pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
        if len(pairs) < 100:
            return None
        x = np.array([p[0] for p in pairs])
        y = np.array([p[1] for p in pairs])
        A = np.vstack([x, np.ones(len(x))]).T
        return np.linalg.lstsq(A, y, rcond=None)[0]

    @staticmethod
    def _demand_features(d, hdd):
        doy = d.timetuple().tm_yday
        wd = d.weekday()
        return [
            hdd, hdd ** 2,
            *[1.0 if wd == k else 0.0 for k in range(6)],
            1.0 if calendrier.is_holiday(d) else 0.0,
            math.sin(2 * math.pi * doy / 365.25), math.cos(2 * math.pi * doy / 365.25),
            (d - date(2020, 9, 1)).days / 365.25,
            1.0,
        ]

    def predicted_peak(self, target, hdd):
        if self.demand_coef is None or hdd != hdd:
            return float("nan")
        return float(np.dot(self._demand_features(target, hdd), self.demand_coef))

    def residual_for(self, target, horizon, hdd):
        """Charge residuelle attendue (MW) : pointe estimee - eolien - solaire."""
        peak = self.predicted_peak(target, hdd)
        if peak != peak:
            return float("nan"), float("nan"), float("nan")
        r = self.renewables_for(target, horizon)
        wind_mw = solar_mw = 0.0
        if r and self.wind_coef is not None and r["wind_index"] is not None:
            wind_mw = float(self.wind_coef[0] * r["wind_index"] + self.wind_coef[1])
        if r and self.solar_coef is not None and r["solar_index"] is not None:
            solar_mw = float(self.solar_coef[0] * r["solar_index"] + self.solar_coef[1])
        return peak, wind_mw, peak - wind_mw - solar_mw

    def observed_residuals(self, run_date):
        """Residuels reellement constates depuis le debut de la saison (jusqu'a R)."""
        if run_date in self._residual_season_cache:
            return self._residual_season_cache[run_date]
        vals = []
        d = calendrier.season_start(calendrier.season_of(run_date))
        while d <= run_date:
            v = self.conso.get(d)
            if v and v["peak_mw"] is not None and v["eolien_mw"] is not None:
                vals.append(v["peak_mw"] - v["eolien_mw"] - (v["solaire_mw"] or 0))
            d += timedelta(days=1)
        vals.sort()
        self._residual_season_cache[run_date] = vals
        return vals

    def renewables_for(self, target, horizon):
        """Eolien/solaire tels que prevus `horizon` jours avant la cible.

        Jamais de repli direct sur l'observe pour un horizon > 0 : cela donnerait au
        modele une vision parfaite du vent a venir, qu'il n'aura jamais en production.
        Avant 2022 (pas d'archive de previsions), on degrade l'observe avec un bruit
        cale sur l'erreur de prevision reellement mesuree a cette echeance.
        """
        lead = min(horizon, config.MAX_ARCHIVED_LEAD)
        for candidate in (lead, horizon):
            r = self.renewables.get((target, candidate))
            if r and r["wind_index"] is not None:
                return r
        if horizon == 0:
            return self.renewables.get((target, 0))
        obs = self.renewables.get((target, 0))
        if not obs or obs["wind_index"] is None:
            return None
        noise = self._noise(target, 100 + lead)
        wind = min(1.0, max(0.0, obs["wind_index"] + noise * self.wind_sigma.get(lead, 0.15)))
        solar = obs["solar_index"]
        if solar is not None:
            solar = min(1.5, max(0.0, solar + noise * self.solar_sigma.get(lead, 0.05)))
        return {"wind_index": wind, "solar_index": solar}

    def net_load_for(self, target, horizon, hdd):
        """Charge nette approchee, en degres-jours equivalents.

        hdd mesure la demande de chauffage ; on en retranche ce que l'eolien et le
        solaire couvriront. Les coefficients sont des ordres de grandeur (parc eolien
        ~24 GW contre ~2,4 GW/degre de sensibilite thermique) : le modele voit de toute
        facon les composantes brutes et peut les recombiner lui-meme.
        """
        r = self.renewables_for(target, horizon)
        if r is None or hdd is None or hdd != hdd:
            return float("nan"), float("nan"), float("nan")
        wind = r["wind_index"] or 0.0
        solar = r["solar_index"] or 0.0
        return hdd - 10.0 * wind - 4.0 * solar, wind, solar

    def net_load_window(self, run_date, rng=None):
        """Charge nette prevue pour R+1..R+10, telle que connue le jour R."""
        if run_date in self._net_window_cache:
            return self._net_window_cache[run_date]
        window = {}
        for h in range(1, config.MAX_HORIZON + 1):
            d = run_date + timedelta(days=h)
            w, _ = self.weather_for(d, h, rng)
            if w is None:
                continue
            net, _, _ = self.net_load_for(d, h, w["hdd"])
            if net == net:
                window[d] = net
        self._net_window_cache[run_date] = window
        return window

    def net_load_percentile(self, run_date, value):
        """Position de la charge nette prevue dans ce qui a deja ete observe cette saison."""
        if run_date not in self._net_season_cache:
            season = calendrier.season_of(run_date)
            vals = []
            d = calendrier.season_start(season)
            while d <= run_date:
                w = self.weather.get((d, 0))
                if w and w["hdd"] is not None:
                    net, _, _ = self.net_load_for(d, 0, w["hdd"])
                    if net == net:
                        vals.append(net)
                d += timedelta(days=1)
            self._net_season_cache[run_date] = sorted(vals)
        vals = self._net_season_cache[run_date]
        if len(vals) < 10 or value != value:
            return float("nan")
        return float(np.searchsorted(vals, value) / len(vals))

    def forecast_window(self, run_date, rng=None):
        """HDD prevus pour R+1..R+10 tels que connus le jour R (froid relatif).

        C'est le signal cle : EDF classe un jour Rouge parce qu'il est parmi les
        plus froids de la periode, pas parce qu'il depasse un seuil absolu.
        """
        if run_date in self._window_cache:
            return self._window_cache[run_date]
        window = {}
        for h in range(1, config.MAX_HORIZON + 1):
            d = run_date + timedelta(days=h)
            w, _ = self.weather_for(d, h, rng)
            if w and w["hdd"] is not None:
                window[d] = w["hdd"]
        self._window_cache[run_date] = window
        return window

    def season_hdd_percentile(self, run_date, hdd):
        """Position du froid prevu dans la distribution deja observee cette saison."""
        if run_date not in self._season_hdd_cache:
            season = calendrier.season_of(run_date)
            start = calendrier.season_start(season)
            vals = []
            d = start
            while d <= run_date:
                w = self.weather.get((d, 0))
                if w and w["hdd"] is not None:
                    vals.append(w["hdd"])
                d += timedelta(days=1)
            self._season_hdd_cache[run_date] = sorted(vals)
        vals = self._season_hdd_cache[run_date]
        if len(vals) < 10 or hdd != hdd:
            return float("nan")
        return float(np.searchsorted(vals, hdd) / len(vals))

    def observed_hdd(self, run_date, days=7):
        vals = []
        for i in range(days):
            w = self.weather.get((run_date - timedelta(days=i), 0))
            if w and w["hdd"] is not None:
                vals.append(w["hdd"])
        return sum(vals) / len(vals) if vals else float("nan")


_ROUGE_DAYS_CACHE = {}


def _remaining_rouge_days(target):
    """Jours ouvres restants dans la fenetre Rouge apres `target` (pression du quota)."""
    if target in _ROUGE_DAYS_CACHE:
        return _ROUGE_DAYS_CACHE[target]
    season = calendrier.season_of(target)
    end_year = int(season.split("-")[1])
    end = date(end_year, 3, 31)
    if target > end:
        _ROUGE_DAYS_CACHE[target] = 0
        return 0
    n = 0
    d = target
    while d <= end:
        if rules.rouge_possible(d):
            n += 1
        d += timedelta(days=1)
    _ROUGE_DAYS_CACHE[target] = n
    return n


def build_row(store, run_date, target, state=None, rng=None, use_renewables=True):
    info = store.days.get(target)
    if info is None:
        return None
    horizon = (target - run_date).days
    state = state or store.season_state(run_date)
    w, lead_used = store.weather_for(target, horizon, rng)
    if w is None:
        return None

    doy = target.timetuple().tm_yday
    normal = store.normal.get(doy)
    anomaly = (w["tmean"] - normal) if normal is not None else float("nan")

    wp, _ = store.weather_for(target - timedelta(days=1), max(1, horizon - 1), rng)
    wn, _ = store.weather_for(target + timedelta(days=1), horizon + 1, rng)
    hdd_prev = wp["hdd"] if wp else float("nan")
    hdd_next = wn["hdd"] if wn else float("nan")
    win = [x for x in (hdd_prev, w["hdd"], hdd_next) if x == x]
    hdd_win3 = sum(win) / len(win) if win else float("nan")

    # Couleurs deja connues dans la semaine civile de la cible (uniquement <= run_date)
    week_start = target - timedelta(days=target.weekday())
    rouge_week = blanc_week = 0
    d = week_start
    while d < target:
        if d <= run_date:
            di = store.days.get(d)
            if di and di["color"] == config.ROUGE:
                rouge_week += 1
            elif di and di["color"] == config.BLANC:
                blanc_week += 1
        d += timedelta(days=1)

    # Froid relatif : rang du jour cible dans la fenetre de prevision et dans sa semaine
    window = store.forecast_window(run_date, rng)
    candidates = {d: v for d, v in window.items() if rules.rouge_possible(d)}
    if candidates and target in candidates:
        ref = candidates[target]
        ordered = sorted(candidates.values(), reverse=True)
        rank_window = ordered.index(ref) / max(1, len(ordered) - 1) if len(ordered) > 1 else 0.0
        coldest = ordered[0]
        gap_to_coldest = ref - coldest
        is_coldest_window = int(ref >= coldest - 1e-9)
    else:
        rank_window, gap_to_coldest, is_coldest_window = float("nan"), float("nan"), 0

    week_days = {d: v for d, v in window.items()
                 if d - timedelta(days=d.weekday()) == target - timedelta(days=target.weekday())
                 and rules.rouge_possible(d)}
    if week_days and target in week_days:
        ref_w = week_days[target]
        ordered_w = sorted(week_days.values(), reverse=True)
        rank_week = ordered_w.index(ref_w) / max(1, len(ordered_w) - 1) if len(ordered_w) > 1 else 0.0
        is_coldest_week = int(ref_w >= ordered_w[0] - 1e-9)
    else:
        rank_week, is_coldest_week = float("nan"), 0

    hdd_pct_season = store.season_hdd_percentile(run_date, w["hdd"])

    # Consommation nette : un jour froid mais venteux sollicite peu le parc pilotable.
    if not use_renewables:
        nan = float("nan")
        net_load = wind_index = solar_index = nan
        net_rank_window = net_pct_season = wind_window_mean = nan
        peak_mw_pred = wind_mw_pred = residual_mw = nan
        residual_rank_window = residual_pct_season = residual_vs_season_max = nan
        is_peak_net_window = 0
        net_window = {}
    else:
        net_load, wind_index, solar_index = store.net_load_for(target, horizon, w["hdd"])
        net_window = store.net_load_window(run_date, rng)
        net_candidates = {d: v for d, v in net_window.items() if rules.rouge_possible(d)}
        if net_candidates and target in net_candidates:
            ref_n = net_candidates[target]
            ordered_n = sorted(net_candidates.values(), reverse=True)
            net_rank_window = (ordered_n.index(ref_n) / max(1, len(ordered_n) - 1)
                               if len(ordered_n) > 1 else 0.0)
            is_peak_net_window = int(ref_n >= ordered_n[0] - 1e-9)
        else:
            net_rank_window, is_peak_net_window = float("nan"), 0
        net_pct_season = store.net_load_percentile(run_date, net_load)
        winds = [store.renewables_for(d, (d - run_date).days) for d in net_window]
        winds = [r["wind_index"] for r in winds if r and r["wind_index"] is not None]
        wind_window_mean = sum(winds) / len(winds) if winds else float("nan")

        # Charge residuelle en MW (modele de demande appris sur eCO2mix)
        peak_mw_pred, wind_mw_pred, residual_mw = store.residual_for(target, horizon, w["hdd"])
        res_window = {}
        for d in net_window:
            wd, _ = store.weather_for(d, (d - run_date).days, rng)
            if wd is None or not rules.rouge_possible(d):
                continue
            _, _, res = store.residual_for(d, (d - run_date).days, wd["hdd"])
            if res == res:
                res_window[d] = res
        if res_window and target in res_window:
            ordered_r = sorted(res_window.values(), reverse=True)
            residual_rank_window = (ordered_r.index(res_window[target]) / max(1, len(ordered_r) - 1)
                                    if len(ordered_r) > 1 else 0.0)
        else:
            residual_rank_window = float("nan")
        observed = store.observed_residuals(run_date)
        if len(observed) >= 10 and residual_mw == residual_mw:
            residual_pct_season = float(np.searchsorted(observed, residual_mw) / len(observed))
            residual_vs_season_max = residual_mw - observed[-1]
        else:
            residual_pct_season = residual_vs_season_max = float("nan")

    season_start = calendrier.season_start(state["season"])
    rouge_days_left = _remaining_rouge_days(target)
    days_left_season = (calendrier.season_end(state["season"]) - target).days + 1

    xmas = int((target.month == 12 and target.day >= 20) or (target.month == 1 and target.day <= 3))
    holiday_adj = int(calendrier.is_holiday(target - timedelta(days=1))
                      or calendrier.is_holiday(target + timedelta(days=1)))

    row = [
        horizon,
        target.weekday(),
        int(target.weekday() == 5),
        int(info["is_holiday"]),
        holiday_adj,
        target.month,
        math.sin(2 * math.pi * doy / 365.25),
        math.cos(2 * math.pi * doy / 365.25),
        (target - season_start).days,
        int(target.month in rules.ROUGE_MONTHS),
        xmas,
        w["tmean"], w["tmin"], w["tmax"], w["hdd"],
        lead_used,
        anomaly,
        hdd_prev, hdd_next, hdd_win3,
        state["rouge_used"], state["rouge_left"], state["blanc_used"], state["blanc_left"],
        state["rouge_left"] / rouge_days_left if rouge_days_left else 0.0,
        state["blanc_left"] / days_left_season if days_left_season else 0.0,
        (target - state["last_rouge"]).days if state["last_rouge"] else 999,
        (target - state["last_blanc"]).days if state["last_blanc"] else 999,
        state["rouge_last7"], state["rouge_last14"], state["blanc_last7"],
        rouge_week, blanc_week,
        store.observed_hdd(run_date),
        rank_window, gap_to_coldest, is_coldest_window,
        rank_week, is_coldest_week, hdd_pct_season,
        wind_index, solar_index, net_load, net_rank_window,
        is_peak_net_window, net_pct_season, wind_window_mean,
        peak_mw_pred, wind_mw_pred, residual_mw, residual_rank_window,
        residual_pct_season, residual_vs_season_max,
    ]
    return np.array(row, dtype=float)


def build_dataset(store, start, end, horizons=range(1, config.MAX_HORIZON + 1), seed=0,
                  use_renewables=True):
    """Toutes les paires (R, T) dont la cible est entre start et end et la couleur connue."""
    rng = np.random.default_rng(seed)
    X, y, meta = [], [], []
    for target in calendrier.daterange(start, end):
        info = store.days.get(target)
        if not info or not info["color"]:
            continue
        for h in horizons:
            run_date = target - timedelta(days=h)
            state = store.season_state(run_date)
            row = build_row(store, run_date, target, state, rng, use_renewables)
            if row is None:
                continue
            X.append(row)
            y.append(info["color"])
            meta.append({
                "run_date": run_date, "target": target, "horizon": h,
                "season": info["season"], "weekday": target.weekday(),
                "is_holiday": info["is_holiday"],
                "rouge_left": state["rouge_left"], "blanc_left": state["blanc_left"],
            })
    return np.array(X), np.array(y), meta
