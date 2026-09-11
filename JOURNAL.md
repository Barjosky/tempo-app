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
| Jours éligibles | 106 | 52 (49 %) | 32 (30 %) | 22 (21 %) |

Sur un jour ouvré d'hiver, c'est **pile ou face** : une journée sur deux est déjà chère.
Sur les 43 Blanc d'une saison, 37 tombent nov-mars ; les six autres débordent la
fenêtre (avril, octobre), où aucun Rouge n'est possible.

**Les deux couleurs ne culminent pas le même mois** (moyenne sur six saisons) :

| Mois | Bleu | Blanc | Rouge |
|---|---|---|---|
| novembre | 84 % | 14 % | 1 % |
| décembre | 53 % | 32 % | 15 % |
| **janvier** | 39 % | 25 % | **36 %** |
| **février** | 55 % | **37 %** | 8 % |
| mars | 72 % | 17 % | 11 % |

Janvier est le mois du Rouge — 11,2 des 22 y tombent, la moitié du quota. Février est
celui du Blanc, avec le Rouge déjà presque épuisé. Un modèle qui ignore la date se prive
de ce décalage d'un mois entre les deux pics.

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
| Modèle à deux étages (tendu ? puis Blanc ou Rouge ?) | moyenne 0,670 → 0,709, **pire saison 0,902 → 1,104** | écarté |
| Arbitrage Blanc/Rouge (`quota_arbitrage`) | rappel Blanc 44 → 46 %, **pire saison 0,911 → 0,942** | écarté |
| Un modèle par bande d'échéance | moyennes toutes meilleures, **pire saison 0,909 → 1,028** | écarté |

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

## Le modèle à deux étages : mesuré, écarté

L'idée visait la dernière faiblesse connue : 220 jours Blanc annoncés Rouge. Premier
étage « journée tendue ? », second « Blanc ou Rouge ? » entraîné sur les seuls jours
tendus, où l'arbitrage est équilibré (32 Blanc contre 22 Rouge) au lieu d'être noyé
sous le Bleu.

| Saison | 1 étage | 2 étages | |
|---|---|---|---|
| 2022-2023 | **0,343** | 0,356 | |
| 2023-2024 | 0,807 | **0,771** | |
| 2024-2025 | 0,626 | **0,604** | |
| 2025-2026 | **0,902** | 1,104 | ← la saison sous contrainte de quota |
| **Pire saison** | **0,902** | 1,104 | |

Il gagne sur trois saisons et perd lourdement sur la quatrième — précisément celle où
treize jours de mars sont Rouge par arithmétique. Découper la décision en deux coupe
aussi la contrainte de quota en deux : le premier étage sait qu'une journée est tendue,
le second arbitre sans voir que le calendrier a déjà tranché.

Il rapporte tout de même **+3 points de précision d'alerte** (65 % → 68 %) contre
−5 points de rappel (82 % → 77 %). Un utilisateur qui préfère moins d'alertes mais plus
sûres y gagnerait ; celui qui veut ne pas rater un Rouge y perd. Le plancher décidant
ici, `config.TWO_STAGE` reste à `False`.

## Le Blanc : la faiblesse qui reste

C'est la seule couleur sous les 50 %, et de loin :

| Réel | Bleu | Blanc | Rouge | rappel |
|---|---|---|---|---|
| Bleu | 1699 | 242 | 149 | 81 % |
| **Blanc** | **304** | **552** | **364** | **45 %** |
| Rouge | 52 | 105 | 723 | 82 % |

**L'erreur se partage en deux moitiés presque égales** : 30 % des Blanc sont annoncés
Rouge, mais 25 % sont annoncés **Bleu**. Ce n'est donc pas seulement une hésitation
avec le Rouge — c'était une lecture trop rapide, faite en ne regardant que les fausses
alertes. Une piste qui ne viserait que la frontière Blanc/Rouge peut corriger une
moitié en aggravant l'autre, d'où l'affichage des deux directions dans le comparatif.

### L'arbitrage entre les deux quotas : mesuré, écarté

`rouge_pressure` compte le quota restant sur les jours **éligibles** jusqu'au 31 mars ;
`blanc_pressure` le comptait sur les jours de **calendrier** jusqu'au 31 août. Au
13 mars 2026 : 13 jours d'un côté, 147 de l'autre. Leur rapport — quel quota est le
plus rare aujourd'hui — ne voulait donc rien dire.

Mesuré sur une fenêtre commune, il fait exactement ce pour quoi il est conçu, et ça ne
suffit pas :

| | rappel B | B vu Bleu | B vu Rouge | pire saison |
|---|---|---|---|---|
| sans arbitrage | 44 % | 25 % | 31 % | **0,911** |
| avec arbitrage | **46 %** | 24 % | 30 % | 0,942 |

Les deux directions d'erreur reculent ensemble — le signal est donc réel — mais
2023-2024 passe de 0,846 à 0,942, et le rappel Rouge perd deux points. Deux points de
Blanc contre le plancher : refusé. Même profil que le modèle à deux étages.

### La correction du dénominateur, elle, est conservée

Aucun Blanc ne peut tomber un dimanche ; les compter était faux. Mais il faut savoir ce
que cette correction vaut : **corrélation de rang 0,999992** entre l'ancienne et la
nouvelle définition, et l'ordre de deux jours ne s'inverse que dans **0,19 %** des cas.
Un arbre ne voit que l'ordre — la correction est donc juste et pratiquement sans effet.

Ce chiffre sert surtout à ne pas se tromper de cause : le plancher a bougé de 0,902 à
0,911 entre deux mesures, et ce n'est **pas** ce changement qui l'explique. Les deux
runs ne tournaient pas sur la même base (la consommation avait été recollectée entre
les deux). Deux mesures prises sur des données différentes ne se comparent pas.

## La prévision de RTE : mesurée à son échéance, sans valeur

Elle est publiée la veille pour le lendemain, donc elle n'existe **qu'à J+1**. La
permutation la jugeait pourtant sur les dix échéances, où elle est NaN neuf fois sur
dix : détruire sa colonne ne touchait qu'un dixième des lignes, et elle sortait
mécaniquement en poids mort. **Le verdict portait sur sa rareté, pas sur sa valeur.**

Mesurée à J+1 seulement, la réponse ne change pas :

| | +log-loss | bruit | pire saison |
|---|---|---|---|
| groupe « prévision RTE » | **−0,004** | 0,005 | −0,012 |
| `rte_forecast_mw` | +0,0005 | 0,0006 | +0,0001 |
| `rte_forecast_gap` | −0,006 | 0,004 | −0,017 |

Sans valeur, cette fois pour de bon. L'explication tient au tableau voisin :
`residual_mw` vaut **+0,110** à J+1. La consommation attendue est déjà connue par le
modèle de demande maison, calibré sur éCO2mix — la prévision de RTE ne dit rien
qu'il ignore. Piste close.

### Couper l'apprentissage par échéance : mesuré, écarté

La suite logique de ce qui précède : si une colonne vaut à J+1 ce qu'elle ne vaut plus
à J+10, donnons au modèle deux apprentissages séparés plutôt qu'un seul où il doit
redécouvrir l'interaction dans chaque branche.

| | moyenne | **pire saison** | exactitude | rappel R | préc. R | calibration |
|---|---|---|---|---|---|---|
| échéances mélangées | **0,681** | **0,909** | 72,1 % | 74 % | 74 % | 9,6 % |
| coupure à J+3 | 0,757 | 1,142 | 72,6 % | 75 % | 75 % | 9,2 % |
| coupure à J+5 | 0,730 | 1,028 | **72,7 %** | 75 % | 75 % | **9,2 %** |

**Toutes les moyennes s'améliorent** — exactitude, rappel, précision, calibration — et
pourtant c'est refusé. Le détail par saison dit pourquoi :

| | 2022-2023 | 2023-2024 | 2024-2025 | 2025-2026 |
|---|---|---|---|---|
| mélangées | 0,345 | **0,841** | 0,628 | **0,909** |
| coupure J+3 | 0,352 | 1,142 | **0,593** | 0,939 |
| coupure J+5 | 0,370 | 0,939 | **0,585** | 1,028 |

2024-2025 gagne nettement (0,628 → 0,585) et 2023-2024 explose (0,841 → 1,142). Deux
modèles voient chacun moins de lignes, et la fragilité se paie sur la saison où le
modèle était déjà le plus juste.

**Ce que ça ne dit pas.** Ce n'est *pas* un démenti de l'idée que le froid pilote les
jours Rouge — cette part reste mesurée et vraie à J+1. C'est un démenti du **mécanisme**
choisi pour l'exploiter : séparer les jeux d'entraînement fragmente les données plus
qu'il ne spécialise les modèles.

**Piste qui reste ouverte** : une version qui ne fragmente pas. Bandes chevauchantes, ou
un modèle unique complété d'une correction apprise sur les seules échéances courtes.
Les moyennes disent qu'il y a quelque chose à prendre ; la pire saison dit que ce n'est
pas comme ça.

### Ce que J+1 révèle en passant

Le classement change beaucoup quand on ne regarde que l'échéance la plus fiable :

| Groupe | toutes échéances | à J+1 |
|---|---|---|
| charge résiduelle | +0,055, **pire saison −0,094** | +0,164, **pire saison +0,024** |
| hiver restant | +0,078, instable | +0,181, **porteur** |
| météo brute | +0,060, instable | +0,111, indistinct |

**La charge résiduelle passe d'instable à solidement porteuse.** C'est cohérent : à
J+1 la prévision de consommation est juste, à J+10 c'est du bruit. La même colonne
n'a pas la même valeur selon l'échéance — et le modèle unique les traite pourtant
toutes pareil. Piste sérieuse, non explorée.

## Le seuil d'alerte, recalé sous contrainte de quota

Balayage sur 5 saisons, jours éligibles. Les deux colonnes de droite sont celles qui
comptent, puisque c'est la pire saison qui décide ici :

| Seuil | Rappel | Précision | Pire : précision | Pire : rappel | Fausses / éch. / hiver |
|---|---|---|---|---|---|
| 0,10 | 92 % | 55 % | 27 % | 65 % | 22,6 |
| 0,25 (ancien) | 84 % | 67 % | 42 % | 48 % | 11,9 |
| 0,30 | 81 % | 70 % | 48 % | 45 % | 9,8 |
| **0,40** (retenu) | 76 % | 75 % | **57 %** | 45 % | 6,9 |
| 0,50 | 72 % | 81 % | **62 %** | 45 % | 4,8 |
| 0,65 | 62 % | 90 % | 75 % | 42 % | 2,0 |

**Le rappel de la pire saison est plat de 0,30 à 0,55** — 45 % partout — pendant que sa
précision monte de 48 % à 67 %. Au-delà de 0,30, monter le seuil ne coûte donc presque
rien là où le modèle est le plus faible, et rapporte beaucoup.

**Décision : 0,25 → 0,40.** Un jour Rouge de moins repéré par hiver, contre une
vingtaine de journées d'organisation inutile évitées. En euros c'est un léger recul
assumé (voir plus bas) : une alerte juste 42 % du temps finit ignorée, et une alerte
ignorée ne vaut rien.

**Effet mesuré après rejeu du backtest.** L'effet de bord attendu s'est produit, et il
est plus large que le coût :

| Sur les jours éligibles | seuil 0,25 | seuil 0,40 |
|---|---|---|
| Exactitude globale | 71,0 % | **72,2 %** |
| Rappel Bleu | 81 % | 82 % |
| **Rappel Blanc** | 45 % | **53 %** |
| Rappel Rouge | 82 % | 74 % |
| **Précision des alertes Rouge** | 58 % | **68 %** |
| Nombre d'alertes Rouge | 1241 | 961 |

Le gain du Blanc vient exactement d'où il était prévu : la part des Blanc annoncés Rouge
tombe de **30 % à 20 %**, ce sont les journées que le Rouge ne vole plus. La part
annoncée Bleu bouge à peine (25 → 27 %), ce qui confirme le mécanisme : `decide()`
promeut le Blanc puis laisse le Rouge écraser par-dessus, et il écrase moins.

Ce que je n'avais pas prévu : **l'exactitude globale monte** (71,0 → 72,2 %). Le Blanc
étant plus fréquent que le Rouge sur les jours éligibles (30 contre 22), lui rendre
8 points de rappel rapporte plus que les 8 points perdus sur le Rouge.

Le détail par saison montre deux régimes opposés :

| Saison | Détectés | Fausses alertes | Rappel | Précision |
|---|---|---|---|---|
| 2021-2022 | 19,9/22 | 7,3 | 90 % | 73 % |
| 2022-2023 | 21,5/22 | 9,5 | 98 % | 69 % |
| 2023-2024 | 10,5/22 | 1,4 | 48 % | 88 % |
| 2024-2025 | 18,3/22 | 11,4 | 83 % | 62 % |
| 2025-2026 | 22,0/22 | **30,0** | 100 % | 42 % |
| **Total** | **92,2/110** | 11,9 | 84 % | 67 % |

En 2023-2024 le modèle est prudent et rate la moitié des Rouge ; en 2025-2026 il les
trouve tous, au prix de trente fausses alertes. C'est la saison des treize jours de mars
forcés par arithmétique — la contrainte de quota l'y pousse, et c'est le prix du gain de
rappel obtenu ailleurs.

### En euros, à J+1

`analyse_euros.py`, 8 kWh décalables, gain de 0,568 € par kWh un jour Rouge :

| Gêne consentie / alerte inutile | Seuil optimal | € / saison | Alertes | Justes | Rouge ratés |
|---|---|---|---|---|---|
| 0,00 € | 0,05 | 99 € | 46 | 20 | 2 |
| 0,50 € | 0,10 | 86 € | 40 | 20 | 2 |
| **1,00 €** | **0,25** | 81 € | 28 | 19 | 3 |
| 2,00 € | 0,30 | 74 € | 26 | 19 | 3 |
| 5,00 € | 0,55 | 63 € | 18 | 16 | 6 |

**Le seuil de 0,25 est exactement l'optimum si une alerte inutile vaut 1 € de gêne.**
Il n'avait pas été choisi comme ça — c'est une confirmation indépendante, pas une
justification rétrospective.

Ce que rapporte un jour de contrainte consenti :

| Stratégie | € / saison | Jours gênés | € / jour gêné |
|---|---|---|---|
| tout décaler, tous les jours | 185 € | 365 | 0,51 € |
| suivre le modèle (seuil 0,25) | 90 € | 28 | 3,25 € |
| oracle (les 22 vrais Rouge) | 100 € | 22 | 4,54 € |

Le modèle capte **90 % des euros de l'oracle** pour six jours de contrainte de plus.

### Le seuil Blanc

| Seuil | Rappel Blanc | Précision Blanc | Bleu abîmés |
|---|---|---|---|
| 0,30 | 79 % | 53 % | 13,3 |
| **0,40** (retenu) | 65 % | 57 % | 9,2 |
| 0,50 | 52 % | 61 % | 6,2 |

Rien ne pousse à en changer : la précision progresse lentement, le rappel chute vite.

**Piège de lecture à connaître** : ce tableau compte les jours dont la probabilité de
Blanc dépasse le seuil, alors que `decide()` laisse ensuite le Rouge écraser certains
d'entre eux. Les 65 % de rappel affichés ici ne sont donc pas les 45 % réellement
obtenus dans la matrice de confusion — l'écart, ce sont les jours Blanc volés par le
Rouge. **Le rappel du Blanc dépend du seuil Rouge autant que du sien.**

## L'API RTE des indisponibilités : sondée avant d'être exploitée

Seule source du projet qui demande une clé. Sondée avant d'écrire la moindre collecte,
parce qu'une seule réponse pouvait tout invalider.

| Question | Réponse |
|---|---|
| Authentification | **OK** — l'URL du jeton, absente du Swagger, était bien sur le même hôte |
| Profondeur d'historique | **2020 → 2026, six hivers.** Suffisant |
| Volume nucléaire | **420 arrêts sur 790** en 30 jours, toutes filières |
| `publication_date` | **790/790 remplis.** La reconstitution point-in-time est possible |

Arrêts par année (une semaine de janvier) : 387, 188, 241, 311, 372, 339, 240.
Répartition : 662 programmés contre 128 fortuits.

**Le format de date a coûté deux runs**, et la leçon n'est pas le format :

| Envoyé | Réponse de RTE |
|---|---|
| `2026-09-11T00:00:00+02:00` | refus `UNADINFO_GENUN_F03` |
| `2026-09-11T00:00:00Z` | **OK — 17 arrêts** |
| `2026-09-11` | refus |

La première sonde envoyait tous les paramètres d'un coup, recevait un 400, et
concluait « API inexploitable ». Deux fautes : un 400 dit *mal formé*, pas *pas de
données* — et surtout le code **jetait le corps de la réponse**, là où RTE explique
son refus. Diagnostic supprimé, puis deviné à sa place. Remplacé par une échelle qui
part de la requête nue et ajoute un paramètre à la fois.

### Le piège, et comment il a été refermé

`last_version=true` rend la **dernière** version de chaque arrêt — donc corrigée après
coup. L'utiliser pour reconstituer le passé ferait fuiter du futur dans le backtest,
exactement comme une prévision météo corrigée. La collecte garde donc **toutes** les
versions (`last_version=false`, fenêtres en `PUBLICATION_DATE`) et l'état se rejoue
chronologiquement : à aucun instant il ne contient une révision publiée plus tard.

Ce que ça donne en base : **135 913 arrêts, 50 340 distincts — 2,7 versions par arrêt
en moyenne**, dont 10 411 nucléaires, publiés du 1ᵉʳ janvier 2020 au 26 août 2026. Ces
2,7 versions sont exactement ce qui aurait fuité sans la précaution.

Deux refus de plus, tous deux payés d'un run :

| Envoyé | Réponse |
|---|---|
| `end_date` = demain, en `PUBLICATION_DATE` | refus `UNADINFO_GENUN_F02` — *publication dates must be in the past* |
| `continuation_token` en paramètre d'URL | à éviter : RTE l'attend en **en-tête** et refuse tout paramètre inconnu |

La borne de fin est donc l'instant courant moins une minute. Reculer à minuit évitait
aussi le refus mais perdait les déclarations de la matinée — donc les avaries
fortuites, celles qui apportent le plus d'information. Trois tests verrouillent tout
ça : la borne, le suffixe `Z`, et le jeton qui ne doit pas fuiter dans l'URL.

**Et une leçon d'architecture, pas de format** : l'échec d'une seule fenêtre sur 82 a
fait sauter collecte, entraînement, export et publication du jour. Une source
facultative ne doit pas pouvoir faire ça. Elle prévient désormais et rend la main ; le
rattrapage repart de la dernière publication *en base*, donc il repasse tout seul sur
le trou.

## Le calendrier des indisponibilités : mesuré, RETENU

Tout le côté offre était jusqu'ici **rétrospectif**. `nuclear_recent_mw` lit ce que le
parc a produit les quatorze derniers jours : si douze réacteurs s'arrêtent mardi, la
colonne l'apprend mardi. La question posée est pourtant « que se passera-t-il dans dix
jours ». Le calendrier RTE, lui, publie les arrêts programmés des mois à l'avance.

Cinq colonnes : puissance nucléaire annoncée à l'arrêt le jour J, son écart à la
normale du mois, la part fortuite, le total toutes filières, la marge qui en découle
face à la charge résiduelle prévue.

| | log-loss moy | **PIRE saison** | exactitude | rappel R | préc. R | écart calib. |
|---|---|---|---|---|---|---|
| Sans le calendrier RTE | 0,688 | **0,912** | 72,1 % | 75 % | 74 % | 9,7 % |
| Avec le calendrier RTE | 0,684 | **0,883** | 72,6 % | 75 % | 75 % | 9,5 % |

**Le plancher se relève : 0,912 → 0,883.** C'est le juge du projet, et il tranche pour.

Le Blanc, faiblesse numéro un, progresse dans les deux directions d'erreur à la fois —
ce qui est le signe qu'on a ajouté de l'information, pas déplacé un arbitrage :

| | rappel B | préc. B | B vu Bleu | B vu Rouge |
|---|---|---|---|---|
| Sans | 54 % | 58 % | 27 % | 20 % |
| Avec | **57 %** | 58 % | **25 %** | **18 %** |

Détail par saison — et c'est là que l'honnêteté impose de ralentir :

| | 2022-2023 | 2023-2024 | 2024-2025 | 2025-2026 |
|---|---|---|---|---|
| Sans | **0,342** | **0,868** | 0,630 | 0,912 |
| Avec | 0,359 | 0,878 | **0,615** | **0,883** |

Deux saisons gagnent, deux perdent. Les deux qui perdent sont les deux plus faciles ;
les deux qui gagnent sont les deux plus dures, dont la pire. Le gain est **modeste et
concentré**, pas général.

Et un résultat que je n'avais pas prévu : **2022-2023 se dégrade**, alors que c'est
l'hiver de la corrosion sous contrainte, celui où le parc s'est effondré — donc
précisément celui où ces colonnes auraient dû briller. À ce stade je n'ai pas
d'explication mesurée, seulement une hypothèse : cet hiver-là, l'indisponibilité était
si générale qu'elle ne discriminait plus les jours entre eux.

### Ce que contiennent vraiment ces colonnes

135 913 arrêts avaient rendu 135 934 paliers, soit **un pour un** : ou bien chaque
version d'arrêt ne porte qu'un palier, ou bien `values` est absent de la réponse et le
repli sur la puissance **installée** surestime chaque arrêt partiel. Le backtest aimait
ces colonnes sans qu'on sache ce qu'elles contenaient.

Mesure, lue en base plutôt que dans les logs de collecte :

```
136 750 paliers · 50 574 arrêts · 18 versions à plusieurs paliers
70 % à la puissance installée entière · 0 sans puissance
```

**Ce que ça tranche.** `values` existe et est exploité : **30 % des paliers portent une
puissance différente de la puissance installée**, ce que le repli ne peut pas produire —
il ne sait écrire que `installed_mw`. Et aucun palier n'est muet. La granularité
temporelle fine passe donc par les **versions** (2,7 par arrêt), pas par `values` : 18
versions multi-paliers sur 136 750, c'est négligeable.

**Ce que ça ne tranche pas.** Les 70 % restants sont ambigus : un réacteur complètement
à l'arrêt donne légitimement indisponible = puissance installée. Le discriminant ne
sépare pas ce cas-là du repli. Borne haute du repli : 70 % ; borne basse : 0 %. Pour
fermer la question il faudrait marquer l'origine de chaque ligne à l'écriture — non
fait, parce que le risque résiduel est borné (un repli surestime un arrêt *partiel*, et
les arrêts partiels sont minoritaires dans un parc nucléaire).

## Le compte à rebours annonçait une heure fausse de 2 à 4 heures

Il visait le `cron` GitHub. Mesure sur les quatre passages programmés (API Actions) :

| Cron demandé | Départ réel (UTC) | Retard |
|---|---|---|
| 10:30 → 09/09 | 14h41 | **4 h 11** |
| 10:30 → 10/09 | 14h32 | **4 h 02** |
| 10:30 → 11/09 | 14h30 | **4 h 00** |
| 17:00 → 11/09 | 19h27 | **2 h 27** |

Dispersion du passage du matin : **11 minutes sur trois jours**. Un retard de quatre
heures aussi stable n'est pas un aléa de charge — c'est la cadence du service.

Le compteur tombait donc à zéro, affichait « en cours » un quart d'heure, puis repartait
vers le passage suivant, alors que rien n'avait bougé et ne bougerait pas avant des
heures. **Un garde-fou qui repose sur une promesse que personne ne tient n'en est pas
un** — c'est la même leçon que le numéro de version à incrémenter à la main.

Corrigé par la mesure : la table `runs` enregistre l'instant réel de chaque passage,
`src/cadence.py` en tire cron + médiane des retards, et la page annonce ça. Avec deux
règles d'honnêteté : aucun compte à rebours tant qu'un passage n'a pas
**3 mesures et moins de 2 h de dispersion** (on affiche alors « dernière mise à jour il
y a X », vrai par construction) ; et passé l'heure attendue, on n'enchaîne pas sur le
passage suivant — on annonce une mise à jour imminente et on guette les données.

Les essais manuels (`workflow_dispatch`) sont exclus de la médiane : un run lancé à 3 h
du matin n'a rien à dire sur la cadence quotidienne.

## Pistes non explorées
- **Une feature n'a pas la même valeur à chaque échéance** (voir ci-dessus) : la charge
  résiduelle est porteuse à J+1 et instable à J+10, et un modèle unique les traite
  pareil. `horizon` est bien une colonne, mais un arbre doit alors dépenser sa capacité
  à réapprendre cette interaction partout.

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
