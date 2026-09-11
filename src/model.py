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


def constrain(probs, meta, force_quota=None):
    """Annule les couleurs impossibles, renormalise, puis impose les Rouge forces.

    Le masque enleve ce que le contrat interdit. La contrainte de quota fait
    l'inverse : elle impose ce que le contrat oblige. Quand il ne reste pas plus de
    jours eligibles que de Rouge a placer, tous ces jours SONT Rouge -- ce n'est pas
    une prevision mais une consequence, et la laisser apprendre au modele serait lui
    demander d'extrapoler un regime qu'il n'a presque jamais vu.
    """
    masked = probs * _mask_matrix(meta)
    total = masked.sum(axis=1, keepdims=True)
    fallback = np.zeros_like(masked)
    fallback[:, 0] = 1.0
    out = np.where(total > 0, masked / np.where(total > 0, total, 1), fallback)

    if force_quota is None:
        force_quota = config.FORCE_QUOTA_ROUGE
    if not force_quota:
        return out
    forces = np.array([rules.rouge_force(m["target"], m.get("rouge_left"))
                       for m in meta])
    if forces.any():
        # Le masque a pu interdire le Rouge (dimanche, ferie) : on n'impose que la
        # ou il reste possible, sinon on contredirait une regle plus forte.
        forces &= out[:, 2] > 0
        out[forces] = [0.0, 0.0, 1.0]
    return out


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
                 n_seeds=None, force_quota=None, two_stage=None,
                 horizon_split=None):
        self.seed = seed
        self.excluded = config.EXCLUDED_FEATURES if excluded is None else excluded
        self.winter_weight = (config.WINTER_WEIGHT if winter_weight is None
                              else winter_weight)
        self.n_seeds = config.N_SEEDS if n_seeds is None else n_seeds
        self.force_quota = (config.FORCE_QUOTA_ROUGE if force_quota is None
                            else force_quota)
        self.two_stage = config.TWO_STAGE if two_stage is None else two_stage
        self.horizon_split = (config.HORIZON_SPLIT if horizon_split is None
                              else horizon_split)
        self.class_weight = class_weight
        self.rouge_threshold = (config.ROUGE_ALERT_THRESHOLD if rouge_threshold is None
                                else rouge_threshold)
        self.blanc_threshold = (config.BLANC_ALERT_THRESHOLD if blanc_threshold is None
                                else blanc_threshold)
        self.clf = None        # liste de modeles, moyennee a la prediction
        self.clf_tendu = None  # second etage : Blanc contre Rouge
        self.clf_court = None  # modele dedie aux echeances courtes
        self.dead_court = None  # ses colonnes vides a lui, distinctes de l'autre bande
        self.dead_columns = None

    def _base(self, seed):
        return HistGradientBoostingClassifier(
            max_iter=400, learning_rate=0.05, max_leaf_nodes=31,
            min_samples_leaf=40, l2_regularization=1.0,
            class_weight=self.class_weight,
            # Sans tirage sur les colonnes, changer de graine ne change rien : avec
            # early_stopping desactive et un jeu plus petit que le seuil de
            # sous-echantillonnage du binning, l'algorithme est deterministe. Le
            # tirage n'est donc introduit que quand on moyenne plusieurs modeles.
            max_features=1.0 if self.n_seeds <= 1 else 0.85,
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
        return self._applique(X, self.dead_columns)

    def _masque_mort(self, X, annonce=""):
        """Colonnes a neutraliser pour CE jeu : entierement vides, ou exclues."""
        X = np.asarray(X, dtype=float)
        exclues = np.array([n in self.excluded for n in features.FEATURE_NAMES])
        if len(exclues) != X.shape[1]:
            exclues = np.zeros(X.shape[1], dtype=bool)
        masque = np.isnan(X).all(axis=0) | exclues
        vides = [features.FEATURE_NAMES[i] for i, mort in enumerate(masque)
                 if mort and i < len(features.FEATURE_NAMES)]
        if vides:
            print(f"  features neutralisees{annonce} : {', '.join(vides)}")
        return masque

    def _applique(self, X, masque):
        X = np.asarray(X, dtype=float)
        if masque is not None and masque.any():
            X = X.copy()
            X[:, masque] = 0.0
        return X

    def _ensemble(self, Xn, y, meta, decalage=0):
        """Plusieurs modeles ne differant que par leur graine.

        Un seul arbre boostee depend du hasard de ses coupures, et c'est ce hasard
        qui fait qu'un hiver passe et que le suivant casse ; la moyenne l'attenue.
        """
        return [self._fit_calibrated(Xn, y, meta, self.seed + decalage + k)
                for k in range(max(1, self.n_seeds))]

    def fit(self, X, y, meta):
        y = np.array(y)
        X = np.asarray(X, dtype=float)

        # Une meme colonne n'a pas la meme valeur selon l'echeance. A J+1 la prevision
        # de consommation est juste et la charge residuelle devient solidement porteuse
        # (+0,164, pire saison +0,024) ; a J+10 c'est du bruit, et elle tombe a
        # +0,055 avec une pire saison a -0,094. Le modele unique traite pourtant les
        # dix echeances pareil : `horizon` est bien une colonne, mais un arbre doit
        # alors depenser sa capacite a redecouvrir cette interaction dans chaque
        # branche. Couper l'apprentissage en deux la lui donne d'emblee.
        h = np.array([m["horizon"] for m in meta])
        court = h <= (self.horizon_split or 0)
        # Il faut assez de lignes DES DEUX cotes, sinon deux modeles faibles valent
        # moins qu'un seul entraine sur tout.
        if self.horizon_split and court.sum() >= 400 and (~court).sum() >= 400:
            # Chaque bande a ses propres colonnes vides : `rte_forecast_mw` n'existe
            # qu'a J+1, elle est donc INTEGRALEMENT vide dans la bande longue. Un
            # masque global ne le verrait pas -- la colonne a des valeurs quelque part
            # -- et le binning echouerait sur la bande ou elle n'en a aucune.
            self.dead_court = self._masque_mort(X[court], " (J+1 a J+%d)" % self.horizon_split)
            self.dead_columns = self._masque_mort(X[~court], " (au-dela de J+%d)" % self.horizon_split)
            mc = [m for m, k in zip(meta, court) if k]
            ml = [m for m, k in zip(meta, court) if not k]
            self.clf_court = self._ensemble(
                self._applique(X[court], self.dead_court), y[court], mc, 200)
            self.clf = self._ensemble(
                self._applique(X[~court], self.dead_columns), y[~court], ml, 300)
        else:
            self.dead_columns = self._masque_mort(X)
            self.dead_court = None
            self.clf = self._ensemble(self._applique(X, self.dead_columns), y, meta)

        if self.two_stage:
            # Second etage : Blanc ou Rouge, entraine sur les SEULS jours tendus.
            #
            # Le modele a trois classes est ecrase par les Bleu, qui font 82 % des
            # exemples : il apprend surtout a les reconnaitre, et l'arbitrage entre
            # Blanc et Rouge -- qui ne se joue que sur un jour sur sept -- ne pese
            # presque rien dans sa fonction de cout. C'est pourtant la que tout
            # echoue : 220 jours Blanc annonces Rouge, et 71 % des fausses alertes
            # tombant sur du Blanc.
            #
            # Isole, ce second etage voit un probleme equilibre (43 Blanc contre 22
            # Rouge) et peut consacrer toute sa capacite a cette frontiere-la.
            tendu = y != config.BLEU
            if tendu.sum() >= 100 and len(set(y[tendu].tolist())) == 2:
                mt = [m for m, k in zip(meta, tendu) if k]
                self.clf_tendu = [
                    self._fit_calibrated(Xn[tendu], y[tendu], mt, self.seed + 100 + k)
                    for k in range(max(1, self.n_seeds))]
        return self

    def _moyenne(self, modeles, Xn, colonnes):
        """Probabilites moyennees sur les modeles, remises dans l'ordre des couleurs."""
        out = np.zeros((len(Xn), len(colonnes)))
        for clf in modeles:
            raw = clf.predict_proba(Xn)
            for i, cls in enumerate(clf.classes_):
                out[:, colonnes.index(int(cls))] += raw[:, i]
        return out / len(modeles)

    def predict_proba(self, X, meta):
        Xn = self._applique(X, self.dead_columns)
        ordered = self._moyenne(self.clf, Xn, CLASSES)

        if self.clf_court is not None:
            # Chaque ligne est jugee par le modele de sa bande d'echeance, avec le
            # masque de colonnes vides de cette bande-la.
            court = np.array([m["horizon"] <= self.horizon_split for m in meta])
            if court.any():
                Xc = self._applique(X, self.dead_court)
                ordered[court] = self._moyenne(self.clf_court, Xc[court], CLASSES)

        if self.clf_tendu:
            # La masse « pas Bleu » vient du premier etage, sa repartition du second.
            # Le premier reste donc juge de « tendu ou non », le second de la couleur,
            # et les probabilites continuent de sommer a un.
            tendu = ordered[:, 1] + ordered[:, 2]
            part = self._moyenne(self.clf_tendu, Xn, [config.BLANC, config.ROUGE])
            ordered[:, 1] = tendu * part[:, 0]
            ordered[:, 2] = tendu * part[:, 1]
        return constrain(ordered, meta, self.force_quota)


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
