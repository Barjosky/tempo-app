# Journal des mesures et des décisions

Ce fichier existe parce que les décisions de modélisation de ce projet ne se lisent
nulle part dans le code : `config.py` dit *ce que* le modèle fait, jamais *pourquoi*,
ni ce qui a été essayé avant et écarté. Sans cette trace, une piste déjà mesurée sans
succès se represente tôt ou tard comme une bonne idée neuve.

Règle tenue ici : **aucune ligne sans chiffre**, et le chiffre vient d'une mesure, pas
d'une intuition.

---

## Le dénominateur : sur quoi on mesure

Un taux de réussite sur l'année entière ne veut rien dire — d'avril à octobre la
réponse est Bleu d'avance. Trois périmètres, du plus flatteur au plus honnête :

| Périmètre | Prédictions | Réussite |
|---|---|---|
| Toute l'année | 14 610 | 89,2 % |
| Novembre → mars | 6 050 | 77,7 % |
| **Jours éligibles** (lun-ven, nov-mars, hors fériés) | **4 190** | **71,0 %** |

Presque 20 points d'écart. **Tout ce qui suit est mesuré sur les jours éligibles.**

Le backtest ne note plus que ce périmètre ; l'entraînement, lui, continue de voir
toute l'année, parce que les jours sans enjeu portent l'état des quotas. Le périmètre
existe en deux exemplaires — un prédicat Python (`backtest.evaluables`) et un filtre
SQL (`app.period_clause`) — et un test verrouille leur concordance.

## Les taux de base

| Périmètre | Jours/saison | Bleu | Blanc | Rouge |
|---|---|---|---|---|
| Toute l'année | 365 | 300 (82 %) | 43 (12 %) | 22 (6 %) |
| Novembre → mars | 151 | 92 (61 %) | 37 (24 %) | 22 (15 %) |
| Jours éligibles | 105 | 52 (50 %) | 30 (29 %) | 22 (21 %) |

Sur un jour ouvré d'hiver, c'est **pile ou face** : une journée sur deux est déjà chère.
Sur les 43 Blanc d'une saison, 37 tombent nov-mars ; les six autres débordent la
fenêtre (avril, octobre), où aucun Rouge n'est possible.

Conséquence : le modèle apprend sur une population à 82 % de Bleu et sert sur une
population à 50 %. Ce décalage est la racine de plusieurs problèmes ci-dessous.

---

## Ce qui a été retenu, et ce que ça a rapporté

### La contrainte de quota — le gain le plus net

**Cause identifiée.** En 2025-2026, treize des vingt-deux Rouge sont tombés en mars,
le dernier le 31. Le profil de saison date le basculement au jour près : **le 13 mars
2026, il restait exactement 13 Rouge à placer et 13 jours éligibles**. À partir de là,
chaque jour restant était Rouge par arithmétique. Les autres saisons comptent 0 ou 1
jour forcé ; celle-ci en compte 13.

**Pourquoi le modèle ne pouvait pas le voir.** `rouge_pressure` portait l'information
— c'est même la seule feature individuellement porteuse des soixante — mais un arbre
n'extrapole pas : au-delà de la dernière valeur vue à l'entraînement, il rend la même
feuille. Entraîné sur des hivers soldés dès février, il n'avait jamais rencontré ça.

**La réponse.** Une règle, pas un apprentissage (`rules.rouge_force`, appliquée dans
`model.constrain`). Le masque contractuel garde la priorité : un dimanche reste Bleu
même sous quota tendu.

| | Avant | Après |
|---|---|---|
| Pire saison (log-loss) | 1,093 | **0,901** |
| Moyenne | 0,784 | **0,670** |
| Rappel Rouge | 73 % | **82 %** |
| Précision des alertes | 57 % | 58 % |

**+10 points de rappel à précision constante** : les 13 jours de mars n'ont pas été
devinés plus agressivement, ils ont été déduits.

Détail instructif : donner la marge au modèle comme simple *feature* (`rouge_slack`)
ne suffit pas — mesurée seule elle dégrade même le plancher (1,101 contre 1,085).
C'est la contrainte qui paie.

### L'ensemble de modèles

Inerte à la première tentative, et pour une raison à retenir : avec `early_stopping`
désactivé et un jeu plus petit que le seuil de sous-échantillonnage du binning,
`HistGradientBoosting` est **déterministe**. Cinq graines rendaient cinq modèles
identiques — cinq fois le calcul, zéro effet. Avec `max_features=0.85` la diversité
apparaît et 2023-2024 passe de 1,053 à 0,809.

---

## Ce qui a été mesuré et ÉCARTÉ

À ne pas represser sans nouvelle raison.

| Piste | Résultat | Verdict |
|---|---|---|
| Retirer les features d'offre (nucléaire) | moyenne 0,770 → 0,749, **pire saison 1,085 → 1,124** | écarté : améliore la moyenne, dégrade le plancher |
| Pondérer l'entraînement vers l'hiver | 2024-2025 : 0,638 → **0,720** | écarté |
| Ensemble sans diversification | strictement identique | écarté (voir ci-dessus) |

**Leçon de méthode.** J'ai d'abord conclu du contraire pour le nucléaire, à partir
d'une mesure de permutation. Permuter une colonne sur un modèle **déjà entraîné** ne
dit pas ce que vaut un modèle **entraîné sans elle** : dans le premier cas les autres
colonnes gardent les compensations apprises grâce à celle qu'on détruit, dans le
second le modèle se réorganise. Deux mesures, deux questions.

---

## Ce que chaque groupe de features apporte

Permutation, trois saisons, jours éligibles. La colonne qui tranche est la **pire
saison** : un groupe qui aide en moyenne mais nuit à un hiver est chanceux, pas utile.

| Groupe | +log-loss | bruit | pire saison | Verdict |
|---|---|---|---|---|
| **quotas** | **+0,120** | 0,025 | **+0,016** | porteur |
| hiver restant | +0,078 | 0,017 | −0,015 | instable |
| météo brute | +0,060 | 0,026 | −0,076 | instable |
| charge résiduelle | +0,055 | 0,016 | −0,094 | instable |
| offre (nucléaire) | −0,032 | 0,017 | −0,091 | négatif |

**Un seul groupe est solidement porteur : les quotas.** Le modèle roule sur l'état de
la saison bien plus que sur la météo. `rouge_pressure` est la seule feature
individuellement porteuse des soixante.

Piège à connaître : `hdd_f`, `temp_anomaly` et `hdd_rank_window` sortent
individuellement en poids mort alors que leur groupe aide — elles encodent toutes la
même température et se couvrent mutuellement. Ne jamais supprimer sur la seule lecture
feature par feature.

---

## Où en est la précision des alertes

| Échéance | Alertes | Justes | Précision | Rappel |
|---|---|---|---|---|
| J+1 | 113 | 74 | 65 % | 84 % |
| J+5 | 126 | 72 | 57 % | 82 % |
| J+10 | 131 | 72 | 55 % | 82 % |
| **Total** | **1241** | **725** | **58 %** | **82 %** |

Nuance qui change la lecture : sur les 516 alertes inutiles, **368 (71 %) tombent sur
des jours Blanc**, donc des journées déjà chères. Les vraies fausses alertes, sur du
Bleu, ne sont que 148 sur 1241 — **12 %**.

À retenir : le rappel seul se maximise trivialement en annonçant Rouge tous les jours.
Les deux se lisent ensemble ou pas du tout — d'où leur affichage conjoint dans la page.

## Fiabilité des probabilités

Sur les jours éligibles : **sous 30 % le modèle est trop prudent** (annonce 25 %,
tombe 33 %) ; **entre 40 et 70 % il est trop sûr de lui** (annonce 55 %, tombe 40 %).
Écarts de 15 points, qui passent tout juste le critère de viabilité (< 20).

---

## En cours

**Modèle à deux étages** (`config.TWO_STAGE`, désactivé par défaut). Vise la dernière
faiblesse : 220 jours Blanc annoncés Rouge. Premier étage « journée tendue ? », second
« Blanc ou Rouge ? » entraîné sur les seuls jours tendus, où il voit 30 Blanc contre
22 Rouge — un arbitrage équilibré au lieu d'être noyé. Comparatif lancé, verdict non
connu à la rédaction.

## Pistes non explorées

- **`blanc_pressure` est calculé sur un mauvais dénominateur** : les jours restants de
  la saison entière (jusqu'au 31 août), alors que `rouge_pressure` utilise la fenêtre
  hivernale. Les deux ne sont pas comparables, et leur *rapport* — quel quota est le
  plus rare en ce moment — est justement l'arbitrage Blanc/Rouge. Défaut probable.
- **La prévision RTE J+1 n'a jamais été mesurée équitablement** : elle n'existe qu'à
  une échéance sur dix, donc permuter sa colonne ne touche que 10 % des lignes. Il
  faudrait la mesurer à J+1 seulement.
- **Le seuil d'alerte n'a pas été recalé** depuis la contrainte de quota, ni sur la
  courbe en euros (`analyse_euros.py`).
- **Le tableau des seuils du README est périmé** : produit quand `analyse_seuils.py`
  n'appelait pas `fit_demand_model`, donc avec toutes les colonnes de charge
  résiduelle à NaN.

---

## Un piège qui s'est répété trois fois

**Un modèle en cache rencontre du code neuf.** Trois pannes de production :

1. Une feature ajoutée → le modèle attendait un nombre de colonnes qui n'existait plus.
2. `clf` devenu une liste (ensemble) → `CalibratedClassifierCV object is not iterable`.
   Les noms de features n'avaient pas bougé, le contrôle d'alors ne voyait rien.
3. `clf_tendu` ajouté → `has no attribute`. Le garde-fou existait mais demandait
   d'incrémenter un numéro **à la main**, et je l'ai oublié.

Corrigé à la racine : l'empreinte des attributs du modèle se calcule toute seule
(`predict.structure`), et le moindre ajout invalide les modèles d'avant sans que
personne ait à y penser. Le numéro manuel ne sert plus qu'aux changements de *sens* à
structure identique. **Un contrôle qui repose sur la mémoire de celui qui modifie n'en
est pas un.**

## Comment mesurer

```bash
python selection_modele.py      # compare des configurations, la PIRE saison décide
python diagnostic_features.py   # apport de chaque feature, par permutation
python diagnostic_saison.py     # pourquoi certains hivers sont durs
python analyse_euros.py         # ce que la prédiction rapporte, et à quel prix
python analyse_seuils.py        # balayage des seuils Rouge et Blanc
```

Rien de tout cela ne tourne sans base constituée. En pratique, tout passe par
`workflow_dispatch` sur `.github/workflows/quotidien.yml`, dont les entrées activent
chaque analyse. Le résultat se lit dans les logs du run.

**Avertissement qui vaut pour tout ce fichier** : quatre saisons évaluables, c'est
peu. Comparer beaucoup de variantes finit par élire celle qui colle le mieux à ces
quatre-là plutôt que celle qui généralise. D'où des listes de candidats courtes et
justifiées d'avance, et la pire saison comme juge plutôt que la moyenne.
