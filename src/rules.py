"""Contraintes dures Tempo, verifiees sur 6 saisons reelles (2020-2026).

Constate sur les donnees :
  - dimanche : 0 Blanc, 0 Rouge  -> toujours Bleu
  - samedi   : 33 Blanc, 0 Rouge -> Blanc possible, Rouge impossible
  - feries   : 2 Blanc, 0 Rouge  -> Blanc possible, Rouge impossible
  - Rouge uniquement de novembre a mars, jamais avril-octobre
  - quotas 300/43/22 respectes a la journee pres chaque saison
"""
import sys
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
