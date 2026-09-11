# Repères pour travailler sur ce dépôt

## Fuseau horaire

**L'utilisateur est en France — `Europe/Paris`.** Le conteneur, lui, tourne en UTC, et
l'écart est de +1 h en hiver, **+2 h en été**.

Conséquence pratique : **toujours annoncer les horaires en heure de Paris**, pas en UTC.
Dire « la page se met à jour à 11h30 » (ou 12h30 l'été), jamais « à 10h30 UTC » en
laissant la conversion à faire.

La collecte tourne **deux fois par jour** (`config.SCHEDULES_UTC`, verrouillé sur les
`cron` du workflow par un test) :

| Passage | UTC | Hiver (UTC+1) | Été (UTC+2) |
|---|---|---|---|
| Matin — après l'annonce RTE | 10:30 | **11h30** | **12h30** |
| Soir — rattrapage météo | 17:00 | **18h00** | **19h00** |

Un `cron` ne connaît que l'UTC et ne suit pas le changement d'heure : l'horaire local
glisse donc d'une heure entre les deux saisons. C'est assumé, pas un oubli.

**Mais ces horaires sont ceux qu'on DEMANDE, pas ceux qu'on obtient.** Mesuré sur les
passages réellement effectués (API GitHub Actions, 9 au 11 septembre 2026) :

| Cron | Départs observés | Retard |
|---|---|---|
| 10:30 UTC | 14h41, 14h32, 14h30 | **~4 h** |
| 17:00 UTC | 19h27 | **~2 h 30** |

Quatre heures reproduites à la minute près trois jours de suite : ce n'est pas un aléa
de charge, c'est la cadence réelle du service. En heure de Paris, la page se met donc à
jour vers **16h30 et 21h30** l'été, pas 12h30 et 19h.

Conséquence : **ne jamais annoncer un horaire à partir du `cron`.** Le compte à rebours
de la page vise l'horaire mesuré (`src/cadence.py` : cron + médiane des retards
observés) et se tait tant que la mesure n'est pas assez solide. Le `cron` reste la
demande ; la table `runs` est la mesure.

## Conventions du projet

- **Branche de travail** : `claude/tempo-page-status-qv8ur4`. Jamais de push direct sur
  `main` — passer par une PR.
- **Langue** : tout en français. Les commentaires Python et les messages de commit
  s'écrivent en ASCII sans accents ; le Markdown, le HTML et les textes de la page
  portent leurs accents.
- **Ton des commentaires** : ils disent *pourquoi*, pas *quoi*. Un commentaire qui
  paraphrase la ligne suivante ne sert à rien.

## Culture de mesure

Ce projet tranche ses choix de modélisation **sur des mesures, pas sur des intuitions**,
et la **pire saison décide**, jamais la moyenne : un réglage qui améliore la moyenne en
dégradant le plancher est refusé.

**`JOURNAL.md` est la mémoire des mesures.** Le lire avant de proposer une idée : il
enregistre ce qui a été mesuré puis conservé, et surtout ce qui a été mesuré puis
**écarté**, avec les chiffres. Sans lui, une piste déjà démentie revient tôt ou tard
comme une bonne idée neuve. Sa règle : aucune ligne sans chiffre.

Rien n'est exécutable dans le conteneur de développement — ni base de données, ni accès
aux sources de données (le proxy les bloque). **Toute mesure passe par
`workflow_dispatch` sur `.github/workflows/quotidien.yml`**, dont les entrées activent
chaque analyse ; le résultat se lit dans les logs du run.

## Une leçon qui s'est répétée quatre fois

Un contrôle qui repose sur la mémoire de celui qui modifie n'est pas un contrôle. À
chaque fois qu'un garde-fou a demandé de « penser à » incrémenter un numéro, passer une
option ou recopier une valeur, il a fini par laisser passer la panne qu'il devait
empêcher. Les garde-fous de ce dépôt se déclenchent donc **tout seuls** : empreinte des
attributs du modèle, signature du cache de probabilités, empreinte des assets, test qui
relit le `cron` du workflow.
