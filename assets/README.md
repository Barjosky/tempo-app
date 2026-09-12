# Fond de page

La page cherche **`assets/fond.jpg`** et l'installe en fond fixe si elle le trouve.
Le fichier n'est pas suivi ici : déposez-le, il sera pris en compte au chargement
suivant.

## La règle qui compte : pas de typographie dans le fond

L'image déposée le 12 septembre 2026 portait son propre titre manuscrit, un
sous-titre « PRÉVISIONS J+10 » et une légende des trois couleurs. Au rendu, ce
titre passait **derrière celui de la page** et le sous-titre traversait le bandeau
d'alerte : deux titres l'un sur l'autre, et une légende qui redisait moins bien ce
que la colonne de gauche dit déjà.

Aucun voile ne corrige ça — du blanc vif sur fond sombre survit à
l'assombrissement. **C'est le recadrage qui s'en charge**, à la source : on ne garde
que le paysage (ici, sous 430 px sur 941). Effet de bord bienvenu, le voile n'a plus
qu'un seul travail — la lisibilité — et peut rester léger, donc le paysage se voit.

Pour changer d'image : couper au-dessus de tout texte avant de l'enregistrer.

## Déposer l'image

Sur GitHub : **Add file → Upload files**, dans ce dossier, en nommant le fichier
exactement `fond.jpg` (le nom est lu par `app.js`, constante `FOND`).

## Ce à quoi il faut faire attention

- **Poids.** La page entière pèse quelques dizaines de kilo-octets. L'image déposée
  faisait **2,1 Mo**, soit cent fois le reste du site, téléchargés à chaque première
  visite ; recadrée et réencodée en JPEG qualité 82, elle en fait **158**. Viser
  **300 Ko au plus**.

  La qualité se juge **sous le voile**, pas à nu : le fond s'affiche à environ 12 %
  de sa luminosité, donc un défaut de compression y est divisé d'autant. Mesuré dans
  le ciel — dégradé uni, là où le JPEG fait des bandes — l'écart affiché est de
  0,2/255 aussi bien en qualité 88 qu'en 76, très en dessous de ce qu'un écran
  distingue. Payer des kilo-octets au-delà, c'est payer pour un détail que personne
  ne verra.
- **Lisibilité.** Le voile est défini dans `styles.css` (`body.a-fond::before`), en
  trois temps : dense en haut pour effacer la typographie de l'image derrière le
  titre de la page, léger au tiers, dense en bas sous les cartes. C'est le seul
  endroit à retoucher si le rendu est trop sombre ou trop clair.
- **Absence du fichier.** Le voile n'est posé qu'une fois l'image *réellement
  chargée* : sans elle, la page garde son dégradé d'origine, intact. Rien ne casse.
- **Remplacement.** Les navigateurs gardent l'image en cache longtemps. Pour en
  changer, **utiliser un nouveau nom** (`fond2.jpg`) et l'indiquer dans `app.js`,
  plutôt que d'écraser le fichier : sinon l'ancienne image peut persister des jours.
