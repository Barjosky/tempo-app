"""Configuration centrale du prédicteur Tempo."""
from pathlib import Path

ROOT = Path(__file__).parent
# Stockage interne (base SQLite, modele). Volontairement distinct du dossier `data/`
# publie par le site : sur le depot Pages, les deux seraient au meme endroit et
# l'export, qui repart d'un dossier vide, effacerait la base.
DATA_DIR = ROOT / "store"
REPORTS_DIR = ROOT / "reports"
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

# Features neutralisees. La permutation les a mesurees nuisibles : le groupe
# « offre (nucleaire) » est le pire des dix, negatif dans TOUTES les saisons
# (-0,032 de log-loss en moyenne, -0,091 sur la pire). L'intuition etait bonne --
# un jour Rouge naît d'une marge tendue -- mais la production nucleaire appelee est
# un proxy trop grossier de la puissance disponible.
# Elles sont neutralisees plutot que supprimees : la ligne de features garde sa
# longueur, donc aucun risque de decalage entre les colonnes et leurs noms, et
# revenir en arriere ne demande que de vider cette liste.
EXCLUDED_FEATURES = [
    "nuclear_recent_mw",
    "nuclear_anomaly_mw",
    "margin_proxy_mw",
]

# Poids des jours ou le Rouge est possible pendant l'entrainement. Sans lui, la
# fonction de cout est dominee par les journees d'ete, ou la reponse est Bleu
# d'avance : le modele optimise surtout ce qui ne se joue pas. Le poids concentre
# l'apprentissage ET la calibration sur le regime hivernal, ce qui repond aussi a
# la sur-confiance mesuree entre 40 et 70 % de probabilite annoncee.
WINTER_WEIGHT = 5.0

# Nombre de modeles moyennes. Chacun ne differe que par sa graine ; leur moyenne
# reduit la part du hasard d'entrainement, qui est justement ce qui fait qu'une
# saison passe et l'autre casse. C'est le levier le plus sur contre l'instabilite.
N_SEEDS = 5

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
