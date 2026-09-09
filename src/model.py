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
                 blanc_threshold=None):
        self.seed = seed
        self.class_weight = class_weight
        self.rouge_threshold = (config.ROUGE_ALERT_THRESHOLD if rouge_threshold is None
                                else rouge_threshold)
        self.blanc_threshold = (config.BLANC_ALERT_THRESHOLD if blanc_threshold is None
                                else blanc_threshold)
        self.clf = None
        self.dead_columns = None

    def _base(self):
        return HistGradientBoostingClassifier(
            max_iter=400, learning_rate=0.05, max_leaf_nodes=31,
            min_samples_leaf=40, l2_regularization=1.0,
            class_weight=self.class_weight,
            early_stopping=False, random_state=self.seed,
        )

    def _fit_calibrated(self, X, y, meta):
        groups = np.array([m["target"].toordinal() for m in meta])
        n_groups = len(set(groups.tolist()))
        splits = list(GroupKFold(n_splits=min(4, max(2, n_groups))).split(X, y, groups))
        clf = CalibratedClassifierCV(self._base(), method="isotonic", cv=splits)
        clf.fit(X, y)
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
            self.dead_columns = np.isnan(X).all(axis=0)
            vides = [features.FEATURE_NAMES[i]
                     for i, mort in enumerate(self.dead_columns)
                     if mort and i < len(features.FEATURE_NAMES)]
            if vides:
                print(f"  features sans donnees, neutralisees : {', '.join(vides)}")
        if self.dead_columns.any():
            X = X.copy()
            X[:, self.dead_columns] = 0.0
        return X

    def fit(self, X, y, meta):
        self.dead_columns = None
        self.clf = self._fit_calibrated(self._neutralise(X), np.array(y), meta)
        return self

    def predict_proba(self, X, meta):
        raw = self.clf.predict_proba(self._neutralise(X))
        ordered = np.zeros((len(X), 3))
        for i, cls in enumerate(self.clf.classes_):
            ordered[:, CLASSES.index(int(cls))] = raw[:, i]
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
