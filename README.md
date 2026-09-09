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
110**, sans perte de précision. Les cinq saisons progressent.

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
et republie chaque jour à 10h30 UTC, après l'annonce RTE de 11h. La base et le modèle
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

Annoncer un jour Rouge est un compromis entre en rater et en inventer. Mesuré sur 5 saisons :

| Seuil | Rouges détectés | Fausses alertes / échéance / hiver | Précision |
|---|---|---|---|
| 0,10 | 90 % | 22 | 54 % |
| **0,25** (retenu) | **79 %** | **13** | **64 %** |
| 0,30 | 76 % | 11 | 66 % |
| 0,50 | 61 % | 5 | 75 % |

Réglable dans `config.ROUGE_ALERT_THRESHOLD`. Pour revoir le tableau complet avant de
changer : `python analyse_seuils.py` (instantané, relit des probabilités mises en cache ;
`--refit` pour les recalculer).

Un calage automatique de ce seuil a été essayé — sur une saison de validation, puis sur
plusieurs mises en commun. Les deux fois il s'effondrait au plancher et noyait la page sous
les fausses alertes : le coût d'une fausse alerte n'est pas mesurable par le modèle, il
n'appartient qu'à l'utilisateur. D'où un réglage explicite.

## Quel pourcentage, sur quel dénominateur ?

Un taux de réussite calculé sur l'année entière ne veut pas dire grand-chose : d'avril à
octobre la réponse est Bleu d'avance, et ces mois-là gonflent le score sans que le modèle
ait rien risqué. L'onglet Historique propose donc trois dénominateurs, et l'écart entre
eux est l'information :

| Période | Ce qu'elle contient |
|---|---|
| Toute l'année | flatteur — inclut les mois sans enjeu |
| Novembre → mars | la fenêtre où un Rouge est possible |
| Jours éligibles | lundi-vendredi, novembre à mars, hors fériés — **le seul honnête** |

C'est sur le dernier que le modèle a réellement un choix à faire, et c'est là qu'apparaît
la pente par échéance que la moyenne annuelle masquait entièrement.

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

## Où en est le modèle

Trois signaux ont été ajoutés là où les mesures montraient un manque :

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

Pour savoir si la météo sert vraiment, et à quelle échéance :

```bash
python diagnostic_meteo.py   # permutation par groupe de features
```

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

## Structure

```
ingest.py      collecte historique (à lancer une fois)
collector.py   pipeline quotidien : couleurs + météo + prédictions figées
app.py         serveur web (onglets Prévisions / Historique)
backfill.py    rejoue le backtest dans l'historique de la page
analyse_seuils.py  balayage des seuils d'alerte Rouge et Blanc sur 5 saisons
analyse_euros.py   ce que la prediction rapporte, et a quel prix en contrainte
diagnostic_meteo.py  d'ou vient reellement l'information, par echeance
src/rules.py   contraintes contractuelles Tempo
src/features.py construction des features, strictement point-in-time
src/model.py   baseline climatologique + GBM calibré
src/backtest.py walk-forward + critères de viabilité
```
