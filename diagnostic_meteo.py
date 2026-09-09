"""La meteo sert-elle vraiment au modele, et a quelle echeance ?

    python diagnostic_meteo.py

Point de depart : sur le backtest, la precision passe de 90,0 % a J+1 a 88,0 % a J+10,
et le rappel Rouge ne bouge pas du tout. C'est anormal. A J+1 la meteo est quasi connue
et RTE a deja tranche ; a J+10 elle n'est qu'une esperance. Un modele qui exploite la
meteo devrait donc etre nettement meilleur a court terme. Une courbe plate laisse deux
hypotheses : soit le modele roule surtout sur le calendrier et l'etat des quotas, soit
le signal meteo est noye.

Methode : on permute aleatoirement chaque groupe de features dans le jeu de test, ce
qui detruit son information sans rien changer d'autre, et on mesure la chute. Un groupe
dont la permutation ne coute rien n'apportait rien.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import config
from src import db, features, model, rules
from src.sources import calendrier

SEASONS = ["2023-2024", "2024-2025", "2025-2026"]
REPETITIONS = 5

# Regroupes par question posee, pas par nom de colonne : ce qu'on veut savoir, c'est
# d'ou vient l'information, pas quelle variable prise isolement pese le plus.
GROUPES = {
    "meteo brute": ["tmean_f", "tmin_f", "tmax_f", "hdd_f", "temp_anomaly",
                    "hdd_prev", "hdd_next", "hdd_win3", "weather_lead_used"],
    "froid relatif": ["hdd_rank_window", "hdd_gap_to_coldest", "is_coldest_window",
                      "hdd_rank_week", "is_coldest_week", "hdd_pct_season",
                      "hdd_observed_7d"],
    "renouvelables": ["wind_index", "solar_index", "net_load", "net_load_rank_window",
                      "is_peak_net_window", "net_load_pct_season", "wind_index_window_mean"],
    "charge residuelle": ["peak_mw_pred", "wind_mw_pred", "residual_mw",
                          "residual_rank_window", "residual_pct_season",
                          "residual_vs_season_max"],
    "offre (nucleaire)": ["nuclear_recent_mw", "nuclear_anomaly_mw", "margin_proxy_mw"],
    "prevision RTE J+1": ["rte_forecast_mw", "rte_forecast_gap"],
    "hiver restant": ["colder_days_ahead", "rouge_budget_ratio"],
    "calendrier": ["weekday", "is_saturday", "is_holiday", "holiday_adjacent", "month",
                   "doy_sin", "doy_cos", "season_day", "in_rouge_window", "xmas_break"],
    "quotas": ["rouge_used", "rouge_left", "blanc_used", "blanc_left", "rouge_pressure",
               "blanc_pressure", "days_since_rouge", "days_since_blanc", "rouge_last7",
               "rouge_last14", "blanc_last7", "rouge_known_week", "blanc_known_week"],
}


def _indices(noms):
    return [features.FEATURE_NAMES.index(n) for n in noms if n in features.FEATURE_NAMES]


def evalue(gbm, X, y, meta, horizons=None):
    """Precision sur les seuls jours ou le Rouge est possible : le denominateur utile."""
    keep = [i for i, m in enumerate(meta)
            if m["target"].month in rules.ROUGE_MONTHS
            and m["target"].weekday() < 5 and not m["is_holiday"]
            and (horizons is None or m["horizon"] in horizons)]
    if not keep:
        return float("nan")
    probs = gbm.predict_proba(X[keep], [meta[i] for i in keep])
    preds = model.decide(probs, gbm.rouge_threshold, gbm.blanc_threshold)
    return float((preds == np.array(y)[keep]).mean())


def main():
    conn = db.connect()
    store = features.FeatureStore(conn)
    rng = np.random.default_rng(0)
    lignes = {}

    for season in SEASONS:
        print(f"Saison {season} :", flush=True)
        store.fit_demand_model(calendrier.season_start(season))
        train_end = calendrier.season_start(season) - timedelta(days=1)
        Xtr, ytr, mtr = features.build_dataset(
            store, date(config.FIRST_SEASON, 9, 1), train_end)
        Xte, yte, mte = features.build_dataset(
            store, calendrier.season_start(season), calendrier.season_end(season))
        if not len(Xte):
            continue
        gbm = model.TempoModel().fit(Xtr, ytr, mtr)

        for label, horizons in (("J+1..J+3", {1, 2, 3}), ("J+8..J+10", {8, 9, 10})):
            base = evalue(gbm, Xte, yte, mte, horizons)
            lignes.setdefault(label, {}).setdefault("_base", []).append(base)
            for nom, colonnes in GROUPES.items():
                idx = _indices(colonnes)
                if not idx:
                    continue
                chutes = []
                for _ in range(REPETITIONS):
                    Xp = Xte.copy()
                    for j in idx:
                        Xp[:, j] = rng.permutation(Xp[:, j])
                    chutes.append(base - evalue(gbm, Xp, yte, mte, horizons))
                lignes[label].setdefault(nom, []).append(float(np.mean(chutes)))
            print(f"  {label} : reference {base:.1%}", flush=True)

    print("\nChute de precision quand le groupe est detruit (jours eligibles au Rouge) :")
    print(f"{'groupe':>20} {'J+1..J+3':>12} {'J+8..J+10':>12}")
    court = lignes.get("J+1..J+3", {})
    long_ = lignes.get("J+8..J+10", {})
    print(f"{'(reference)':>20} {np.mean(court.get('_base', [0])):>11.1%} "
          f"{np.mean(long_.get('_base', [0])):>11.1%}")
    for nom in GROUPES:
        if nom not in court:
            continue
        print(f"{nom:>20} {np.mean(court[nom]):>11.1%} {np.mean(long_.get(nom, [0])):>11.1%}")

    print("\nLecture : si « meteo brute » et « froid relatif » coutent aussi peu a J+1")
    print("qu'a J+10, le modele n'exploite pas la precision de la prevision courte,")
    print("et c'est la qu'il reste du gisement.")


if __name__ == "__main__":
    main()
