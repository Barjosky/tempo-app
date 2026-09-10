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

        # Un attribut ajoute a TempoModel doit suffire, SANS toucher au numero de
        # format : c'est l'oubli qui a provoque la troisieme panne de production.
        gbm = model.TempoModel()
        complete = predict.structure(gbm)
        joblib.dump({"model": gbm, "version": "v1", "features": features.FEATURE_NAMES,
                     "format": predict.MODEL_FORMAT,
                     "structure": [a for a in complete if a != "clf"]}, chemin)
        try:
            predict.load()
            raise AssertionError("une structure amputee a ete acceptee")
        except predict.ModeleObsolete as exc:
            assert "structure" in str(exc)

        # Et un modele conforme sur les deux points doit passer.
        joblib.dump({"model": gbm, "version": "v2", "features": features.FEATURE_NAMES,
                     "format": predict.MODEL_FORMAT, "structure": complete}, chemin)
        assert predict.besoin_d_entrainement() is False
    finally:
        predict.MODEL_PATH = ancien


def test_le_quota_force_les_derniers_jours():
    """Quand le quota ne tient plus dans les jours restants, la meteo n'a plus voix.

    Vecu en 2025-2026 : treize des vingt-deux Rouge places en mars, jusqu'au 31.
    Le modele, entraine sur des hivers consommes des fevrier, annonçait Bleu. Ce
    n'est pourtant pas une prevision mais une arithmetique -- d'ou une contrainte
    plutot qu'un apprentissage.
    """
    from src import model, rules
    # 25 mars 2026 : un mercredi, et il ne reste que cinq jours ouvres avant le 31.
    cible = date(2026, 3, 25)
    assert rules.remaining_rouge_days(cible) == 5, rules.remaining_rouge_days(cible)

    meta = [{"target": cible, "is_holiday": False, "rouge_left": 5, "blanc_left": 10}]
    probs = np.array([[0.90, 0.08, 0.02]])   # le modele voyait Bleu
    out = model.constrain(probs, meta)
    assert out[0][2] == 1.0, f"le Rouge devait etre impose, obtenu {out[0]}"

    # Avec de la marge, rien n'est impose : la prevision reprend la main.
    meta[0]["rouge_left"] = 2
    assert model.constrain(probs, meta)[0][2] < 0.10


def test_une_regle_plus_forte_prime_sur_le_quota():
    """Un dimanche reste Bleu meme si le quota est a court de jours.

    La contrainte de quota impose ; le masque contractuel interdit. L'interdit doit
    gagner, sinon on annoncerait un Rouge un jour ou il n'en tombe jamais.
    """
    from src import model
    dimanche = date(2026, 3, 29)
    assert dimanche.weekday() == 6
    meta = [{"target": dimanche, "is_holiday": False, "rouge_left": 5, "blanc_left": 10}]
    out = model.constrain(np.array([[0.30, 0.20, 0.50]]), meta)
    assert out[0][2] == 0.0, f"un dimanche ne peut pas etre Rouge, obtenu {out[0]}"
    assert abs(out[0].sum() - 1.0) < 1e-9


def test_le_profil_de_saison_tourne():
    """Les scripts d'analyse n'avaient aucun filet : celui-ci a casse en CI.

    `diagnostic_saison.py` appelait encore un compteur de jours deplace de features
    vers rules. Rien ne l'a vu -- ni les tests, ni l'import, puisque l'erreur ne
    survient qu'a l'execution. Ce profil ne coute qu'un peu d'arithmetique de
    calendrier sur la base synthetique : autant le passer a chaque fois.
    """
    import diagnostic_saison
    s = store()
    saisons = list(diagnostic_saison.saisons_completes(s))
    assert saisons, "la base synthetique devrait contenir des saisons completes"
    saison, jours = saisons[0]
    p = diagnostic_saison.profil(s, saison, jours)
    assert p["n"] == config.QUOTA_ROUGE, p["n"]
    assert p["marge_min"] >= 0, "la marge ne peut pas etre negative : le quota tiendrait pas"
    assert p["premier"] <= p["dernier"]


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


def test_l_empreinte_des_assets_suit_leur_contenu():
    """Un asset modifie doit changer l'URL, sinon le navigateur sert l'ancien.

    C'est le defaut qui a fait croire que la mise en ligne n'avait pas eu lieu : le
    serveur publiait la nouvelle page, le navigateur gardait l'ancien script. La page
    servie etait alors un melange des deux, sans qu'aucun outil ne le signale.
    """
    import re
    import shutil
    import tempfile
    import export_static

    racine = Path(__file__).parent.parent
    with tempfile.TemporaryDirectory() as tmp:
        faux = Path(tmp)
        for nom in ("index.html",) + export_static.ASSETS:
            shutil.copy(racine / nom, faux / nom)

        # Le fichier du depot est deja estampille : on repart de l'etat vierge, sans
        # quoi le test mesurerait l'estampille d'hier au lieu du mecanisme.
        vierge = re.sub(r"\?v=[0-9a-f]+", "",
                        (faux / "index.html").read_text(encoding="utf-8"))
        (faux / "index.html").write_text(vierge, encoding="utf-8")
        for nom in export_static.ASSETS:
            assert f'{nom}"' in vierge or f"{nom}'" in vierge, (
                f"index.html ne reference pas {nom} : le test ne prouverait rien")

        assert export_static.estampiller(faux), "premier passage : rien estampille"
        premier = (faux / "index.html").read_text(encoding="utf-8")
        for nom in export_static.ASSETS:
            assert f'{nom}?v=' in premier, f"{nom} n'est pas estampille"

        # Idempotent : a contenu egal, aucune reecriture, donc aucun commit inutile.
        assert not export_static.estampiller(faux), "reestampille sans changement"

        # Et le contenu change doit changer l'empreinte -- c'est tout l'interet.
        (faux / "app.js").write_text("// autre chose", encoding="utf-8")
        assert export_static.estampiller(faux)
        apres = (faux / "index.html").read_text(encoding="utf-8")
        assert apres != premier, "l'empreinte n'a pas suivi le contenu"
        # Une seule empreinte par asset : pas d'accumulation de ?v= a chaque passage.
        assert apres.count("app.js?v=") == 1 and apres.count("?v=") == 2


def test_les_deux_pressions_de_quota_sont_comparables():
    """Blanc et Rouge doivent se mesurer sur la meme fenetre, sinon leur rapport ment.

    `blanc_pressure` comptait les jours de CALENDRIER jusqu'au 31 aout, quand
    `rouge_pressure` compte les jours ELIGIBLES jusqu'au 31 mars. Au 13 mars 2026 cela
    faisait 147 jours contre 13 : un facteur dix, qui rendait tout arbitrage entre les
    deux quotas illisible.
    """
    from src import rules

    mars = date(2026, 3, 13)
    fin_hiver = date(2026, 3, 31)

    # Aucun dimanche ne doit entrer dans le compte des jours Blanc.
    debut = date(2026, 3, 1)
    dimanches = sum(1 for d in _jours(debut, fin_hiver) if d.weekday() == 6)
    attendu = (fin_hiver - debut).days + 1 - dimanches
    assert rules.remaining_blanc_days(debut, fin_hiver) == attendu

    # Sur la fenetre hivernale, les deux comptes sont du meme ordre ; sur la saison
    # entiere, non. C'est precisement ce qui rendait le rapport inutilisable.
    hiver = rules.remaining_blanc_days(mars, fin_hiver)
    saison = rules.remaining_blanc_days(mars)
    rouge = rules.remaining_rouge_days(mars)
    assert rouge <= hiver <= 2 * rouge, (rouge, hiver)
    assert saison > 5 * hiver, (saison, hiver)

    # Et le rapport doit disparaitre hors fenetre plutot que de valoir zero : passe le
    # 31 mars aucun Rouge n'est possible, l'arbitrage n'a plus d'objet.
    i = features.FEATURE_NAMES.index("quota_arbitrage")
    store = _STORE["store"]
    for cible, dedans in ((date(2026, 1, 15), True), (date(2026, 6, 15), False)):
        run = cible - timedelta(days=3)
        etat = store.season_state(run)
        ligne = features.build_row(store, run, cible, etat,
                                   np.random.default_rng(0), True)
        if ligne is None:
            continue
        # bool() explicite : `ligne[i] == ligne[i]` rend un booleen NumPy, et
        # `np.True_ is True` vaut faux -- le test passerait a cote de son sujet.
        fini = bool(ligne[i] == ligne[i])    # False si NaN
        assert fini is dedans, f"{cible} : arbitrage {'attendu' if dedans else 'de trop'}"


def _jours(debut, fin):
    d = debut
    while d <= fin:
        yield d
        d += timedelta(days=1)


def test_un_cache_de_probabilites_perime_est_detecte():
    """Un seuil choisi sur les probabilites d'un autre modele ne veut rien dire.

    Troisieme incarnation de la meme lecon : le format du modele, la structure de ses
    attributs, et maintenant le cache de probabilites. A chaque fois, le controle qui
    reposait sur la memoire de celui qui modifie n'a rien vu venir.
    """
    import analyse_seuils

    with tempfile.TemporaryDirectory() as tmp:
        chemin = Path(tmp) / "probs.npz"
        assert analyse_seuils.cache_perime(chemin)[0], "un cache absent doit etre perime"

        # Un cache ecrit avec la configuration courante est accepte...
        np.savez(chemin, signature=np.array(analyse_seuils.signature()))
        perime, raison = analyse_seuils.cache_perime(chemin)
        assert not perime, raison

        # ...et cesse de l'etre des qu'un reglage du modele bouge.
        avant = config.FORCE_QUOTA_ROUGE
        try:
            config.FORCE_QUOTA_ROUGE = not avant
            perime, raison = analyse_seuils.cache_perime(chemin)
            assert perime, "un changement de configuration doit perimer le cache"
            assert raison, "la raison doit etre lisible"
        finally:
            config.FORCE_QUOTA_ROUGE = avant

        # Un cache d'avant ce controle n'a pas de signature : perime lui aussi.
        np.savez(chemin, autre=np.array([1.0]))
        assert analyse_seuils.cache_perime(chemin)[0]


def test_le_dossier_des_rapports_se_cree_tout_seul():
    """`reports/` n'existe pas sur un runner neuf, et numpy ne le cree pas.

    Un script a calcule cinq saisons -- six minutes -- avant d'echouer sur son
    `np.savez` final, faute de dossier parent. Deux scripts ecrivaient au meme endroit,
    l'un creant le dossier et l'autre non ; c'est celui qui ne le creait pas qui
    tournait en premier. Passer par `config.reports_path` rend l'oubli impossible.
    """
    ancien = config.REPORTS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        try:
            config.REPORTS_DIR = Path(tmp) / "absent" / "reports"
            assert not config.REPORTS_DIR.exists()
            chemin = config.reports_path("essai.npz")
            assert chemin.parent.is_dir(), "le dossier aurait du etre cree"
            # Et l'ecriture reelle doit passer, pas seulement le mkdir.
            np.savez(chemin, x=np.array([1.0]))
            assert chemin.exists()
        finally:
            config.REPORTS_DIR = ancien
