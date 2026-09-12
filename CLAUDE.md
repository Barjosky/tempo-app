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
| Tôt — pari sur le retard | 06:30 | **07h30** | **08h30** |
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

Conséquence : **la page n'annonce plus aucun horaire.** Elle affiche seulement
« dernier calcul », date et heure — vrai par construction. Un compte à rebours a existé,
deux fois : d'abord calé sur le `cron` (il mentait de quatre heures), puis sur l'horaire
mesuré (il annonçait encore 16h32 le jour où le passage est tombé à 13h). La cadence de
GitHub varie trop d'un créneau à l'autre pour qu'une heure précise veuille dire quelque
chose. **Ne pas le réintroduire sans nouvelle mesure.**

La cadence continue d'être **mesurée** sans être affichée : `src/cadence.py` regroupe les
heures réellement observées, sans jamais regarder les `cron`, et la table `runs`
enregistre l'instant de chaque passage. Le `cron` reste la demande ; `runs` est la
mesure.

Le passage de **6h30 UTC** exploite ce retard au lieu de le subir : demandé avant
l'annonce RTE, il s'exécute après. C'est un **pari**, pas une garantie — si GitHub
redevient ponctuel il tournera trop tôt et le J+1 sortira en prédiction plutôt qu'en
couleur officielle. Le passage de 10h30 reste le filet.

Corollaire pour toute mesure de cadence : **ne jamais rattacher un passage observé au
`cron` qu'il suit de plus près.** Avec des créneaux rapprochés, 6h30 retardé de quatre
heures tombe sur 10h30 et se ferait créditer d'un retard nul.

## En cours au 12 septembre 2026

Ce qui n'est pas encore tranché, pour ne pas le redécouvrir :

- **Le pari du créneau de 6h30 attend ses mesures.** Première observation : demandé à
  8h30 (Paris), exécuté à **13h00** — 4 h 30 de retard, contre les 2 h 30 à 4 h attendues.
  Mieux que 16h30, mais pas 11h. Une observation ne conclut rien ; il en faut trois.
  Se lisent dans la table `runs` ou dans `data/forecast.json` (`cadence`).
- **Le modèle en cache sur `main` est périmé** depuis que trois colonnes sont passées
  dans `EXCLUDED_FEATURES`. Le garde-fou de `load()` le détecte seul et le prochain
  passage réentraînera — un run plus long que d'ordinaire, c'est normal.
- **Proposé, sans réponse de l'utilisateur** : faire remonter automatiquement l'écart
  entre la réussite en **temps réel** et celle du **backtest**. L'échantillon temps réel
  est aujourd'hui trop maigre (une dizaine de lignes) ; dès novembre il dira, en quelques
  semaines, si le modèle tient ses promesses sur un hiver qu'il n'a pas vu. C'est la
  seule chose que j'ajouterais avant l'hiver — un moyen de savoir tôt, pas une feature.
- **Question ouverte sur les indisponibilités RTE** : 70 % des paliers sont à la
  puissance installée entière, sans qu'on sache séparer « arrêt total publié » de
  « repli sur la puissance installée ». Borne haute du repli 70 %, borne basse 0 %.
  Fermer la question demanderait de marquer l'origine de chaque ligne à l'écriture.

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
