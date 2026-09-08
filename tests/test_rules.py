"""Verifie les contraintes Tempo contre les 6 saisons reellement observees."""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
from src import db, rules
from src.sources import calendrier


def load_real_days():
    conn = db.connect()
    return [dict(r) for r in conn.execute(
        "SELECT * FROM days WHERE color IS NOT NULL ORDER BY date")]


def test_aucun_rouge_le_weekend_ou_ferie():
    for row in load_real_days():
        if row["color"] != config.ROUGE:
            continue
        d = date.fromisoformat(row["date"])
        assert d.weekday() < 5, f"Rouge un week-end : {d}"
        assert not calendrier.is_holiday(d), f"Rouge un jour ferie : {d}"


def test_blanc_possible_le_samedi_mais_jamais_le_dimanche():
    days = load_real_days()
    samedis_blancs = [r for r in days
                      if r["color"] == config.BLANC and date.fromisoformat(r["date"]).weekday() == 5]
    assert samedis_blancs, "Aucun samedi Blanc trouve : la regle serait a revoir"
    for row in days:
        d = date.fromisoformat(row["date"])
        if d.weekday() == 6:
            assert row["color"] == config.BLEU, f"Dimanche non bleu : {d}"


def test_rouge_uniquement_de_novembre_a_mars():
    for row in load_real_days():
        if row["color"] == config.ROUGE:
            assert date.fromisoformat(row["date"]).month in rules.ROUGE_MONTHS


def test_quotas_respectes_par_saison():
    days = load_real_days()
    seasons = {}
    for row in days:
        seasons.setdefault(row["season"], []).append(row["color"])
    for season, colors in seasons.items():
        if len(colors) < 365:  # saison en cours
            continue
        assert colors.count(config.ROUGE) == config.QUOTA_ROUGE, season
        assert colors.count(config.BLANC) == config.QUOTA_BLANC, season


def test_le_masque_interdit_ce_que_les_donnees_interdisent():
    samedi = date(2025, 1, 11)
    assert rules.allowed_mask(samedi) == [True, True, False]
    dimanche = date(2025, 1, 12)
    assert rules.allowed_mask(dimanche) == [True, False, False]
    juillet = date(2025, 7, 8)
    assert rules.allowed_mask(juillet) == [True, True, False]
    hiver = date(2025, 1, 14)
    assert rules.allowed_mask(hiver) == [True, True, True]
    assert rules.allowed_mask(hiver, rouge_left=0) == [True, True, False]


def test_les_probabilites_masquees_sont_renormalisees():
    probs = rules.apply_mask([0.5, 0.3, 0.2], date(2025, 1, 12))  # dimanche
    assert probs == [1.0, 0.0, 0.0]
    probs = rules.apply_mask([0.5, 0.3, 0.2], date(2025, 1, 11))  # samedi
    assert probs[2] == 0.0
    assert abs(sum(probs) - 1.0) < 1e-9
    assert probs[1] > 0, "le Blanc du samedi doit rester possible"


def test_features_sans_fuite_temporelle():
    """Les quotas connus a la date R ne doivent integrer aucune couleur posterieure a R."""
    from src import features
    conn = db.connect()
    store = features.FeatureStore(conn)
    run_date = date(2025, 1, 15)
    state = store.season_state(run_date)
    manual = 0
    d = calendrier.season_start(state["season"])
    while d <= run_date:
        if store.days.get(d, {}).get("color") == config.ROUGE:
            manual += 1
        d += timedelta(days=1)
    assert state["rouge_used"] == manual
    future_rouges = sum(1 for dd, info in store.days.items()
                        if dd > run_date and dd.year == 2025 and info["color"] == config.ROUGE)
    assert future_rouges > 0, "il doit rester des rouges apres cette date dans l'historique"
    assert state["rouge_used"] < config.QUOTA_ROUGE
