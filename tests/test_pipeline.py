"""Verifie le pipeline lui-meme, sur une base synthetique.

Ces tests ne disent rien de la QUALITE du modele -- les donnees sont fabriquees. Ils
verifient ce qui casse en silence : une feature ajoutee sans son nom, une valeur qui
regarde vers l'avenir, un filtre qui ne filtre pas.
"""
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
import fixtures
from src import db, features, model

_STORE = {}


def store():
    """Base synthetique construite une seule fois pour toute la session de test."""
    if "store" not in _STORE:
        path = Path(tempfile.gettempdir()) / "tempo_test_synthetique.db"
        if path.exists():
            path.unlink()
        conn = fixtures.build(path)
        _STORE["conn"] = conn
        _STORE["path"] = path
        s = features.FeatureStore(conn)
        s.fit_demand_model(date(2025, 9, 1))
        _STORE["store"] = s
    return _STORE["store"]


def test_chaque_feature_a_un_nom():
    """Le piege classique : ajouter une colonne a `row` sans l'ajouter aux noms.

    Rien ne planterait -- les colonnes glisseraient simplement d'un cran et le modele
    apprendrait sur des features mal etiquetees.
    """
    s = store()
    row = features.build_row(s, date(2025, 1, 10), date(2025, 1, 15))
    assert row is not None, "aucune ligne construite sur la base synthetique"
    assert len(row) == len(features.FEATURE_NAMES), (
        f"{len(row)} valeurs pour {len(features.FEATURE_NAMES)} noms de features")


def test_la_prevision_rte_reste_cantonnee_a_j1():
    """RTE publie sa prevision la veille : la propager plus loin serait une fuite."""
    s = store()
    idx = features.FEATURE_NAMES.index("rte_forecast_mw")
    target = date(2025, 1, 15)
    proche = features.build_row(s, target - timedelta(days=1), target)
    lointain = features.build_row(s, target - timedelta(days=6), target)
    assert proche[idx] == proche[idx], "la prevision RTE devrait exister a J+1"
    assert lointain[idx] != lointain[idx], "la prevision RTE ne doit pas exister a J+6"


def test_l_hiver_restant_ignore_les_saisons_posterieures():
    """Le coeur de l'honnetete du backtest : ne rien savoir de ce qui n'est pas arrive.

    On calcule la feature, on modifie ensuite lourdement une saison POSTERIEURE, et on
    recalcule : la valeur doit etre identique au bit pres.
    """
    s = store()
    target = date(2023, 1, 17)
    avant = s.colder_days_ahead(target, 60000.0)

    posterieurs = [d for d in s.conso if d >= date(2024, 9, 1)]
    assert posterieurs, "il faut une saison posterieure pour que le test ait un sens"
    for d in posterieurs:
        s.conso[d]["peak_mw"] = 999999.0
    s._climat_residual_cache.clear()
    apres = s.colder_days_ahead(target, 60000.0)

    assert avant == apres, (
        f"la feature a bouge ({avant} -> {apres}) alors que seule une saison "
        "posterieure a change : il y a une fuite temporelle")


def test_les_seuils_promeuvent_bien_leur_couleur():
    """Sans seuil, le Blanc ne sort jamais de l'argmax face a 82 % de Bleu."""
    probs = np.array([[0.55, 0.45, 0.00],   # Blanc sous l'argmax mais au-dessus du seuil
                      [0.60, 0.10, 0.30],   # Rouge minoritaire mais assez probable
                      [0.90, 0.05, 0.05]])  # rien a promouvoir
    out = model.decide(probs, rouge_threshold=0.25, blanc_threshold=0.40)
    assert list(out) == [config.BLANC, config.ROUGE, config.BLEU], list(out)


def test_le_rouge_prime_sur_le_blanc():
    """Quand les deux seuils sont franchis, c'est le Rouge qui doit gagner."""
    probs = np.array([[0.10, 0.55, 0.35]])
    out = model.decide(probs, rouge_threshold=0.25, blanc_threshold=0.40)
    assert out[0] == config.ROUGE


def test_une_source_absente_n_empeche_pas_d_entrainer():
    """Une colonne entierement vide ne doit pas faire echouer l'entrainement.

    Vecu en production : la colonne `nucleaire_mw` venait d'etre ajoutee et n'etait
    remplie nulle part, les trois features d'offre sortaient donc NaN partout, et le
    binning de scikit-learn s'effondrait sur « window shape cannot be larger than
    input array shape ». Une source pas encore collectee doit rendre sa feature sans
    effet, pas empecher le modele de demarrer.
    """
    s = store()
    X, y, meta = features.build_dataset(
        s, date(2021, 9, 1), date(2023, 8, 31), horizons=range(1, 4))
    assert len(X) > 100

    # On vide integralement trois colonnes, comme si leur source manquait. Elles
    # sont choisies HORS de config.EXCLUDED_FEATURES : sur des colonnes deja
    # neutralisees par configuration, le test passerait meme si la detection des
    # colonnes vides etait cassee.
    cibles = ("wind_index", "solar_index", "net_load")
    assert not set(cibles) & set(config.EXCLUDED_FEATURES), (
        "ces features sont exclues par config : le test ne prouverait plus rien")
    morts = [features.FEATURE_NAMES.index(n) for n in cibles]
    X = X.copy()
    X[:, morts] = np.nan
    assert np.isnan(X[:, morts]).all()

    # n_seeds=1 : ce test verifie la mecanique, pas la qualite. L'ensemble
    # complet multiplierait par cinq un temps de test sans rien y ajouter.
    gbm = model.TempoModel(n_seeds=1).fit(X, y, meta)
    probs = gbm.predict_proba(X[:5], meta[:5])
    assert probs.shape == (5, 3)
    assert np.allclose(probs.sum(axis=1), 1.0)
    neutralisees = set(gbm.dead_columns.nonzero()[0])
    assert set(morts) <= neutralisees, "les colonnes vides n'ont pas ete detectees"


def test_un_modele_d_un_autre_format_est_refuse():
    """Un depickle est un contrat : le rompre en silence coute une journee de production.

    Vecu deux fois. La premiere, une feature ajoutee et un modele attendant l'ancien
    nombre de colonnes. La seconde, la moyenne de plusieurs graines transformant un
    attribut en liste, SANS toucher a une seule feature -- le controle des noms n'y
    voyait rien et la collecte a plante sur « CalibratedClassifierCV object is not
    iterable ». D'ou un numero de format, verifie avant tout le reste.
    """
    import joblib
    from src import predict
    ancien = predict.MODEL_PATH
    try:
        chemin = Path(tempfile.gettempdir()) / "tempo_test_modele.joblib"
        predict.MODEL_PATH = chemin
        joblib.dump({"model": None, "version": "v0", "features": features.FEATURE_NAMES,
                     "format": predict.MODEL_FORMAT - 1}, chemin)
        try:
            predict.load()
            raise AssertionError("un format perime a ete accepte")
        except predict.ModeleObsolete as exc:
            assert "format" in str(exc)
        assert predict.besoin_d_entrainement() is True

        # Et le format courant, lui, doit passer le controle de format.
        joblib.dump({"model": None, "version": "v1", "features": features.FEATURE_NAMES,
                     "format": predict.MODEL_FORMAT}, chemin)
        assert predict.besoin_d_entrainement() is False
    finally:
        predict.MODEL_PATH = ancien


def test_les_periodes_sont_embiquees():
    """« eligibles » est inclus dans « hiver », lui-meme inclus dans « tout »."""
    import app as web
    conn = _STORE.get("conn") or store() and _STORE["conn"]
    rows = conn.execute("SELECT COUNT(*) FROM days WHERE color IS NOT NULL").fetchone()[0]
    assert rows > 0
    assert web.period_clause("all") == ""
    assert "IN (" in web.period_clause("hiver")
    assert "weekday" in web.period_clause("eligibles")
    # Une periode inconnue ne doit rien filtrer plutot que de casser la requete.
    assert web.period_clause("nimporte quoi") == ""


def test_le_perimetre_de_notation_est_le_meme_partout():
    """Le backtest et l'API doivent designer exactement les memes jours.

    Le perimetre existe en deux exemplaires : `backtest.evaluables()` en Python, pour
    noter le modele, et `app.period_clause('eligibles')` en SQL, pour la page. S'ils
    divergeaient, le pourcentage affiche ne serait plus celui qui a ete mesure -- et
    rien ne le signalerait.
    """
    import app as web
    from src import backtest, rules
    conn = _STORE.get("conn") or store() and _STORE["conn"]

    par_sql = {r["date"] for r in conn.execute(
        f"""SELECT p.target_date AS date FROM days d
            JOIN (SELECT date AS target_date FROM days) p ON p.target_date = d.date
            WHERE d.color IS NOT NULL {web.period_clause('eligibles')}""")}
    par_python = {d.isoformat() for d, info in _STORE["store"].days.items()
                  if info["color"] is not None
                  and rules.rouge_possible(d, info["is_holiday"])}
    assert par_sql == par_python, (
        f"{len(par_sql ^ par_python)} jours differents entre le filtre SQL et le "
        f"predicat Python, par exemple {sorted(par_sql ^ par_python)[:3]}")

    # Et `evaluables()` doit s'appuyer sur le meme predicat.
    metas = [{"target": d, "is_holiday": info["is_holiday"]}
             for d, info in sorted(_STORE["store"].days.items())]
    ev = backtest.evaluables(metas)
    assert {m["target"].isoformat() for m, k in zip(metas, ev) if k} >= par_python


def test_la_fiabilite_compte_ce_qu_elle_annonce():
    """Les tranches doivent refleter les probabilites, pas l'ordre des lignes."""
    import app as web
    lignes = ([{"p_rouge": 0.05, "actual": 1}] * 30 +
              [{"p_rouge": 0.85, "actual": 3}] * 30)
    bins = web.reliability(lignes)
    assert len(bins) == 2, bins
    basse, haute = bins
    assert basse["observed"] == 0.0 and haute["observed"] == 1.0
    assert abs(basse["predicted"] - 0.05) < 1e-9
    assert abs(haute["predicted"] - 0.85) < 1e-9
