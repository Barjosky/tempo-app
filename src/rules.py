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

# --- Deliberation CRE n°2026-189 du 21 octobre 2026, applicable au 1er novembre 2026 ---
#
# RTE tire desormais les Rouge selon la methode des jours PP du mecanisme de capacite,
# dans un « tunnel » de cumuls mensuels. N'est encode ici QUE ce que plusieurs sources
# rapportent a l'identique ; le texte officiel (cre.fr) n'a pas pu etre lu depuis
# l'environnement de developpement. Les bornes de fin janvier et de fin fevrier hors
# plancher n'y sont donc PAS : une seule source les donne, et inventer une borne dure
# reviendrait a interdire au modele une couleur que RTE peut parfaitement tirer.
#
# Tout est date : les saisons passees ont ete tirees sous les anciennes regles, et les
# juger avec les nouvelles fausserait l'historique d'entrainement comme le backtest.
REGLES_CRE_2026 = date(2026, 11, 1)
# Retires du tirage Rouge -- ils peuvent encore etre Blanc.
JOURS_SANS_ROUGE = {(12, 24), (12, 31)}
# Rouge au plus, dans le mois.
PLAFOND_MOIS = {11: 3, 3: 2}
# Cumul de Rouge au soir du 31 decembre : exactement six, ni plus ni moins.
CUMUL_FIN_DECEMBRE = 6
# Corollaire du plafond de mars et du quota de 22 : vingt au moins fin fevrier.
CUMUL_FIN_FEVRIER = config.QUOTA_ROUGE - PLAFOND_MOIS[3]


def regles_2026(d):
    """Vrai si `d` releve du tirage encadre par la deliberation 2026-189."""
    return d >= REGLES_CRE_2026


def echeances_tunnel(season):
    """Planchers de cumul a respecter, par date limite : [(date, cumul minimal)].

    Seules les deux bornes concordantes : six au 31 decembre, vingt fin fevrier. La
    fin de saison (22 au 31 mars) est deja le quota, que `rouge_force` traitait seul.
    """
    debut, fin = (int(a) for a in season.split("-"))
    if date(debut, 12, 31) < REGLES_CRE_2026:
        return []
    fin_fevrier = date(fin, 3, 1) - timedelta(days=1)
    return [(date(debut, 12, 31), CUMUL_FIN_DECEMBRE), (fin_fevrier, CUMUL_FIN_FEVRIER)]


def sous_plafond(d, rouge_par_mois):
    """Le tunnel autorise-t-il encore un Rouge ce jour-la, vu les Rouge deja tires ?

    `rouge_par_mois` compte les Rouge CONNUS de la saison, par mois. C'est une
    condition necessaire, pas suffisante : une prevision a J+5 ignore si J+1..J+4
    seront Rouge -- exactement la limite qu'a deja `rouge_left`.
    """
    if not regles_2026(d) or rouge_par_mois is None:
        return True
    dans_le_mois = rouge_par_mois.get(d.month, 0)
    if d.month in PLAFOND_MOIS and dans_le_mois >= PLAFOND_MOIS[d.month]:
        return False
    if d.month in (11, 12):
        # Aucun Rouge possible en septembre-octobre : le cumul au 31 decembre est la
        # somme de novembre et decembre.
        if rouge_par_mois.get(11, 0) + rouge_par_mois.get(12, 0) >= CUMUL_FIN_DECEMBRE:
            return False
    return True


def rouge_possible(d, is_holiday=None, rouge_left=None, rouge_par_mois=None):
    if is_holiday is None:
        is_holiday = calendrier.is_holiday(d)
    if d.weekday() >= 5 or is_holiday:
        return False
    if d.month not in ROUGE_MONTHS:
        return False
    if regles_2026(d) and (d.month, d.day) in JOURS_SANS_ROUGE:
        return False
    if rouge_left is not None and rouge_left <= 0:
        return False
    if not sous_plafond(d, rouge_par_mois):
        return False
    return True


def blanc_possible(d, blanc_left=None):
    if d.weekday() == 6:
        return False
    if blanc_left is not None and blanc_left <= 0:
        return False
    return True


_JOURS_RESTANTS = {}


def remaining_rouge_days(target, fin=None):
    """Jours ou un Rouge est encore possible, de `target` (inclus) a `fin`.

    Par defaut jusqu'au 31 mars. `fin` sert aux echeances du tunnel mensuel : la
    question n'est plus seulement « reste-t-il assez de jours d'ici fin mars ? » mais
    aussi « d'ici le 31 decembre ? ».
    """
    if fin is None:
        season = calendrier.season_of(target)
        fin = date(int(season.split("-")[1]), 3, 31)
    cle = (target, fin)
    if cle in _JOURS_RESTANTS:
        return _JOURS_RESTANTS[cle]
    n = 0
    d = target
    while d <= fin:
        if rouge_possible(d):
            n += 1
        d += timedelta(days=1)
    _JOURS_RESTANTS[cle] = n
    return n


_JOURS_BLANC = {}


def remaining_blanc_days(target, fin=None):
    """Jours ou un Blanc est encore possible, de `target` (inclus) a `fin`.

    Par defaut jusqu'a la fin de saison. `fin` sert a compter sur la fenetre
    hivernale, la ou le Blanc est reellement en concurrence avec le Rouge : sur six
    saisons, 37 des 43 Blanc tombent entre novembre et mars, les six autres debordant
    en avril et en octobre ou aucun Rouge n'est possible.
    """
    if fin is None:
        fin = calendrier.season_end(calendrier.season_of(target))
    cle = (target, fin)
    if cle in _JOURS_BLANC:
        return _JOURS_BLANC[cle]
    n = 0
    d = target
    while d <= fin:
        if blanc_possible(d):
            n += 1
        d += timedelta(days=1)
    _JOURS_BLANC[cle] = n
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
    """Vrai quand il ne reste pas assez de jours pour etaler le quota restant.

    Depuis la deliberation 2026-189, il y a d'autres dates limites que le 31 mars :
    six Rouge au 31 decembre, vingt fin fevrier. Si, au 23 decembre, deux Rouge
    seulement ont ete tires et qu'il reste quatre jours eligibles avant le 31, ces
    quatre jours SONT Rouge -- par la meme arithmetique que l'ancienne regle de fin
    de saison, avec une echeance plus proche.
    """
    marge = rouge_slack(target, rouge_left)
    if marge is not None and rouge_left > 0 and marge <= 0:
        return True
    if rouge_left is None:
        return False
    deja = config.QUOTA_ROUGE - rouge_left
    for echeance, plancher in echeances_tunnel(calendrier.season_of(target)):
        if target > echeance:
            continue
        manque = plancher - deja
        if manque > 0 and remaining_rouge_days(target, echeance) <= manque:
            return True
    return False


def allowed_mask(d, is_holiday=None, rouge_left=None, blanc_left=None,
                 rouge_par_mois=None):
    """Masque [bleu, blanc, rouge] des couleurs contractuellement possibles."""
    return [
        True,
        blanc_possible(d, blanc_left),
        rouge_possible(d, is_holiday, rouge_left, rouge_par_mois),
    ]


def apply_mask(probs, d, is_holiday=None, rouge_left=None, blanc_left=None):
    """Annule les couleurs impossibles puis renormalise."""
    mask = allowed_mask(d, is_holiday, rouge_left, blanc_left)
    out = [p if m else 0.0 for p, m in zip(probs, mask)]
    total = sum(out)
    if total <= 0:
        return [1.0, 0.0, 0.0]
    return [p / total for p in out]
