"""Configuration centrale du prédicteur Tempo."""
from pathlib import Path

ROOT = Path(__file__).parent
# Stockage interne (base SQLite, modele). Volontairement distinct du dossier `data/`
# publie par le site : sur le depot Pages, les deux seraient au meme endroit et
# l'export, qui repart d'un dossier vide, effacerait la base.
DATA_DIR = ROOT / "store"
# Heures UTC auxquelles la collecte tourne, et donc auxquelles la page change.
#
#   10:30 UTC -- apres l'annonce de RTE (vers 11h a Paris) : 11h30 l'hiver, 12h30 l'ete
#   17:00 UTC -- rattrapage du soir, si la meteo a bouge : 18h l'hiver, 19h l'ete
#
# Un cron ne connait que l'UTC et ne suit pas le changement d'heure : l'horaire local
# glisse donc d'une heure entre les deux saisons, pour les deux passages.
#
# La verite de ces valeurs est le `cron` du workflow, pas ces lignes -- mais la page
# doit les afficher et ne peut pas lire un YAML. Recopier une valeur, c'est accepter
# qu'elle derive ; un test verrouille donc les deux listes ensemble.
SCHEDULES_UTC = ["10:30", "17:00"]

REPORTS_DIR = ROOT / "reports"


def reports_path(nom):
    """Chemin dans reports/, le dossier etant cree au passage.

    `reports/` n'est pas versionne (seul son contenu l'est, et il est ignore), donc il
    n'existe pas sur un runner neuf. `np.savez` ne cree pas le dossier parent : un
    script pouvait calculer vingt minutes puis echouer sur sa derniere ligne. Passer
    par ici rend l'oubli impossible plutot que rare.
    """
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return REPORTS_DIR / nom
DB_PATH = DATA_DIR / "tempo.db"

# Codes couleur tels que renvoyes par api-couleur-tempo.fr
BLEU, BLANC, ROUGE = 1, 2, 3
COLOR_NAMES = {BLEU: "Bleu", BLANC: "Blanc", ROUGE: "Rouge"}

# Quotas contractuels sur une annee Tempo (1er sept -> 31 aout)
QUOTA_BLANC = 43
QUOTA_ROUGE = 22
# EDF ne publie pas de quota Bleu : c'est le solde des deux autres. Il vaut 300 les
# saisons ordinaires et 301 quand un 29 fevrier tombe dedans, d'ou un calcul plutot
# qu'une constante (voir app.season_summary).

# Grille tarifaire Tempo, TTC, puissance 9 kVA (la plus repandue chez les
# particuliers). Bareme EDF en vigueur au 1er aout 2026 : a reactualiser a chaque
# mouvement du tarif reglemente (typiquement le 1er fevrier et le 1er aout).
# Les heures creuses Tempo sont les memes partout en France : 22h -> 6h.
TARIFF_LABEL = "Tarif reglemente TTC, 9 kVA"
TARIFF_EFFECTIVE = "1er aout 2026"
TARIFF_OFFPEAK_HOURS = "22h-6h"
TARIFFS = {
    BLEU: {"hc": 0.1356, "hp": 0.1654},
    BLANC: {"hc": 0.1536, "hp": 0.1921},
    ROUGE: {"hc": 0.1615, "hp": 0.7295},
}

SEASON_START_MONTH = 9
FIRST_SEASON = 2020  # profondeur de l'historique disponible via l'API

# Fenetre ou les jours Rouge sont possibles (1er nov -> 31 mars)
ROUGE_WINDOW = ((11, 1), (3, 31))

# Villes ponderees par population pour la temperature "France"
CITIES = [
    ("Paris", 48.8566, 2.3522, 0.30),
    ("Lyon", 45.7640, 4.8357, 0.12),
    ("Marseille", 43.2965, 5.3698, 0.10),
    ("Toulouse", 43.6047, 1.4442, 0.09),
    ("Lille", 50.6292, 3.0573, 0.10),
    ("Bordeaux", 44.8378, -0.5792, 0.08),
    ("Nantes", 47.2184, -1.5536, 0.08),
    ("Strasbourg", 48.5734, 7.7521, 0.13),
]

# Temperature de reference pour les degres-jours de chauffage
HDD_BASE = 17.0

# Les renouvelables ne se repartissent pas comme la population : l'eolien est au
# nord/ouest/est, le solaire au sud. On reutilise les memes villes (donc les memes
# appels meteo) avec des ponderations differentes.
WIND_WEIGHTS = {
    "Lille": 0.26, "Nantes": 0.18, "Strasbourg": 0.16, "Bordeaux": 0.13,
    "Paris": 0.11, "Toulouse": 0.08, "Lyon": 0.04, "Marseille": 0.04,
}
SOLAR_WEIGHTS = {
    "Marseille": 0.28, "Toulouse": 0.22, "Bordeaux": 0.16, "Lyon": 0.14,
    "Nantes": 0.08, "Paris": 0.06, "Strasbourg": 0.04, "Lille": 0.02,
}

# Courbe de puissance d'eolienne (m/s) : demarrage, puissance nominale, coupure.
WIND_CUT_IN, WIND_RATED, WIND_CUT_OUT = 3.5, 12.0, 25.0

# ---------------------------------------------------------------------------
# Reglages du modele, tous adosses a une mesure (voir diagnostic_features.py et
# selection_modele.py). Le probleme a traiter n'est pas la precision moyenne mais
# l'INSTABILITE : 69 % de reussite un hiver, 51 % le suivant, sur les seuls jours
# ou le Rouge est possible. Un modele qu'on ne peut pas croire une annee sur trois
# n'est pas fiable, quelle que soit sa moyenne.

# Features neutralisees, chacune apres une mesure qui a dementi l'intuition.
#
# L'arbitrage Blanc/Rouge (`blanc_pressure_hiver`, `quota_arbitrage`). Il visait la
# seule couleur sous les 50 % de rappel, et il fait bien ce pour quoi il est concu :
# rappel du Blanc 44 % -> 46 %, et les deux directions d'erreur reculent ensemble
# (Blanc vu Bleu 25 -> 24 %, Blanc vu Rouge 31 -> 30 %). Mais deux points de Blanc se
# paient tres cher ailleurs : 2023-2024 passe de 0,846 a 0,942 de log-loss, donc le
# plancher se degrade de 0,911 a 0,942, et le rappel Rouge perd deux points (82 ->
# 80 %). Le meme profil que le modele a deux etages : un gain reel sur la cible visee,
# annule par une saison qui casse.
#
# La permutation designait le groupe « offre (nucleaire) » comme nuisible dans
# toutes les saisons, et j'en avais conclu qu'il fallait le retirer. Le
# reentrainement SANS ces colonnes dit autre chose : la log-loss moyenne s'ameliore
# (0,770 -> 0,749) mais la PIRE saison se degrade (1,085 -> 1,124). Or c'est le
# plancher qui decide ici, pas la moyenne.
#
# La lecon vaut d'etre notee : permuter une colonne sur un modele deja entraine ne
# dit pas ce que vaut un modele entraine sans elle. Dans le premier cas les autres
# colonnes gardent les compensations apprises grace a celle qu'on detruit ; dans le
# second, le modele se reorganise. Les deux mesures repondent a deux questions.
EXCLUDED_FEATURES = ["blanc_pressure_hiver", "quota_arbitrage"]

# Poids des jours ou le Rouge est possible pendant l'entrainement. A 1.0 : desactive.
# L'idee -- concentrer l'apprentissage sur le regime hivernal plutot que sur des
# journees d'ete ou la reponse est connue d'avance -- reste defendable, mais mesuree
# elle degrade nettement 2024-2025 (0,638 -> 0,720) sans relever le plancher.
WINTER_WEIGHT = 1.0

# Nombre de modeles moyennes.
# Premiere tentative a 5 sans aucun effet, pour une raison instructive : avec
# early_stopping desactive et moins de lignes que le seuil de sous-echantillonnage du
# binning, HistGradientBoosting est DETERMINISTE -- les cinq graines rendaient cinq
# modeles identiques. Une fois `max_features` ajoute pour diversifier les tirages, le
# gain apparaît : la pire saison passe de 1,014 a 0,902 de log-loss, et 2023-2024
# s'ameliore de 1,053 a 0,809. Cinq fois le temps de calcul, mais c'est le poste ou
# la fiabilite se gagne.
N_SEEDS = 5

# Couper la decision en deux etages : « journee tendue ? » puis « Blanc ou Rouge ? ».
# Visait la seule faiblesse qui reste : 220 jours Blanc annonces Rouge, et 71 % des
# fausses alertes tombant sur du Blanc. MESURE ET ECARTE : gagne sur trois saisons,
# perd lourdement sur la quatrieme (pire saison 0,902 -> 1,104), celle ou la fin
# d'hiver est arithmetique. Couper la decision en deux coupe aussi la contrainte de
# quota en deux : le second etage arbitre sans voir que le calendrier a deja tranche.
# Reste implemente parce qu'il echange 5 points de rappel Rouge contre 3 de precision
# d'alerte -- un compromis defendable si un jour on prefere alerter moins mais mieux.
TWO_STAGE = False

# Couper l'apprentissage en deux bandes d'echeance : J+1 a J+N d'un cote, le reste de
# l'autre. A None, un seul modele apprend sur les dix echeances melangees.
#
# L'hypothese vient d'une mesure : a J+1, la charge residuelle rend +0,164 de log-loss
# avec une pire saison a +0,024 (porteuse) ; sur toutes les echeances confondues elle
# tombe a +0,055 avec une pire saison a -0,094 (instable). La prevision de consommation
# est juste a J+1 et n'est plus que du bruit a J+10 -- le modele unique traite pourtant
# les deux pareil.
# MESURE, ECARTE. Toutes les moyennes s'ameliorent -- exactitude 72,1 -> 72,7 %, rappel
# et precision Rouge +1 point, calibration 9,6 -> 9,2 % -- mais 2023-2024 explose de
# 0,841 a 1,028 (coupure J+5) ou 1,142 (coupure J+3), et le plancher passe donc de 0,909
# a 1,028. Deux modeles voient chacun moins de lignes : la fragmentation coute plus que
# la specialisation ne rapporte.
#
# Ce n'est pas un dementi de l'idee -- le froid pilote bien les jours Rouge, et la
# charge residuelle reste porteuse a J+1 -- mais du MECANISME choisi pour l'exploiter.
# Le code reste en place : une version qui ne fragmente pas les donnees (bandes
# chevauchantes, ou correction apprise sur les seules echeances courtes) reste a tenter.
HORIZON_SPLIT = None

# Imposer le Rouge quand le quota ne tient plus dans les jours restants.
# C'est une consequence arithmetique, pas une prevision : voir rules.rouge_force.
FORCE_QUOTA_ROUGE = True

# Periodes d'evaluation. Un taux de reussite calcule sur l'annee entiere est flatte
# par les mois ou la reponse est connue d'avance : d'avril a octobre tout est Bleu.
# Le seul chiffre qui dit quelque chose est celui mesure la ou le modele a un choix
# a faire -- d'ou ces trois denominateurs, du plus flatteur au plus honnete.
PERIODS = {
    "all": "toute l'annee",
    "hiver": "novembre a mars",          # fenetre ou un Rouge est possible
    "eligibles": "jours ou le Rouge est possible",  # + lundi-vendredi, hors feries
}

# Seuil au-dela duquel un jour est annonce Rouge. C'est un arbitrage, pas un reglage
# technique. Mesure sur 5 saisons, sur les jours ou le Rouge est possible :
#
#   seuil  rappel  precision   PIRE saison : precision / rappel   fausses/echeance/hiver
#    0.10    92 %      55 %            27 %  /  65 %                    22,6
#    0.25    84 %      67 %            42 %  /  48 %                    11,9
#    0.40    76 %      75 %            57 %  /  45 %                     6,9   <- retenu
#    0.50    72 %      81 %            62 %  /  45 %                     4,8
#    0.65    62 %      90 %            75 %  /  42 %                     2,0
#
# Ce qui a decide : le rappel de la PIRE saison est plat de 0,30 a 0,55 -- 45 % partout
# -- pendant que sa precision monte de 48 % a 67 %. Au-dela de 0,30, monter le seuil ne
# coute donc presque rien la ou le modele est deja le plus faible, et rapporte beaucoup.
# En 2025-2026 le seuil de 0,25 produisait TRENTE fausses alertes pour 42 % de justesse :
# une alerte a laquelle on ne croit plus ne sert a rien.
#
# Le contrepoint honnete : en euros, 0,25 est l'optimum si une alerte inutile ne coute
# qu'un euro de gene (analyse_euros.py). Passer a 0,40 coute donc quelques euros par
# saison, deliberement, contre une vingtaine de journees de contrainte inutile en moins.
#
# Effet de bord voulu : `decide()` promeut le Blanc puis laisse le Rouge ecraser
# par-dessus. Un seuil Rouge plus haut rend donc des journees au Blanc, dont le rappel
# etait la faiblesse principale.
#
# Pour changer de point de fonctionnement : modifier cette valeur, puis
# `python analyse_seuils.py` pour revoir le tableau complet.
ROUGE_ALERT_THRESHOLD = 0.40

# Meme arbitrage pour le Blanc. Sans ce seuil il ne l'emporte que par argmax, et il ne
# pese que ~12 % des jours contre 82 % de Bleu : il ne gagne donc presque jamais.
#
#   seuil  rappel  precision   jours Bleu abimes / echeance / hiver
#    0.30    79 %      53 %            13,3
#    0.40    65 %      57 %             9,2   <- retenu
#    0.50    52 %      61 %             6,2
#    0.60    37 %      65 %             3,5
#
# Rien ne pousse a en changer : la precision progresse lentement quand le rappel chute
# vite. Le cout d'un Blanc manque reste d'ailleurs faible -- heure pleine +16 % contre
# +341 % pour un Rouge -- ce qui justifie un seuil moins agressif que celui du Rouge.
#
# ATTENTION a la lecture de ces chiffres : ils comptent les jours dont la probabilite
# de Blanc depasse le seuil, alors que `decide()` laisse ensuite le Rouge ecraser
# certains d'entre eux. Le rappel Blanc REELLEMENT obtenu est donc plus bas que celui
# du tableau, et il depend du seuil Rouge autant que de celui-ci.
BLANC_ALERT_THRESHOLD = 0.40

# Dossier du site. En local il est range dans web/ ; sur le depot publie par GitHub
# Pages, la page doit etre a la racine. On s'adapte plutot que de dupliquer les fichiers.
SITE_DIR = ROOT / "web" if (ROOT / "web").is_dir() else ROOT

MAX_HORIZON = 10
# Open-Meteo previous-runs expose les previsions passees jusqu'a J-7
MAX_ARCHIVED_LEAD = 7

HTTP_TIMEOUT = 60
