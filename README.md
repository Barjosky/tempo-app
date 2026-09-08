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

## Honnêteté du backtest

Le piège classique serait de rejouer le passé avec la météo réellement observée : le modèle
disposerait d'une prévision parfaite qu'il n'aura jamais en production, et les scores seraient
gonflés. Ici, chaque prédiction à horizon *h* n'utilise que la météo **archivée telle qu'elle
était prévue *h* jours à l'avance** (`weather.lead = h`). Les saisons antérieures à 2022, pour
lesquelles cette archive n'existe pas, servent à l'entraînement avec une dégradation calibrée
sur l'erreur de prévision réellement mesurée, et sont exclues de l'évaluation.

```bash
python -m src.backtest   # rejeu walk-forward + critères de viabilité
python run_tests.py      # règles métier vérifiées contre 6 saisons réelles
```

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
analyse_seuils.py  balayage du seuil d'alerte Rouge sur 5 saisons
src/rules.py   contraintes contractuelles Tempo
src/features.py construction des features, strictement point-in-time
src/model.py   baseline climatologique + GBM calibré
src/backtest.py walk-forward + critères de viabilité
```
