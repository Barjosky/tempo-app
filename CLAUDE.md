# Repères pour travailler sur ce dépôt

## Fuseau horaire

**L'utilisateur est en France — `Europe/Paris`.** Le conteneur, lui, tourne en UTC, et
l'écart est de +1 h en hiver, **+2 h en été**.

Conséquence pratique : **toujours annoncer les horaires en heure de Paris**, pas en UTC.
Dire « la page se met à jour à 11h30 » (ou 12h30 l'été), jamais « à 10h30 UTC » en
laissant la conversion à faire.

La collecte quotidienne tourne à `10:30` UTC (`config.SCHEDULE_UTC`, verrouillé sur le
`cron` du workflow par un test) :

| Saison | UTC | Heure de Paris |
|---|---|---|
| Hiver (CET, UTC+1) | 10:30 | **11:30** |
| Été (CEST, UTC+2) | 10:30 | **12:30** |

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
