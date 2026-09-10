"""Quelles features servent vraiment, et lesquelles sont du poids mort ?

    python diagnostic_features.py              # groupes puis features une par une
    python diagnostic_features.py --groupes    # groupes seulement (rapide)
    python diagnostic_features.py --reps 10    # plus de repetitions, moins de bruit

Methode : permutation. On melange aleatoirement UNE colonne du jeu de test, ce qui
detruit son information sans toucher a rien d'autre, et on regarde ce que le modele
perd. Une colonne dont la destruction ne coute rien n'apportait rien -- le modele
n'y lisait que du bruit, et la garder ajoute du risque de surapprentissage sans
contrepartie.

Deux mesures, parce qu'elles ne disent pas la meme chose :

  - la LOG-LOSS juge la probabilite. C'est la mesure sensible : elle bouge des que
    la confiance du modele se degrade, meme quand la couleur annoncee ne change pas.
    C'est elle qui tranche.
  - la PRECISION juge la decision finale, apres seuils. Plus parlante, mais grossiere :
    une feature peut ameliorer nettement les probabilites sans faire basculer une
    seule couleur.

Tout est mesure sur les seuls jours ou le Rouge est possible (lundi-vendredi,
novembre a mars, hors feries). Ailleurs la reponse est Bleu d'avance, et une feature
inutile y semblerait excellente.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from sklearn.metrics import log_loss

sys.path.insert(0, str(Path(__file__).parent))
import config
from src import backtest, db, features, model
from src.sources import calendrier

SEASONS = ["2023-2024", "2024-2025", "2025-2026"]
REPETITIONS = 5

# Regroupees par question posee, pas par nom de colonne : on veut savoir d'ou vient
# l'information, avant de savoir quelle variable prise isolement pese le plus. Une
# feature peut sembler inutile seule et porter son groupe avec ses voisines.
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
    "horizon": ["horizon"],
}


def _arg(nom, defaut):
    return int(sys.argv[sys.argv.index(nom) + 1]) if nom in sys.argv else defaut


def scores(gbm, X, y, meta):
    """(log-loss, precision) sur un jeu deja restreint aux jours notables."""
    probs = gbm.predict_proba(X, meta)
    preds = model.decide(probs, gbm.rouge_threshold, gbm.blanc_threshold)
    return (float(log_loss(y, probs, labels=model.CLASSES)),
            float((preds == y).mean()))


def prepare(store, season, seed=0, horizon=None):
    """Entraine sur le passe de la saison, renvoie le jeu de test deja restreint.

    `horizon` restreint le jeu de TEST a une seule echeance. Sans lui, une feature qui
    n'existe qu'a J+1 -- la prevision de consommation de RTE, publiee la veille -- est
    jugee sur un jeu ou elle est NaN neuf fois sur dix : permuter sa colonne ne touche
    qu'un dixieme des lignes, et elle sort mecaniquement en poids mort. Le verdict
    porterait alors sur sa rarete, pas sur sa valeur.
    """
    store.fit_demand_model(calendrier.season_start(season))
    train_end = calendrier.season_start(season) - timedelta(days=1)
    Xtr, ytr, mtr = features.build_dataset(
        store, date(config.FIRST_SEASON, 9, 1), train_end, seed=seed)
    Xte, yte, mte = features.build_dataset(
        store, calendrier.season_start(season), calendrier.season_end(season), seed=seed + 1)
    if not len(Xte):
        return None
    gbm = model.TempoModel(seed=seed).fit(Xtr, ytr, mtr)
    ev = backtest.evaluables(mte)
    if horizon is not None:
        ev = ev & np.array([m["horizon"] == horizon for m in mte])
    if not ev.any():
        return None
    return gbm, Xte[ev], yte[ev], [m for m, k in zip(mte, ev) if k]


def permute(gbm, X, y, meta, colonnes, reps, rng):
    """Pertes par repetition (log-loss, precision) quand ces colonnes sont detruites."""
    base_ll, base_acc = scores(gbm, X, y, meta)
    d_ll, d_acc = [], []
    for _ in range(reps):
        Xp = X.copy()
        for j in colonnes:
            Xp[:, j] = rng.permutation(Xp[:, j])
        ll, acc = scores(gbm, Xp, y, meta)
        d_ll.append(ll - base_ll)      # positif = le modele perd
        d_acc.append(base_acc - acc)   # positif = le modele perd
    return d_ll, d_acc


def _tableau(titre, lignes, seuil_utile):
    """Lignes = [(nom, par_saison, bruit, dacc)], triees par perte moyenne.

    Deux dispersions se cachaient au depart derriere un seul ecart-type, et les
    confondre faisait passer toute mesure pour du bruit :

      - le BRUIT DE PERMUTATION, d'un tirage a l'autre a l'interieur d'une saison.
        C'est le vrai plancher : en dessous, on ne mesure rien.
      - l'ECART ENTRE SAISONS, qui n'est pas du bruit mais de l'heterogeneite reelle.
        Une feature qui sauve un hiver et en abime un autre est instable, ce qui est
        une information en soi -- pas une raison de la declarer nulle.

    D'ou la colonne « pire saison » : une feature n'est jugee fiable que si elle aide
    partout, pas seulement en moyenne.
    """
    print(f"\n{titre}")
    print(f"{'':>22} {'+log-loss':>11} {'bruit':>9} {'pire saison':>12} "
          f"{'-precision':>12}   verdict")
    out = {}
    for nom, par_saison, bruit, dacc in sorted(lignes, key=lambda r: -np.mean(r[1])):
        dll = float(np.mean(par_saison))
        pire = float(np.min(par_saison))
        out[nom] = (dll, bruit, pire)
        if dll <= 2 * bruit and abs(dll) <= 2 * bruit:
            verdict = "indistinct du bruit"
        elif dll < 0:
            verdict = "POIDS MORT"
        elif pire <= 0:
            verdict = "instable (nuit a une saison)"
        elif dll >= seuil_utile * 4:
            verdict = "porteur"
        elif dll >= seuil_utile:
            verdict = "utile"
        else:
            verdict = "marginal"
        print(f"{nom:>22} {dll:>+11.4f} {bruit:>9.4f} {pire:>+12.4f} "
              f"{dacc:>+12.2%}   {verdict}")
    return out


def main():
    reps = _arg("--reps", REPETITIONS)
    horizon = _arg("--horizon", None) if "--horizon" in sys.argv else None
    conn = db.connect()
    store = features.FeatureStore(conn)
    rng = np.random.default_rng(0)

    if horizon is not None:
        print(f"Mesure restreinte a l'echeance J+{horizon} : un dixieme des lignes, "
              f"mais la seule ou certaines features existent.\n")

    prepares = []
    for season in SEASONS:
        print(f"Entrainement pour {season}...", flush=True)
        p = prepare(store, season, horizon=horizon)
        if p:
            prepares.append((season, p))
            gbm, X, y, meta = p
            ll, acc = scores(gbm, X, y, meta)
            print(f"  {len(y)} jours notables — log-loss {ll:.3f}, precision {acc:.1%}",
                  flush=True)
    if not prepares:
        raise SystemExit("aucune saison exploitable : la base est-elle constituee ?")

    def mesure(colonnes):
        """(perte moyenne par saison, bruit de permutation, perte de precision).

        Le bruit est la dispersion A L'INTERIEUR d'une saison, seule dispersion qui
        soit vraiment du hasard ; l'ecart entre saisons est conserve a part, dans la
        liste des moyennes par saison.
        """
        par_saison, bruits, accs = [], [], []
        for _, (gbm, X, y, meta) in prepares:
            a, b = permute(gbm, X, y, meta, colonnes, reps, rng)
            par_saison.append(float(np.mean(a)))
            bruits.append(float(np.std(a)))
            accs += b
        return par_saison, float(np.mean(bruits)), float(np.mean(accs))

    groupes = []
    for nom, noms in GROUPES.items():
        idx = [features.FEATURE_NAMES.index(n) for n in noms
               if n in features.FEATURE_NAMES]
        if idx:
            groupes.append((nom, *mesure(idx)))
    # Un groupe entier detruit coute forcement plus qu'une colonne seule : le seuil
    # d'utilite est proportionne a ce qu'on detruit.
    _tableau("Par GROUPE (le groupe entier est detruit) :", groupes, 0.010)

    if "--groupes" in sys.argv:
        return

    print(f"\nMesure feature par feature ({len(features.FEATURE_NAMES)} colonnes, "
          f"{reps} repetitions)...", flush=True)
    seules = [(nom, *mesure([i])) for i, nom in enumerate(features.FEATURE_NAMES)]
    stats = _tableau("Par FEATURE (une seule colonne detruite) :", seules, 0.002)

    mortes = [n for n, (dll, bruit, _) in stats.items() if dll < 0 and abs(dll) > 2 * bruit]
    instables = [n for n, (dll, bruit, pire) in stats.items()
                 if dll > 2 * bruit and pire <= 0]
    print(f"\n{len(mortes)} feature(s) dont la destruction AMELIORE le modele :")
    print("  " + (", ".join(mortes) if mortes else "aucune"))
    print(f"\n{len(instables)} feature(s) qui aident en moyenne mais nuisent a "
          f"au moins une saison :")
    print("  " + (", ".join(instables) if instables else "aucune"))
    print("\nAttention avant de supprimer : deux features redondantes se couvrent")
    print("l'une l'autre et paraissent toutes deux inutiles prises separement.")
    print("Le tableau par groupe dit si l'information existe ailleurs.")


if __name__ == "__main__":
    main()
