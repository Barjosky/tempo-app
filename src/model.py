"""Baseline climatologique + modele hybride (GBM calibre puis contraint par les regles)."""
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import GroupKFold

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
from src import features, rules

CLASSES = [config.BLEU, config.BLANC, config.ROUGE]


def _mask_matrix(meta):
    """Masque des couleurs contractuellement possibles pour chaque ligne."""
    return np.array([
        rules.allowed_mask(m["target"], m["is_holiday"], m["rouge_left"], m["blanc_left"])
        for m in meta
    ], dtype=float)


def constrain(probs, meta):
    """Annule les couleurs impossibles et renormalise."""
    masked = probs * _mask_matrix(meta)
    total = masked.sum(axis=1, keepdims=True)
    fallback = np.zeros_like(masked)
    fallback[:, 0] = 1.0
    return np.where(total > 0, masked / np.where(total > 0, total, 1), fallback)


class ClimatologyBaseline:
    """Frequences historiques conditionnelles (mois x type de jour x quota restant)."""

    def __init__(self, alpha=1.0):
        self.alpha = alpha
        self.table = defaultdict(lambda: np.zeros(3))
        self.prior = np.zeros(3)

    @staticmethod
    def _key(m):
        target = m["target"]
        wd = target.weekday()
        wd_class = 2 if wd == 6 else (1 if wd == 5 else 0)
        quota_bucket = min(3, m["rouge_left"] // 6)
        return (target.month, wd_class, int(m["is_holiday"]), quota_bucket)

    def fit(self, meta, y):
        for m, color in zip(meta, y):
            self.table[self._key(m)][CLASSES.index(color)] += 1
            self.prior[CLASSES.index(color)] += 1
        return self

    def predict_proba(self, meta):
        prior = (self.prior + self.alpha) / (self.prior.sum() + 3 * self.alpha)
        out = []
        for m in meta:
            counts = self.table.get(self._key(m))
            if counts is None or counts.sum() < 5:
                out.append(prior)
            else:
                out.append((counts + self.alpha) / (counts.sum() + 3 * self.alpha))
        return constrain(np.array(out), meta)


class TempoModel:
    """GBM multi-classe calibre, puis passe dans les contraintes Tempo."""

    # Le seuil d'alerte Rouge est un choix explicite (config.ROUGE_ALERT_THRESHOLD),
    # pas une valeur auto-calee : le calage automatique, teste sur une puis sur
    # plusieurs saisons de validation, s'effondrait au plancher (0.05) et noyait la
    # page sous les fausses alertes. Le balayage complet est dans analyse_seuils.py.
    def __init__(self, seed=0, class_weight=None, rouge_threshold=None,
                 blanc_threshold=None, excluded=None, winter_weight=None,
                 n_seeds=None):
        self.seed = seed
        self.excluded = config.EXCLUDED_FEATURES if excluded is None else excluded
        self.winter_weight = (config.WINTER_WEIGHT if winter_weight is None
                              else winter_weight)
        self.n_seeds = config.N_SEEDS if n_seeds is None else n_seeds
        self.class_weight = class_weight
        self.rouge_threshold = (config.ROUGE_ALERT_THRESHOLD if rouge_threshold is None
                                else rouge_threshold)
        self.blanc_threshold = (config.BLANC_ALERT_THRESHOLD if blanc_threshold is None
                                else blanc_threshold)
        self.clf = None       # liste de modeles, moyennee a la prediction
        self.dead_columns = None

    def _base(self, seed):
        return HistGradientBoostingClassifier(
            max_iter=400, learning_rate=0.05, max_leaf_nodes=31,
            min_samples_leaf=40, l2_regularization=1.0,
            class_weight=self.class_weight,
            early_stopping=False, random_state=seed,
        )

    def _poids(self, meta):
        """Poids d'entrainement : les journees sans enjeu comptent moins.

        D'avril a octobre, les week-ends et les feries, la reponse est Bleu et le
        modele n'a rien a apprendre. Ces lignes sont pourtant les quatre cinquiemes
        du jeu : sans ce reequilibrage, elles dominent la fonction de cout et le
        modele s'optimise surtout la ou rien ne se joue.
        """
        if self.winter_weight == 1.0:
            return None
        return np.where(
            [rules.rouge_possible(m["target"], m["is_holiday"]) for m in meta],
            self.winter_weight, 1.0)

    def _fit_calibrated(self, X, y, meta, seed):
        groups = np.array([m["target"].toordinal() for m in meta])
        n_groups = len(set(groups.tolist()))
        splits = list(GroupKFold(n_splits=min(4, max(2, n_groups))).split(X, y, groups))
        clf = CalibratedClassifierCV(self._base(seed), method="isotonic", cv=splits)
        # Le poids sert a l'arbre ET a l'isotonic : la calibration se cale donc sur
        # le regime hivernal, celui dont on lit les probabilites.
        clf.fit(X, y, sample_weight=self._poids(meta))
        return clf

    def _neutralise(self, X):
        """Neutralise les colonnes entierement vides.

        Une feature dont la source n'a pas encore ete collectee sort NaN sur toutes
        les lignes. Le GBM sait pourtant traiter des NaN epars -- mais son binning
        echoue sur une colonne INTEGRALEMENT vide (« window shape cannot be larger
        than input array shape »), au lieu de l'ignorer. On y met une constante : un
        arbre n'y trouve aucune coupure, la feature est donc sans effet, et le modele
        reste entrainable avec une source de donnees absente plutot que de refuser de
        demarrer. Les colonnes neutralisees sont figees a l'entrainement, sinon la
        prediction sur une seule ligne en declarerait d'autres au hasard des NaN.
        """
        X = np.asarray(X, dtype=float)
        if self.dead_columns is None:
            exclues = np.array([n in self.excluded for n in features.FEATURE_NAMES])
            if len(exclues) != X.shape[1]:
                exclues = np.zeros(X.shape[1], dtype=bool)
            self.dead_columns = np.isnan(X).all(axis=0) | exclues
            vides = [features.FEATURE_NAMES[i]
                     for i, mort in enumerate(self.dead_columns)
                     if mort and i < len(features.FEATURE_NAMES)]
            if vides:
                print(f"  features neutralisees : {', '.join(vides)}")
        if self.dead_columns.any():
            X = X.copy()
            X[:, self.dead_columns] = 0.0
        return X

    def fit(self, X, y, meta):
        self.dead_columns = None
        Xn = self._neutralise(X)
        y = np.array(y)
        # Plusieurs modeles ne differant que par leur graine. Un seul arbre boostee
        # depend du hasard de ses coupures, et c'est ce hasard qui fait qu'un hiver
        # passe et que le suivant casse ; la moyenne l'attenue.
        self.clf = [self._fit_calibrated(Xn, y, meta, self.seed + k)
                    for k in range(max(1, self.n_seeds))]
        return self

    def predict_proba(self, X, meta):
        Xn = self._neutralise(X)
        ordered = np.zeros((len(X), 3))
        for clf in self.clf:
            raw = clf.predict_proba(Xn)
            for i, cls in enumerate(clf.classes_):
                ordered[:, CLASSES.index(int(cls))] += raw[:, i]
        ordered /= len(self.clf)
        return constrain(ordered, meta)


def decide(probs, rouge_threshold, blanc_threshold=None):
    """Couleur retenue : argmax, corrige par deux seuils.

    L'argmax seul est structurellement aveugle aux classes rares : le Blanc pese ~12 %
    des jours contre 82 % de Bleu, il ne l'emporte donc presque jamais meme quand il
    est le pari le plus interessant. Chaque seuil promeut sa couleur des qu'elle est
    assez probable, le Rouge en dernier car c'est lui qui coute le plus cher a rater.
    """
    blanc_threshold = (config.BLANC_ALERT_THRESHOLD if blanc_threshold is None
                       else blanc_threshold)
    colors = np.array(CLASSES)[probs.argmax(axis=1)]
    colors[probs[:, 1] >= blanc_threshold] = config.BLANC
    colors[probs[:, 2] >= rouge_threshold] = config.ROUGE
    return colors
