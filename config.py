"""Configuration centrale du prédicteur Tempo."""
from pathlib import Path

ROOT = Path(__file__).parent
# Stockage interne (base SQLite, modele). Volontairement distinct du dossier `data/`
# publie par le site : sur le depot Pages, les deux seraient au meme endroit et
# l'export, qui repart d'un dossier vide, effacerait la base.
DATA_DIR = ROOT / "store"
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

# Seuil au-dela duquel un jour est annonce Rouge. C'est un arbitrage, pas un
# reglage technique : mesure sur 5 saisons (voir analyse_seuils.py)
#   0.10 -> 90% des Rouge detectes, ~22 fausses alertes / echeance / hiver
#   0.25 -> 79% detectes, ~13 fausses alertes   <- choix retenu
#   0.30 -> 76% detectes, ~11 fausses alertes
#   0.50 -> 61% detectes, ~5 fausses alertes
# Pour changer de point de fonctionnement : modifier cette valeur, puis
# `python analyse_seuils.py` pour revoir le tableau complet.
ROUGE_ALERT_THRESHOLD = 0.25

# Meme arbitrage pour le Blanc. Sans ce seuil le Blanc ne l'emporte que par argmax,
# et il ne pese que ~12 % des jours contre 82 % de Bleu : il ne gagne donc presque
# jamais, d'ou un rappel Blanc mesure a 39 % sur le backtest. Le cout d'un Blanc
# manque reste faible (heure pleine +16 % contre +341 % pour un Rouge), d'ou un seuil
# nettement moins agressif que celui du Rouge. `python analyse_seuils.py` balaye les deux.
BLANC_ALERT_THRESHOLD = 0.40

# Dossier du site. En local il est range dans web/ ; sur le depot publie par GitHub
# Pages, la page doit etre a la racine. On s'adapte plutot que de dupliquer les fichiers.
SITE_DIR = ROOT / "web" if (ROOT / "web").is_dir() else ROOT

MAX_HORIZON = 10
# Open-Meteo previous-runs expose les previsions passees jusqu'a J-7
MAX_ARCHIVED_LEAD = 7

HTTP_TIMEOUT = 60
