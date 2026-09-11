# Tempo EDF — prévision des 10 prochains jours

Prédit la couleur Tempo (Bleu / Blanc / Rouge) des 10 jours à venir, et garde la trace
de chaque prédiction pour mesurer sa fiabilité dans la durée.

## Ce que fait le modèle (et ce qu'il ne fait pas)

RTE ne publie officiellement que la couleur du **lendemain**, vers 11h. Au-delà, aucune
vérité n'existe : la page affiche donc des **probabilités**, pas des certitudes. Quand
l'annonce officielle de J+1 est tombée, elle est affichée telle quelle (badge « officiel RTE »)
et n'est pas comptée dans les statistiques de précision.

## Installation

Aucune clé d'API n'est nécessaire. Dépendances : `numpy`, `scikit-learn`, `joblib`, `flask`.

```bash
python ingest.py               # historique 2020->aujourd'hui (couleurs + météo), ~5 min
python ingest_renouvelables.py # indices éolien/solaire, ~15 min
python collector.py --train # entraîne le modèle et génère les prédictions du jour
python backfill.py          # (optionnel) remplit l'historique avec le backtest rejoué
python app.py               # ouvre http://127.0.0.1:5173
```

Mise à jour automatique quotidienne :

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_scheduler.ps1
```

## Sources de données

| Donnée | Source | Accès |
|---|---|---|
| Couleurs Tempo (depuis 2020) | api-couleur-tempo.fr | libre, sans clé |
| Météo observée (ERA5) | Open-Meteo archive | libre, sans clé |
| Météo *telle que prévue à l'époque* | Open-Meteo previous-runs | libre, sans clé |
| Prévision météo J+1→J+16 | Open-Meteo forecast | libre, sans clé |
| Éolien / solaire (`wind_speed_100m`, `shortwave_radiation`) | Open-Meteo | libre, sans clé |
| Consommation et production nationales réelles | éCO2mix (ODRÉ) | libre, sans clé |

### Charge résiduelle plutôt que froid brut

Ce qui déclenche un jour Rouge n'est pas le froid mais la **charge résiduelle** : ce que
le parc pilotable doit fournir, soit la consommation attendue moins l'éolien et le
solaire attendus. Un jour froid et venteux sollicite bien moins ce parc qu'un jour froid
sans vent, et le froid seul n'expliquait donc pas les jours Rouge.

Le calcul se fait en MW réels, pas en degrés-jours :

- un modèle de demande appris sur 2 198 jours d'éCO2mix estime la pointe nationale à
  partir du froid, du calendrier et de la tendance — 2 487 MW d'erreur moyenne (3,6 %)
  sur une saison jamais vue ;
- les indices météo sont convertis en MW par régression sur la production réelle. Cette
  étape vaut validation du proxy : l'indice éolien explique **77 %** de la production
  observée, avec une pente de 21 900 MW par point — l'ordre de grandeur du parc installé.

Tout est calé sur les seules données antérieures à la saison évaluée, sinon le backtest
se mentirait à lui-même.

Gain mesuré sur 5 saisons, à seuil identique : 79,8 → **86,8 jours Rouge anticipés sur
110**, sans perte de précision. Les cinq saisons progressent. Depuis, la contrainte de
quota a porté ce chiffre à **92,2 sur 110**.

## Mise en ligne (GitHub Pages)

Le site est publié sur <https://barjosky.github.io/tempo-app/>, servi par le dépôt
[tempo-app](https://github.com/Barjosky/tempo-app), qui contient aussi ce code.

La page est statique : `export_static.py` fige les réponses de l'API en JSON dans
`data/`, et `web/app.js` bascule automatiquement dessus quand aucun serveur Flask ne
répond. En local rien ne change — l'API est utilisée si elle est là.

`config.SITE_DIR` s'adapte à l'emplacement de la page : `web/` en local, la racine sur le
dépôt publié (contrainte de GitHub Pages). Le stockage interne — base SQLite et modèle —
vit dans `store/`, séparé du `data/` du site que l'export vide à chaque génération.

Le PC n'a plus besoin d'être allumé : `.github/workflows/quotidien.yml` collecte, prédit
et republie **deux fois par jour**. Le matin à 10h30 UTC, après l'annonce RTE de 11h —
soit 11h30 l'hiver, 12h30 l'été. Le soir à 17h00 UTC — 18h l'hiver, 19h l'été — pour
rattraper une prévision météo qui aurait bougé depuis le matin. Un `cron` ne connaissant
que l'UTC, l'horaire local glisse d'une heure entre les deux saisons ; la page, elle,
affiche le prochain passage dans le fuseau du lecteur. La base et le modèle
(23 Mo) restent hors de git, dans le cache Actions — les commiter chaque jour ferait
grossir l'historique d'environ 2 Go par an.

## Tarifs affichés

Chaque journée porte son prix du kWh — heures pleines et heures creuses — pour que la
couleur se lise en euros et pas seulement en code couleur. Un jour Rouge affiche en plus
son rapport au jour Bleu : à la grille d'août 2026, l'heure pleine rouge coûte **4,4 fois**
l'heure pleine bleue.

La grille vit dans `config.TARIFFS` : tarif réglementé TTC, puissance 9 kVA, heures creuses
22 h → 6 h (identiques partout en France sous l'option Tempo). **Elle doit être remise à jour
à chaque mouvement du tarif réglementé**, en général le 1er février et le 1er août ; la date
du barème est affichée sous le tableau pour que rien ne passe pour actuel à tort. Les prix
ne sont pas récupérés automatiquement : aucune source libre ne les publie de façon stable.

Le quota Bleu n'est pas un chiffre contractuel publié par EDF — c'est le solde des deux
autres sur la durée réelle de la saison : 300 jours d'ordinaire, 301 quand un 29 février
tombe dedans.

## Le seuil d'alerte Rouge est un arbitrage, pas un réglage

Annoncer un jour Rouge est un compromis entre en rater et en inventer. Mesuré sur
5 saisons, **sur les jours où le Rouge est possible** :

| Seuil | Rappel | Précision | Pire saison : précision | Pire saison : rappel | Fausses / échéance / hiver |
|---|---|---|---|---|---|
| 0,10 | 92 % | 55 % | 27 % | 65 % | 22,6 |
| **0,25** (retenu) | **84 %** | **67 %** | **42 %** | **48 %** | **11,9** |
| 0,30 | 81 % | 70 % | 48 % | 45 % | 9,8 |
| **0,40** (retenu) | **76 %** | **75 %** | **57 %** | **45 %** | **6,9** |
| 0,50 | 72 % | 81 % | 62 % | 45 % | 4,8 |
| 0,65 | 62 % | 90 % | 75 % | 42 % | 2,0 |

Deux colonnes méritent d'être lues ensemble. **Le rappel de la pire saison ne bouge
pratiquement plus entre 0,30 et 0,55** — il reste à 45 % — pendant que la précision de
cette même saison passe de 48 % à 67 %. Autrement dit, au-delà de 0,30, monter le seuil
ne coûte presque rien là où le modèle est le plus faible, et rapporte beaucoup. C'est ce
qui a fait retenir **0,40** plutôt que 0,25 : un jour Rouge de moins repéré par hiver,
contre une vingtaine de journées d'organisation inutile évitées.

Le contrepoint honnête : en euros, 0,25 reste l'optimum si une alerte inutile ne coûte
qu'un euro de gêne (`analyse_euros.py`). Le choix de 0,40 sacrifie donc quelques euros
par saison, délibérément — une alerte juste 42 % du temps finit par être ignorée, et une
alerte ignorée ne vaut rien.

Le détail par saison dit d'où vient la moyenne :

| Saison | Détectés | Fausses alertes | Rappel | Précision |
|---|---|---|---|---|
| 2021-2022 | 19,9/22 | 7,3 | 90 % | 73 % |
| 2022-2023 | 21,5/22 | 9,5 | 98 % | 69 % |
| 2023-2024 | 10,5/22 | 1,4 | 48 % | 88 % |
| 2024-2025 | 18,3/22 | 11,4 | 83 % | 62 % |
| 2025-2026 | 22,0/22 | **30,0** | 100 % | 42 % |
| **Total** | **92,2/110** | 11,9 | **84 %** | **67 %** |

(Détail mesuré au seuil 0,25, celui qui était en place lors du balayage.)

Les deux hivers extrêmes sont instructifs. En 2023-2024 le modèle est prudent et rate
la moitié des Rouge ; en 2025-2026 il les trouve **tous**, au prix de trente fausses
alertes — c'est la saison où treize jours de mars sont Rouge par arithmétique, et la
contrainte de quota l'y pousse.

Régénérer le tableau : `python analyse_seuils.py`. Le cache de probabilités se périme
tout seul si la configuration du modèle a changé depuis, `--refit` n'a pas à être
demandé de mémoire.

Un calage automatique de ce seuil a été essayé — sur une saison de validation, puis sur
plusieurs mises en commun. Les deux fois il s'effondrait au plancher et noyait la page sous
les fausses alertes : le coût d'une fausse alerte n'est pas mesurable par le modèle, il
n'appartient qu'à l'utilisateur. D'où un réglage explicite.

## Quel pourcentage, sur quel dénominateur ?

Un taux de réussite calculé sur l'année entière ne veut pas dire grand-chose : d'avril à
octobre la réponse est Bleu d'avance, et ces mois-là gonflent le score sans que le modèle
ait rien risqué. L'onglet Historique propose donc trois dénominateurs, et l'écart entre
eux est l'information :

| Période | Prédictions | Réussite | Ce qu'elle contient |
|---|---|---|---|
| Toute l'année | 14 614 | 89,6 % | flatteur — inclut les mois sans enjeu |
| Novembre → mars | 6 050 | 78,6 % | la fenêtre où un Rouge est possible |
| **Jours éligibles** | **4 190** | **72,2 %** | lundi-vendredi, nov-mars, hors fériés |

Plus de 18 points d'écart entre le premier chiffre et le dernier. C'est sur le dernier que
le modèle a réellement un choix à faire.

Ce dénominateur corrige aussi une lecture erronée. La précision paraissait *plate* d'une
échéance à l'autre — 91,2 % à J+1 contre 88,7 % à J+10 — ce qui laissait croire que le
modèle n'exploitait pas la précision des prévisions courtes. Sur les jours éligibles la
pente apparaît : **77,3 % à J+1 contre 69,2 % à J+10**, soit trois fois plus. La platitude
venait pour l'essentiel du dénominateur, pas du modèle. Le rappel Rouge, lui, reste bien
plat (76 % à J+1, 72 % à J+10) : cette part de l'anomalie tient toujours.

## Ce que ça rapporte, en euros

```bash
python analyse_euros.py --kwh 8 --gene 1.00
```

Le seuil d'alerte se réglait jusqu'ici à l'aveugle, faute de pouvoir chiffrer une fausse
alerte. La moitié du problème est pourtant chiffrable : décaler un kWh d'heure pleine
vers l'heure creuse un jour Rouge vaut **0,568 €**. Ce qui ne l'est pas, c'est la *gêne*
de s'être organisé pour rien — alors on ne l'invente pas, on balaye sa valeur et on
regarde comment le seuil optimal se déplace avec elle.

Le script compare aussi trois stratégies à rendement égal : tout décaler tous les jours,
suivre le modèle, ou disposer d'un oracle. Ce qui compte n'est pas le total en euros
(tout décaler gagne toujours le plus, au prix d'une année entière de contrainte) mais ce
que rapporte **chaque jour de contrainte consenti**.

## Fiabilité des probabilités

L'onglet Historique compare désormais, tranche par tranche, la probabilité de Rouge
annoncée à la fréquence réellement observée. Le constat, sur les jours éligibles :

- **en dessous de 30 %, le modèle est trop prudent** — il annonce 15 %, il tombe 21 % ;
  il annonce 25 %, il tombe 33 % ;
- **entre 40 et 70 %, il est trop sûr de lui** — il annonce 55 %, il tombe 40 % ; il
  annonce 65 %, il tombe 48 %.

Ces écarts de 15 points passent tout juste le critère de viabilité (< 20 points). Ils
n'étaient visibles nulle part avant.

## L'hiver 2025-2026, ou pourquoi une fin de saison n'est pas météorologique

Sur les jours où le Rouge est possible, le modèle faisait 66,8 % de réussite en
2024-2025 et **51,4 %** en 2025-2026. La cause n'a rien à voir avec le temps qu'il a
fait.

| Saison | Déc | Jan | Fév | Mars | Marge minimale | Jours **forcés** |
|---|---|---|---|---|---|---|
| 2023-2024 | — | — | — | — | 0 le 29/03 | 1 |
| 2024-2025 | 8 | 13 | 1 | 0 | 1 le 31/03 | 0 |
| **2025-2026** | 2 | 6 | 1 | **13** | **0 le 13/03** | **13** |

EDF est entré dans mars 2026 avec treize Rouge non consommés. Le 13 mars, il restait
exactement treize jours éligibles pour treize Rouge à placer : **à partir de cette
date, chaque jour restant était Rouge par arithmétique**, quelle que soit la météo.
Le modèle, lui, annonçait Bleu — entraîné sur des hivers soldés dès février, il
n'avait jamais rencontré une fin de saison sous cette contrainte.

`rouge_pressure` portait pourtant l'information, et c'est même la seule feature
individuellement porteuse des soixante. Mais un arbre ne sait pas extrapoler : au-delà
de la dernière valeur rencontrée à l'entraînement, il rend toujours la même feuille.

D'où le choix d'en faire une **règle plutôt qu'un apprentissage**. Quand la marge de
placement tombe à zéro, `constrain()` impose le Rouge, comme le masque contractuel
interdit déjà le Rouge un dimanche. Le contrat n'est pas qu'une liste d'interdits : il
oblige aussi. Mesuré, cela fait passer 2025-2026 de 1,085 à 0,900 de log-loss et le
rappel Rouge de 72 % à 82 %.

Deux détails qui comptent : le masque garde la priorité, donc un dimanche reste Bleu
même sous quota tendu ; et donner la marge au modèle comme simple *feature* ne suffit
pas — mesurée seule, elle ne relève pas le plancher. C'est la contrainte qui paie.

## Ce que chaque groupe de features apporte réellement

Mesuré par permutation sur trois saisons, jours éligibles uniquement. La colonne qui
tranche est la **pire saison** : un groupe qui aide en moyenne mais nuit à un hiver
n'est pas fiable, il est chanceux.

| Groupe | +log-loss | bruit | pire saison | Verdict |
|---|---|---|---|---|
| **quotas** | **+0,120** | 0,025 | **+0,016** | **porteur** |
| hiver restant | +0,078 | 0,017 | −0,015 | instable |
| météo brute | +0,060 | 0,026 | −0,076 | instable |
| charge résiduelle | +0,055 | 0,016 | −0,094 | instable |
| renouvelables | +0,021 | 0,020 | −0,016 | indistinct |
| horizon · RTE J+1 · froid relatif · calendrier | ≈ 0 | — | — | indistinct |
| **offre (nucléaire)** | **−0,032** | 0,017 | −0,091 | **nuit au modèle** |

**Un seul groupe est solidement porteur : les quotas.** Le modèle roule sur l'état de la
saison — combien de Rouge restent, depuis quand, sous quelle pression — bien plus que sur
la météo. `rouge_pressure` est d'ailleurs la seule feature individuelle porteuse sur les
soixante (+0,080, positive dans les trois saisons).

Ce que ça dit des trois signaux ajoutés :

- **la disponibilité nucléaire nuit** : pire groupe des dix, négatif partout. L'idée était
  bonne — un jour Rouge naît d'une marge tendue — mais la production appelée est un proxy
  trop grossier de la puissance disponible.
- **l'hiver restant est le plus prometteur** des trois (2ᵉ groupe), sans être fiable :
  il abîme une saison sur trois.
- **la prévision RTE J+1 ressort à zéro, mais la mesure est injuste** : cette feature
  n'existe qu'à une échéance sur dix, donc permuter sa colonne ne touche que 10 % des
  lignes. Il faudrait la mesurer à J+1 seulement pour conclure.

Les composantes de la météo se masquent entre elles : le groupe aide, mais `hdd_f`,
`temp_anomaly` et `hdd_rank_window` sont individuellement du poids mort — elles encodent
toutes la même température. C'est le piège de la mesure feature par feature, et la raison
pour laquelle le tableau par groupe passe en premier.

Les signaux en question :

- **Le côté offre.** Tout le reste décrit la demande, alors qu'un jour Rouge naît d'une
  *marge* tendue. La puissance nucléaire récemment appelée (`nucleaire` d'éCO2mix, donc
  toujours sans clé) et son écart au niveau habituel du mois donnent enfin une idée de ce
  que le parc peut fournir — ce que le froid seul n'explique pas.
- **La prévision de consommation de RTE**, déjà collectée mais jamais lue. Elle n'existe
  qu'à J+1 et reste NaN au-delà : la propager donnerait au backtest une information que
  la production n'aura jamais.
- **L'hiver restant.** Les features comparaient le jour à la saison *écoulée* ; celle-ci
  le compare à ce qu'il *reste*, estimé sur les seules saisons antérieures. C'est
  l'arbitrage qu'EDF fait — ce jour froid mérite-t-il un Rouge, ou en reste-t-il assez de
  plus froids pour dépenser le quota plus tard ?

Pour savoir ce que chaque feature apporte réellement :

```bash
python diagnostic_features.py            # par groupe, puis une par une
python diagnostic_features.py --groupes  # groupes seulement, rapide
```

La méthode est la permutation : on détruit une colonne du jeu de test en la mélangeant,
et on mesure ce que le modèle perd. Trois colonnes décident de la lecture — la perte
moyenne, le **bruit** de permutation (en dessous, on ne mesure rien) et la **pire
saison** (une feature qui sauve un hiver et en abîme un autre est instable, pas utile).
Distinguer le bruit de permutation de l'écart entre saisons est ce qui rend le verdict
lisible : confondus, tout paraît insignifiant.

## Honnêteté du backtest

Le piège classique serait de rejouer le passé avec la météo réellement observée : le modèle
disposerait d'une prévision parfaite qu'il n'aura jamais en production, et les scores seraient
gonflés. Ici, chaque prédiction à horizon *h* n'utilise que la météo **archivée telle qu'elle
était prévue *h* jours à l'avance** (`weather.lead = h`). Les saisons antérieures à 2022, pour
lesquelles cette archive n'existe pas, servent à l'entraînement avec une dégradation calibrée
sur l'erreur de prévision réellement mesurée, et sont exclues de l'évaluation.

```bash
python -m src.backtest   # rejeu walk-forward + critères de viabilité
python run_tests.py      # règles métier + pipeline
```

**Le backtest ne note que les jours où le Rouge est possible.** L'entraînement, lui,
continue de voir toute l'année : les jours sans enjeu portent l'état des quotas et la
dynamique de la saison, dont le modèle a besoin. Seule la note est restreinte, parce
qu'un score calculé sur des journées dont la réponse est connue d'avance ne mesure
rien. Le même périmètre sert à l'onglet Historique, et un test verrouille le fait que
les deux implémentations — le prédicat Python et le filtre SQL — désignent bien les
mêmes jours.

`run_tests.py` mêle deux familles. `tests/test_rules.py` confronte les règles Tempo aux
**vraies** couleurs collectées : sans base constituée il se saute, plutôt que d'échouer
pour une raison qui n'est pas un bug. `tests/test_pipeline.py` tourne sur une base
synthétique (`tests/fixtures.py`) et doit passer partout — il vérifie ce qui casse en
silence : une feature ajoutée sans son nom, une valeur qui regarde vers l'avenir, un
filtre qui ne filtre pas.

## Règles Tempo encodées (vérifiées sur 2020-2026)

- Dimanche : toujours Bleu.
- Samedi : Blanc possible (33 cas observés), Rouge impossible.
- Jour férié : Blanc possible, Rouge impossible.
- Rouge : lundi-vendredi, novembre à mars uniquement.
- Quotas 300 Bleu / 43 Blanc / 22 Rouge par saison (1er sept → 31 août).

Ces contraintes sont appliquées **après** le modèle : une couleur impossible voit sa
probabilité mise à zéro, le reste est renormalisé.

## Historique des mesures

`JOURNAL.md` garde la trace de ce qui a été mesuré, retenu et **écarté** — avec les
chiffres. `config.py` dit ce que le modèle fait ; le journal dit pourquoi, et ce qui
avait été essayé avant. Sans lui, une piste déjà mesurée sans succès se represente tôt
ou tard comme une bonne idée neuve.

## Structure

```
ingest.py      collecte historique (à lancer une fois)
collector.py   pipeline quotidien : couleurs + météo + prédictions figées
app.py         serveur web (onglets Prévisions / Historique)
backfill.py    rejoue le backtest dans l'historique de la page
analyse_seuils.py  balayage des seuils d'alerte Rouge et Blanc sur 5 saisons
analyse_euros.py   ce que la prediction rapporte, et a quel prix en contrainte
diagnostic_features.py  ce que chaque feature apporte, par permutation
src/rules.py   contraintes contractuelles Tempo
src/features.py construction des features, strictement point-in-time
src/model.py   baseline climatologique + GBM calibré
src/backtest.py walk-forward + critères de viabilité
```
