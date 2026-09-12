# Fond de page

La page cherche **`assets/fond.jpg`** et l'installe en fond fixe si elle le trouve.
Le fichier n'est pas suivi ici : déposez-le, il sera pris en compte au chargement
suivant.

## Déposer l'image

Sur GitHub : **Add file → Upload files**, dans ce dossier, en nommant le fichier
exactement `fond.jpg` (le nom est lu par `app.js`, constante `FOND`).

## Ce à quoi il faut faire attention

- **Poids.** La page entière pèse quelques dizaines de kilo-octets. Une image
  générée fait couramment 2 à 3 Mo, soit cent fois le reste du site, et elle se
  télécharge à chaque première visite. Viser **300 Ko au plus**, en 1920 px de
  large : au-delà, rien ne se voit de plus, le voile couvrant l'essentiel du détail.
- **Lisibilité.** Le voile est défini dans `styles.css` (`body.a-fond::before`), en
  trois temps : dense en haut pour effacer la typographie de l'image derrière le
  titre de la page, léger au tiers, dense en bas sous les cartes. C'est le seul
  endroit à retoucher si le rendu est trop sombre ou trop clair.
- **Absence du fichier.** Le voile n'est posé qu'une fois l'image *réellement
  chargée* : sans elle, la page garde son dégradé d'origine, intact. Rien ne casse.
- **Remplacement.** Les navigateurs gardent l'image en cache longtemps. Pour en
  changer, **utiliser un nouveau nom** (`fond2.jpg`) et l'indiquer dans `app.js`,
  plutôt que d'écraser le fichier : sinon l'ancienne image peut persister des jours.
