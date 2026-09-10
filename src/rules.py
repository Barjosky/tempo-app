"""Contraintes dures Tempo, verifiees sur 6 saisons reelles (2020-2026).

Constate sur les donnees :
  - dimanche : 0 Blanc, 0 Rouge  -> toujours Bleu
  - samedi   : 33 Blanc, 0 Rouge -> Blanc possible, Rouge impossible
  - feries   : 2 Blanc, 0 Rouge  -> Blanc possible, Rouge impossible
  - Rouge uniquement de novembre a mars, jamais avril-octobre
  - quotas 300/43/22 respectes a la journee pres chaque saison
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
from src.sources import calendrier

ROUGE_MONTHS = {11, 12, 1, 2, 3}


def rouge_possible(d, is_holiday=None, rouge_left=None):
    if is_holiday is None:
        is_holiday = calendrier.is_holiday(d)
    if d.weekday() >= 5 or is_holiday:
        return False
    if d.month not in ROUGE_MONTHS:
        return False
    if rouge_left is not None and rouge_left <= 0:
        return False
    return True


def blanc_possible(d, blanc_left=None):
    if d.weekday() == 6:
        return False
    if blanc_left is not None and blanc_left <= 0:
        return False
    return True


_JOURS_RESTANTS = {}


def remaining_rouge_days(target):
    """Jours ou un Rouge est encore possible, de `target` (inclus) au 31 mars."""
    if target in _JOURS_RESTANTS:
        return _JOURS_RESTANTS[target]
    season = calendrier.season_of(target)
    fin = date(int(season.split("-")[1]), 3, 31)
    n = 0
    d = target
    while d <= fin:
        if rouge_possible(d):
            n += 1
        d += timedelta(days=1)
    _JOURS_RESTANTS[target] = n
    return n


def rouge_slack(target, rouge_left):
    """Marge de placement : jours encore disponibles moins Rouge encore a placer.

    Les quotas sont consommes a la journee pres -- verifie sur six saisons. Quand
    cette marge tombe a zero, tous les jours eligibles restants SONT Rouge, par
    arithmetique : la meteo n'a plus voix au chapitre.

    C'est ce qui est arrive en mars 2026, ou treize des vingt-deux Rouge ont ete
    places dans le dernier mois, jusqu'au 31 mars. Un modele entraine sur des hivers
    consommes des fevrier ne pouvait pas le deviner.
    """
    if rouge_left is None:
        return None
    return remaining_rouge_days(target) - rouge_left


def rouge_force(target, rouge_left):
    """Vrai quand il ne reste pas assez de jours pour etaler le quota restant."""
    marge = rouge_slack(target, rouge_left)
    return marge is not None and rouge_left > 0 and marge <= 0


def allowed_mask(d, is_holiday=None, rouge_left=None, blanc_left=None):
    """Masque [bleu, blanc, rouge] des couleurs contractuellement possibles."""
    return [
        True,
        blanc_possible(d, blanc_left),
        rouge_possible(d, is_holiday, rouge_left),
    ]


def apply_mask(probs, d, is_holiday=None, rouge_left=None, blanc_left=None):
    """Annule les couleurs impossibles puis renormalise."""
    mask = allowed_mask(d, is_holiday, rouge_left, blanc_left)
    out = [p if m else 0.0 for p, m in zip(probs, mask)]
    total = sum(out)
    if total <= 0:
        return [1.0, 0.0, 0.0]
    return [p / total for p in out]
