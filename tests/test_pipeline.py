"""Verifie le pipeline lui-meme, sur une base synthetique.

Ces tests ne disent rien de la QUALITE du modele -- les donnees sont fabriquees. Ils
verifient ce qui casse en silence : une feature ajoutee sans son nom, une valeur qui
regarde vers l'avenir, un filtre qui ne filtre pas.
"""
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
import fixtures
import ingest_rte
from src import cadence, db, features, model
from src.sources import calendrier, rte

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
    par_python = {d.isoformat() for d, info in store().days.items()
                  if info["color"] is not None
                  and rules.rouge_possible(d, info["is_holiday"])}
    assert par_sql == par_python, (
        f"{len(par_sql ^ par_python)} jours differents entre le filtre SQL et le "
        f"predicat Python, par exemple {sorted(par_sql ^ par_python)[:3]}")

    # Et `evaluables()` doit s'appuyer sur le meme predicat.
    metas = [{"target": d, "is_holiday": info["is_holiday"]}
             for d, info in sorted(store().days.items())]
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
    base = store()
    for cible, dedans in ((date(2026, 1, 15), True), (date(2026, 6, 15), False)):
        run = cible - timedelta(days=3)
        etat = base.season_state(run)
        ligne = features.build_row(base, run, cible, etat,
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


def test_l_heure_affichee_est_celle_du_workflow():
    """La page annonce une heure de mise a jour ; elle doit etre la vraie.

    `config.SCHEDULES_UTC` est une copie : la verite est le `cron` du workflow, qu'une
    page statique ne peut pas lire. Une copie non verrouillee derive -- il suffit de
    changer l'horaire du workflow sans penser a l'autre ligne, et la page ment sans
    que rien ne le signale. C'est la meme classe de probleme que le format du modele.
    """
    import re

    yml = (Path(__file__).parent.parent / ".github/workflows/quotidien.yml").read_text(
        encoding="utf-8")
    crons = re.findall(r'^\s*-\s*cron:\s*"([^"]+)"', yml, re.M)
    assert crons, "aucun horaire trouve dans le workflow"
    attendus = []
    for c in crons:
        minute, heure = c.split()[:2]
        attendus.append(f"{int(heure):02d}:{int(minute):02d}")
    assert sorted(config.SCHEDULES_UTC) == sorted(attendus), (
        f"config.SCHEDULES_UTC vaut {config.SCHEDULES_UTC} alors que le workflow "
        f"tourne a {attendus} UTC")


def test_chaque_echeance_est_jugee_par_son_modele():
    """Avec une coupure d'echeance, une ligne courte et une ligne longue doivent etre
    jugees par DEUX modeles differents.

    L'astuce du test : la meme ligne de features est presentee deux fois, en ne
    changeant que l'echeance declaree dans le meta. Si l'aiguillage fonctionne, les
    deux probabilites different -- puisque seules deux modeles distincts peuvent
    expliquer un ecart a entree identique. Sans cette precaution, un test verifierait
    seulement que le code ne plante pas.
    """
    base = store()
    X, y, meta = features.build_dataset(
        base, date(config.FIRST_SEASON, 9, 1), date(2024, 8, 31))

    gbm = model.TempoModel(n_seeds=1, horizon_split=3).fit(X, y, meta)
    assert gbm.clf_court is not None, "les deux bandes auraient du etre entrainees"

    # Des lignes d'HIVER : sur un jour de septembre le masque contractuel ecrase les
    # deux sorties a la meme valeur, et le test ne prouverait plus rien.
    #
    # Il en faut PLUSIEURS, et hors jours feries. La version precedente n'en jugeait
    # qu'une seule et tombait sur le 1er janvier -- ferie, donc Rouge contractuellement
    # impossible, donc les deux bandes forcees a la meme sortie. Elle annoncait alors
    # « l'aiguillage ne fait rien » alors qu'il marchait : mesure sur quarante lignes
    # d'hiver, l'ecart median entre les deux bandes est de 0,21.
    lignes = [k for k, m in enumerate(meta)
              if m["target"].month == 1 and m["target"].weekday() < 5
              and not m["is_holiday"]][:40]
    assert len(lignes) >= 10, "pas assez de jours d'hiver ouvres dans la base d'essai"
    ecarts = [float(np.abs(gbm.predict_proba(X[k:k + 1], [dict(meta[k], horizon=2)])
                           - gbm.predict_proba(X[k:k + 1], [dict(meta[k], horizon=7)])).max())
              for k in lignes]
    differents = sum(1 for e in ecarts if e > 1e-6)
    assert differents >= len(lignes) // 2, (
        f"seulement {differents}/{len(lignes)} lignes distinguent les deux echeances : "
        "l'aiguillage ne fait rien")

    # Et sans coupure, la meme entree doit donner la meme sortie aux deux echeances.
    plat = model.TempoModel(n_seeds=1, horizon_split=None).fit(X, y, meta)
    assert plat.clf_court is None
    k = lignes[0]
    assert np.allclose(plat.predict_proba(X[k:k + 1], [dict(meta[k], horizon=2)]),
                       plat.predict_proba(X[k:k + 1], [dict(meta[k], horizon=7)])), (
        "sans coupure, l'echeance du meta ne devrait rien changer")


def _palier(ident, version, publie, debut, fin, mw,
            fuel="NUCLEAR", nature="PLANNED", statut="ACTIVE"):
    return {
        "identifier": ident, "version": version, "palier_start": debut,
        "palier_end": fin, "publication_date": publie, "fuel_type": fuel,
        "unavailability_type": nature, "event_status": statut, "unit_name": "UNITE",
        "installed_mw": 1300.0, "unavailable_mw": mw, "fetched_at": "",
    }


def _base_indispo(lignes):
    conn = db.connect(":memory:")
    db.init_db(conn)
    db.upsert_unavailabilities(conn, lignes)
    return features.IndispoStore(conn, amorce=0)


def test_une_revision_ne_remonte_pas_le_temps():
    """Le test qui decide de l'honnetete du backtest.

    RTE republie un arret a chaque revision : prolonge, aggrave, parfois annule. Si
    l'etat du 11 janvier contenait deja la revision du 15, le modele « saurait » a
    l'avance qu'un reacteur va rester a l'arret -- et le backtest annoncerait une
    precision que la production n'atteindra jamais.
    """
    s = _base_indispo([
        _palier("A", 1, "2020-12-01", "2021-01-10", "2021-01-20", 900.0),
        _palier("A", 2, "2021-01-15", "2021-01-10", "2021-01-31", 1300.0),
    ])
    avant = s.etat(date(2021, 1, 11), date(2021, 1, 16))[0]
    apres = s.etat(date(2021, 1, 16), date(2021, 1, 16))[0]
    assert avant == 900.0, f"la v1 seule vaut 900 MW, obtenu {avant}"
    assert apres == 1300.0, f"la v2 connue le 16 vaut 1300 MW, obtenu {apres}"
    # Et le 26 janvier, que seule la v2 couvre : invisible depuis le 11.
    assert s.etat(date(2021, 1, 11), date(2021, 1, 21))[0] == 0.0


def test_un_arret_annule_ne_retire_aucune_puissance():
    """DISMISSED = arret annule. Le compter ferait perdre au parc une puissance
    qu'il n'a jamais perdue, et gonflerait la tension apparente de la journee."""
    s = _base_indispo([
        _palier("C", 1, "2020-12-05", "2021-01-14", "2021-01-18", 500.0,
                statut="DISMISSED"),
        _palier("D", 1, "2020-12-05", "2021-01-14", "2021-01-18", 200.0,
                fuel="FOSSIL_GAS", nature="UNPLANNED"),
    ])
    nuc, total, fortuit = s.etat(date(2021, 1, 13), date(2021, 1, 16))
    assert nuc == 0.0, f"l'arret annule ne doit rien retirer, obtenu {nuc}"
    assert total == 200.0 and fortuit == 200.0


def test_les_paliers_priment_sur_la_fenetre_entiere():
    """Un arret long ne retire pas la meme puissance du premier au dernier jour.
    Retenir la puissance maximale sur toute la fenetre la surestimerait."""
    evenement = {
        "identifier": "E", "version": 1, "publication_date": "2021-01-01T10:00:00Z",
        "start_date": "2021-01-05T00:00:00Z", "end_date": "2021-01-15T00:00:00Z",
        "fuel_type": "NUCLEAR", "unavailability_type": "PLANNED",
        "event_status": "ACTIVE", "affected_asset_or_unit_installed_capacity": 1300,
        "values": [
            {"start_date": "2021-01-05T00:00:00Z", "end_date": "2021-01-09T00:00:00Z",
             "unavailable_capacity": 1300},
            {"start_date": "2021-01-10T00:00:00Z", "end_date": "2021-01-15T00:00:00Z",
             "unavailable_capacity": 400},
        ],
    }
    lignes = rte.paliers(evenement)
    assert len(lignes) == 2, f"{len(lignes)} palier(s) au lieu de 2"
    s = _base_indispo(lignes)
    tot = s.etat(date(2021, 1, 4), date(2021, 1, 12))[0]
    assert tot == 400.0, f"le second palier vaut 400 MW, obtenu {tot}"


def test_le_jeton_de_continuation_ne_part_pas_en_parametre():
    """RTE l'attend dans un en-tete et refuse tout parametre inconnu : le laisser
    dans la query string ferait echouer toute collecte des la deuxieme page."""
    vus = {}

    def faux_urlopen(req, timeout=None):
        vus["url"] = req.full_url
        raise RuntimeError("stop")

    original = rte.urllib.request.urlopen
    rte.urllib.request.urlopen = faux_urlopen
    rte._jeton["valeur"], rte._jeton["expire"] = "factice", 9e18
    try:
        try:
            rte._get("/generation_unavailabilities",
                     {"start_date": "x", "_suite": "JETON-INTERNE"}, retries=1)
        except Exception:
            pass
    finally:
        rte.urllib.request.urlopen = original
        rte._jeton["valeur"], rte._jeton["expire"] = None, 0.0
    assert "JETON-INTERNE" not in vus.get("url", ""), (
        f"le jeton de continuation a fuite dans l'URL : {vus.get('url')}")


def test_la_fenetre_de_publication_ne_va_jamais_dans_le_futur():
    """RTE refuse une date de publication future, et le refus a coute un run entier.

    La collecte demandait « jusqu'a demain » : les 81 fenetres d'historique sont
    passees, la derniere a rendu UNADINFO_GENUN_F02, et l'echec a emporte la prevision
    du jour avec lui. Le controle porte sur la borne elle-meme, pas sur le souvenir
    d'avoir lu le message d'erreur.
    """
    horloge = datetime(2026, 9, 11, 12, 30, 0, tzinfo=timezone.utc)
    fin = ingest_rte.borne_de_fin(horloge)
    assert fin < horloge, f"la borne de fin doit etre dans le passe, obtenu {fin}"
    # Et elle doit rester dans la journee : reculer jusqu'a minuit perdrait les
    # declarations de la matinee, c'est-a-dire les avaries fortuites.
    assert fin.date() == horloge.date(), f"borne reculee d'un jour entier : {fin}"


def test_les_dates_envoyees_a_rte_portent_le_suffixe_z():
    """Le decalage explicite (« +02:00 ») est refuse : UNADINFO_GENUN_F03."""
    jour = rte._horodatage(date(2026, 9, 11))
    instant = rte._horodatage(datetime(2026, 9, 11, 12, 30, 5, tzinfo=timezone.utc))
    for rendu in (jour, instant):
        assert rendu.endswith("Z"), f"format refuse par RTE : {rendu}"
        assert "+" not in rendu, f"decalage explicite refuse par RTE : {rendu}"
    assert jour.endswith("T00:00:00Z") and instant.endswith("T12:30:05Z")


# Les quatre passages programmes reellement observes, en UTC. Ils servent de reference
# a plusieurs tests : ce sont des mesures, pas un jeu d'essai invente.
PASSAGES_REELS = [
    "2026-09-09T14:41:54+00:00",   # cron 10:30 -> 4 h 11 de retard
    "2026-09-10T14:32:06+00:00",   # cron 10:30 -> 4 h 02
    "2026-09-11T14:30:53+00:00",   # cron 10:30 -> 4 h 00
    "2026-09-11T19:27:58+00:00",   # cron 17:00 -> 2 h 27
]


def test_la_page_annonce_l_heure_obtenue_pas_l_heure_demandee():
    """Le compte a rebours a menti pendant des heures, et c'est ce test qui l'empeche.

    Il visait le cron (10h30 UTC) quand les passages partent a 14h30. Il tombait a zero,
    affichait « en cours » un quart d'heure, puis repartait vers le passage suivant --
    alors que rien n'avait bouge. On verrouille donc l'ecart : l'heure annoncee doit
    suivre la MESURE, pas la demande.
    """
    vu = cadence.observee(PASSAGES_REELS)
    matin = vu["passages"][0]
    assert "10:30" not in str(vu), "l'horaire DEMANDE ne doit pas ressortir de la mesure"
    assert matin["attendu_utc"] == "14:32", f"mediane attendue 14:32, obtenu {matin}"


def test_sans_assez_de_mesures_on_ne_promet_rien():
    """Le passage du soir n'a qu'une observation : annoncer une heure precise dessus
    serait remplacer une promesse fausse par une autre."""
    vu = cadence.observee(PASSAGES_REELS)
    soir = vu["passages"][1]
    assert soir["mesures"] == 1
    assert not soir["fiable"], "une seule mesure ne fait pas une cadence"
    # Mais le matin, lui, est mesurable : un horaire mal connu ne doit pas priver
    # la page du compte a rebours de l'autre.
    assert vu["fiable"], "au moins un passage fiable devrait suffire"


def test_un_horaire_trop_disperse_est_declare_non_fiable():
    """Une mediane sur des heures qui sautent de trois heures ne prevoit rien."""
    # Toutes a moins de deux heures de la suivante : un seul rendez-vous, tres etale.
    erratiques = ["2026-09-0%dT%02d:%02d:00+00:00" % (j, h, m) for j, h, m in
                  ((1, 11, 0), (2, 12, 30), (3, 13, 50), (4, 12, 10), (5, 11, 40))]
    vu = cadence.observee(erratiques)
    assert vu["passages"][0]["mesures"] == 5
    assert not vu["passages"][0]["fiable"], "dispersion de 4 h declaree fiable"


def test_un_essai_manuel_ne_fausse_pas_la_cadence():
    """Un `workflow_dispatch` lance a 3 h du matin n'a rien a dire sur la cadence
    quotidienne. Sans ce filtre, il tirerait la mediane n'importe ou."""
    conn = db.connect(":memory:")
    db.init_db(conn)
    for horodatage, declencheur in (
            ("2026-09-09T14:41:54+00:00", "schedule"),
            ("2026-09-10T14:32:06+00:00", "schedule"),
            ("2026-09-10T03:12:00+00:00", "workflow_dispatch"),
            ("2026-09-11T14:30:53+00:00", "schedule")):
        db.enregistrer_passage(conn, horodatage, horodatage[:10], declencheur)
    vus = db.passages_reguliers(conn)
    assert len(vus) == 3, f"l'essai manuel n'a pas ete ecarte : {vus}"
    assert cadence.observee(vus)["passages"][0]["attendu_utc"] == "14:32"


def test_l_amorce_s_efface_quand_la_base_mesure_seule():
    """L'amorce evite deux jours sans cadence au demarrage. Mais un releve fige dans
    le code ne doit pas continuer a peser une fois que le systeme se mesure lui-meme :
    sinon la page annoncerait encore, dans six mois, l'horaire de septembre."""
    assert cadence.avec_amorce([]) == cadence.AMORCE, (
        "sans aucune observation, l'amorce devrait servir")
    propres = ["2027-01-%02dT09:00:00+00:00" % j for j in range(1, 8)]
    assert cadence.avec_amorce(propres) == propres, (
        "l'amorce pese encore alors que la base a de quoi mesurer seule")


def test_un_reentrainement_ne_dedouble_pas_les_predictions():
    """La panne du 11 septembre 2026 : 21 cartes pour 10 jours.

    `model_version` fait partie de la cle de la table, donc reentrainer un jour ou des
    predictions existent deja n'ecrase rien -- ca ajoute une serie parallele. La page
    affichait chaque date deux fois et, plus grave, l'historique comptait six paires
    (date, echeance) en double sur seize : le taux de reussite publie portait sur des
    lignes dedoublees.

    La vue `predictions_a_jour` ne garde que la derniere serie. Le test verrouille la
    vue, pas la discipline de filtrer dans chaque requete.
    """
    conn = db.connect(":memory:")
    db.init_db(conn)
    commun = dict(run_date="2026-09-11", target_date="2026-09-12", horizon=1,
                  p_bleu=0.9, p_blanc=0.07, p_rouge=0.03, predicted_color=1,
                  is_official=0)
    db.insert_predictions(conn, [
        dict(commun, run_datetime="2026-09-11T10:00:00+00:00", model_version="v1"),
        dict(commun, run_datetime="2026-09-11T20:00:00+00:00", model_version="v2",
             p_bleu=0.5, p_blanc=0.3, p_rouge=0.2, predicted_color=2),
    ])
    brut = conn.execute("SELECT COUNT(*) n FROM predictions").fetchone()["n"]
    vues = conn.execute("SELECT * FROM predictions_a_jour").fetchall()
    assert brut == 2, "les deux versions doivent rester en base"
    assert len(vues) == 1, f"{len(vues)} lignes vues au lieu d'une seule"
    assert vues[0]["model_version"] == "v2", "c'est la plus RECENTE qui doit sortir"

    # Un backtest rejoue ne doit pas masquer la prediction reellement faite ce jour-la :
    # ce sont deux series independantes.
    db.insert_predictions(conn, [
        dict(commun, run_datetime="2026-09-11T23:00:00+00:00",
             model_version="backtest-7")])
    familles = {r["model_version"] for r in
                conn.execute("SELECT * FROM predictions_a_jour")}
    assert familles == {"v2", "backtest-7"}, f"series melangees : {familles}"


def test_la_vue_se_recree_sur_une_base_ancienne():
    """Une base venant du cache Actions porte la vue telle qu'elle etait ce jour-la.
    Si `init_db` ne faisait que `CREATE VIEW IF NOT EXISTS`, la correction ne
    s'appliquerait jamais aux bases existantes -- exactement le genre de garde-fou qui
    attend qu'on pense a lui."""
    conn = db.connect(":memory:")
    db.init_db(conn)
    conn.execute("DROP VIEW predictions_a_jour")
    conn.execute("CREATE VIEW predictions_a_jour AS SELECT * FROM predictions")
    conn.commit()
    db.init_db(conn)
    sql = conn.execute("""SELECT sql FROM sqlite_master
                          WHERE type='view' AND name='predictions_a_jour'""").fetchone()
    assert "ROW_NUMBER" in (sql["sql"] or ""), "la vue perimee n'a pas ete recreee"


def test_deux_crons_rapproches_ne_se_confondent_pas():
    """La raison d'etre du regroupement par heure observee.

    Le passage de 6h30 retarde de quatre heures s'execute vers 10h30 -- l'heure meme
    d'un autre cron. L'ancienne methode, qui rangeait chaque passage sous le cron qu'il
    suivait de plus pres, aurait credite ce dernier d'un retard NUL et fait annoncer a
    la page une heure quatre heures trop tot. On ne regarde donc plus que ce qui a ete
    observe.
    """
    vus = (["2026-10-%02dT10:31:00+00:00" % j for j in (1, 2, 3)]      # 6h30 + 4 h
           + ["2026-10-%02dT14:33:00+00:00" % j for j in (1, 2, 3)])   # 10h30 + 4 h
    passages = cadence.observee(vus)["passages"]
    heures = [p["attendu_utc"] for p in passages]
    assert heures == ["10:31", "14:33"], f"rendez-vous mal separes : {heures}"
    assert all(p["fiable"] for p in passages)


def test_les_horaires_demandes_restent_documentes():
    """`SCHEDULES_UTC` ne sert plus a mesurer, mais il dit ce qu'on DEMANDE -- et un
    test le verrouille deja sur les `cron` du workflow. Les deux doivent rester en
    phase : le jour ou un cron est ajoute sans la config, c'est ce couple qui le dit."""
    assert "06:30" in config.SCHEDULES_UTC, (
        "le passage tot, ajoute pour viser midi, a disparu de la configuration")
    assert len(config.SCHEDULES_UTC) == 3


def test_j1_se_retire_du_perimetre_de_notation():
    """J+1 n'est pas une prediction : `predict_next_days` fait
    `predicted_color = official or ...`, donc la couleur publiee par RTE l'ecrase.
    Le noter revient a se faire noter sur une question deja resolue."""
    from src import backtest
    metas = [{"target": date(2026, 1, 6), "is_holiday": False, "horizon": h}
             for h in range(1, 11)]
    tout = backtest.evaluables(metas)
    sans = backtest.evaluables(metas, horizon_min=2)
    assert tout.sum() == 10, "les dix echeances d'un mardi de janvier sont eligibles"
    assert sans.sum() == 9 and not sans[0], "J+1 aurait du sortir du perimetre"


def test_l_instabilite_de_la_prevision_ne_lit_que_le_passe():
    """La prevision d'echeance L pour la cible T a ete emise le jour T-L. Seules celles
    d'echeance SUPERIEURE ou egale a l'horizon sont deja publiees le jour ou l'on
    predit -- lire les autres ferait entrer du futur dans le backtest."""
    s = store()
    cible = date(2025, 1, 15)
    connues = {lead for (d, lead) in s.weather if d == cible}
    for horizon in (2, 4):
        # On neutralise tout ce qui est plus recent que l'horizon et on verifie que la
        # mesure ne bouge pas : si elle bougeait, c'est qu'elle le lisait.
        avant = s.forecast_churn(cible, horizon)
        s._churn_cache.clear()
        sauvegarde = {}
        for lead in connues:
            if 0 < lead < horizon:
                sauvegarde[lead] = s.weather.pop((cible, lead))
        apres = s.forecast_churn(cible, horizon)
        s.weather.update({(cible, l): v for l, v in sauvegarde.items()})
        s._churn_cache.clear()
        assert (avant != avant and apres != apres) or avant == apres, (
            f"a J+{horizon}, la mesure change quand on retire les previsions plus "
            f"recentes : {avant} puis {apres} — elle lisait le futur")


def test_l_avance_sur_le_quota_ignore_les_saisons_posterieures():
    """Se comparer au rythme d'hivers qu'on n'a pas encore vus reviendrait a savoir
    d'avance quel hiver on va avoir."""
    s = store()
    saisons = sorted({i["season"] for i in s.days.values()})
    assert len(saisons) >= 3, "base d'essai trop courte pour ce test"
    # La premiere saison n'a aucune reference anterieure : la mesure doit etre absente,
    # pas inventee.
    debut = calendrier.season_start(saisons[0])
    v = s.avance_quota(debut + timedelta(days=120), config.ROUGE, 5)
    assert v != v, f"une avance a ete calculee sans saison de reference : {v}"
