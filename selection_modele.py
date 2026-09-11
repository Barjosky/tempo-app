"""Quelle configuration de modele est la plus FIABLE ?

    python selection_modele.py            # compare les candidats
    python selection_modele.py --saisons 2024-2025,2025-2026   # plus rapide

Le probleme n'est pas la precision moyenne, c'est l'instabilite : sur les jours ou
le Rouge est possible, le modele fait 69 % de reussite un hiver et 51 % le suivant.
Une moyenne flatteuse batie sur une saison sauvee et une saison ratee ne vaut rien
pour quelqu'un qui doit decider demain matin.

C'est donc la PIRE SAISON qui departage ici, pas la moyenne. Un candidat ne gagne
que s'il releve le plancher.

Ce qui a ete compare ici visait la derniere faiblesse connue : l'hesitation entre
Blanc et Rouge. Le modele repere tres bien les journees tendues et n'arrive pas a
trancher dedans -- 220 jours Blanc annonces Rouge, et 71 % des fausses alertes tombant
sur du Blanc plutot que sur du Bleu.

L'hypothese etait que le probleme est mal pose : le modele s'entraine sur une
population a 82 % de Bleu et sert sur des jours eligibles ou le Bleu n'est plus qu'a la
moitie. Le candidat coupait la decision en deux -- « journee tendue ? », puis « Blanc
ou Rouge ? » entrainee sur les seuls jours tendus, ou l'arbitrage est equilibre
(32 Blanc contre 22 Rouge).

MESURE, ECARTE. Il gagne sur trois saisons et perd lourdement sur la quatrieme :
pire saison 0,902 -> 1,104, sur l'hiver justement soumis a la contrainte de quota.
Couper la decision en deux coupe aussi la contrainte en deux -- le second etage arbitre
sans voir que le calendrier a deja tranche. Il rapporte +3 points de precision d'alerte
pour -5 de rappel ; le plancher decidant ici, TWO_STAGE reste a False. Le candidat est
laisse dans la liste pour que la mesure se refasse si la contrainte change.

Avertissement d'honnetete : quatre saisons evaluables, c'est peu. Comparer beaucoup
de variantes finirait par choisir celle qui colle le mieux a ces quatre-la. D'ou une
liste courte de candidats justifies d'avance, plutot qu'un balayage.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from sklearn.metrics import log_loss

sys.path.insert(0, str(Path(__file__).parent))
import config
from src import backtest, db, features, model
from src.sources import calendrier

MARGE = ["rouge_slack", "rouge_forced"]
NUCLEAIRE = ["nuclear_recent_mw", "nuclear_anomaly_mw", "margin_proxy_mw"]

# Chaque candidat isole UNE idee, pour que le tableau se lise sans ambiguite.
# La cible est la fin de saison sous contrainte de quota : c'est elle qui plombe
# 2025-2026, ou treize des vingt-deux Rouge sont tombes en mars, jusqu'au 31.
# Chaque candidat FIXE tous les reglages qui varient, sans jamais laisser un defaut
# de config combler un trou : le jour ou N_SEEDS est passe de 1 a 5, les candidats
# qui ne le precisaient pas ont silencieusement herite de l'ensemble, et le tableau
# comparait six variantes deja ensemblees en les etiquetant autrement.
# Le comparatif ne garde que la configuration retenue et la piste a l'essai : chaque
# candidat coute quatre entrainements, et une liste longue finit par elire la variante
# qui colle le mieux a quatre saisons plutot que celle qui generalise.
#
# Deja mesure et conserve : contrainte de quota + marge en jours + ensemble
#   (plancher 1,093 -> 0,901 ; rappel Rouge 73 % -> 82 %).
# Deja mesure et ecarte : retrait des features d'offre (ameliore la moyenne, degrade
#   le plancher), ponderation vers l'hiver (degrade 2024-2025), ensemble sans
#   diversification (strictement sans effet).
RETENU = dict(excluded=config.EXCLUDED_FEATURES, force_quota=True,
              n_seeds=5, two_stage=False, horizon_split=None)

ARBITRAGE = ["blanc_pressure_hiver", "quota_arbitrage"]
INDISPO = ["offline_nuclear_mw", "offline_nuclear_anomaly_mw", "offline_unplanned_mw",
           "offline_total_mw", "margin_rte_mw"]

# La cible du jour est le Blanc, seule couleur sous les 50 % de rappel : 45 %, contre
# 81 % pour le Bleu et 82 % pour le Rouge. Son erreur se partage en deux moities
# presque egales -- 30 % des Blanc annonces Rouge, 25 % annonces Bleu -- donc ce n'est
# pas seulement une hesitation avec le Rouge.
#
# L'hypothese porte sur l'arbitrage entre les deux quotas. Il etait jusqu'ici illisible
# pour le modele : `rouge_pressure` compte les jours eligibles jusqu'au 31 mars,
# `blanc_pressure` comptait les jours de calendrier jusqu'au 31 aout. Au 13 mars 2026,
# 13 jours d'un cote, 147 de l'autre -- un facteur dix entre deux grandeurs censees se
# comparer. Le Blanc se mesure desormais sur la meme fenetre, et leur rapport dit quel
# quota est le plus rare aujourd'hui.
#
# Le denominateur corrige s'applique aux DEUX candidats : c'est une correction, pas une
# option. Ce qui est mesure ici est l'apport des deux features d'arbitrage.
# Deja mesure et ECARTE : l'arbitrage Blanc/Rouge. Il fait ce pour quoi il est concu
# -- rappel du Blanc 44 -> 46 %, les deux directions d'erreur reculant ensemble --
# mais 2023-2024 passe de 0,846 a 0,942, donc le plancher se degrade de 0,911 a 0,942.
#
# CE QUI EST MESURE ICI : couper l'apprentissage par bande d'echeance.
#
# Une meme colonne n'a pas la meme valeur selon l'echeance. Mesure a J+1 seulement, la
# charge residuelle rend +0,164 de log-loss avec une pire saison a +0,024 -- solidement
# porteuse. Sur les dix echeances confondues elle tombe a +0,055 avec une pire saison a
# -0,094, donc instable. La raison est evidente une fois vue : a J+1 la prevision de
# consommation est juste, a J+10 c'est du bruit.
#
# Le modele unique traite pourtant les dix echeances pareil. `horizon` est bien une
# colonne, mais un arbre doit alors redecouvrir cette interaction dans chaque branche,
# au lieu de la recevoir d'emblee.
#
# Le contre-argument, qu'il faut mesurer et non supposer : deux modeles voient chacun
# moins de lignes, et deux modeles faibles peuvent valoir moins qu'un seul entraine sur
# tout. D'ou deux coupures testees, a J+3 et a J+5.
# MESURE, ECARTE. Toutes les moyennes s'ameliorent et le plancher se degrade quand meme :
# 2024-2025 gagne (0,628 -> 0,585) pendant que 2023-2024 explose (0,841 -> 1,142). Deux
# modeles voient chacun moins de lignes, et la fragmentation coute plus que la
# specialisation ne rapporte. L'idee de depart tient -- le froid pilote les jours Rouge,
# mesure a J+1 -- c'est ce mecanisme-la qui ne la sert pas.
#
# CE QUI EST MESURE ICI : le calendrier d'indisponibilites publie par RTE.
#
# Tout le cote offre du modele etait jusqu'ici RETROSPECTIF. `nuclear_recent_mw` lit ce
# que le parc a produit les quatorze derniers jours : si douze reacteurs s'arretent
# mardi, la colonne l'apprend mardi, jamais avant. Or la question posee est « que se
# passera-t-il dans dix jours ». Un proxy du passe ne peut pas y repondre, et ces
# colonnes-la ont d'ailleurs mesure NEGATIF au diagnostic de permutation.
#
# Le calendrier RTE, lui, est de l'information sur le futur : les arrets programmes sont
# publies des mois a l'avance, les fortuits des leur declaration. La marge devient alors
# previsionnelle des DEUX cotes -- une demande prevue face a une offre annoncee.
#
# Le contre-argument, qu'il faut mesurer : cinq colonnes de plus sur quatre saisons
# evaluables, c'est de quoi surajuster. Et l'hiver 2022-2023, ou le parc s'est effondre,
# est aussi celui ou les Rouge ont ete les plus nombreux : le modele peut apprendre
# cette coincidence-la plutot que le mecanisme.
#
# MESURE, RETENU. Le plancher se releve : 0,912 -> 0,883, et le Blanc progresse dans
# ses DEUX directions d'erreur a la fois (54 -> 57 % de rappel, 27 -> 25 % vu Bleu,
# 20 -> 18 % vu Rouge), ce qui signale de l'information ajoutee plutot qu'un arbitrage
# deplace. Les colonnes restent donc actives (elles ne sont pas dans EXCLUDED_FEATURES).
#
# Deux reserves, ecrites ici pour qu'elles ne se perdent pas. Le gain est concentre :
# deux saisons gagnent, deux perdent, et celles qui perdent sont les deux plus faciles.
# Surtout, 2022-2023 se DEGRADE (0,342 -> 0,359) -- l'hiver de la corrosion, celui ou
# ces colonnes auraient du briller. Hypothese non mesuree : cet hiver-la
# l'indisponibilite etait si generale qu'elle ne discriminait plus les jours entre eux.
CANDIDATS = [
    ("sans le calendrier RTE", dict(RETENU, excluded=config.EXCLUDED_FEATURES + INDISPO)),
    ("avec le calendrier RTE", dict(RETENU, excluded=config.EXCLUDED_FEATURES)),
]


def evalue(gbm, X, y, meta):
    """Mesures sur les seuls jours ou le Rouge est possible."""
    probs = gbm.predict_proba(X, meta)
    preds = model.decide(probs, gbm.rouge_threshold, gbm.blanc_threshold)
    rouge = y == config.ROUGE
    signale = preds == config.ROUGE
    # Le Blanc est la seule couleur sous les 50 % de rappel : c'est lui qu'on cherche a
    # relever ici, il doit donc figurer au tableau plutot que d'etre noye dans
    # l'exactitude globale.
    blanc = y == config.BLANC
    dit_blanc = preds == config.BLANC
    return {
        "logloss": float(log_loss(y, probs, labels=model.CLASSES)),
        "acc": float((preds == y).mean()),
        "rappel_r": float((signale & rouge).sum() / rouge.sum()) if rouge.sum() else 0.0,
        "prec_r": float((signale & rouge).sum() / signale.sum()) if signale.sum() else 0.0,
        "rappel_b": float((dit_blanc & blanc).sum() / blanc.sum()) if blanc.sum() else 0.0,
        "prec_b": float((dit_blanc & blanc).sum() / dit_blanc.sum()) if dit_blanc.sum() else 0.0,
        # Ou part le Blanc quand il est rate : vers le Bleu ou vers le Rouge ? Les deux
        # erreurs n'ont pas le meme cout, et une piste peut corriger l'une en aggravant
        # l'autre sans que le rappel le dise.
        "blanc_vu_bleu": float((blanc & (preds == config.BLEU)).sum() / blanc.sum())
                         if blanc.sum() else 0.0,
        "blanc_vu_rouge": float((blanc & signale).sum() / blanc.sum()) if blanc.sum() else 0.0,
        # Ecart moyen entre la probabilite annoncee et la frequence observee, par
        # tranche de 10 points : c'est la mesure directe de « peut-on y croire ».
        "calibration": ecart_calibration(probs[:, 2], rouge),
    }


def ecart_calibration(p, reel, bins=10):
    ecarts, poids = [], []
    for i in range(bins):
        sel = (p >= i / bins) & (p < (i + 1) / bins if i < bins - 1 else p <= 1.0)
        if sel.sum() < 20:
            continue
        ecarts.append(abs(p[sel].mean() - reel[sel].mean()))
        poids.append(sel.sum())
    return float(np.average(ecarts, weights=poids)) if ecarts else float("nan")


def jeux(store, saisons):
    """(saison, Xtrain, ytrain, mtrain, Xtest, ytest, mtest) deja restreints."""
    for saison in saisons:
        store.fit_demand_model(calendrier.season_start(saison))
        fin = calendrier.season_start(saison) - timedelta(days=1)
        Xtr, ytr, mtr = features.build_dataset(
            store, date(config.FIRST_SEASON, 9, 1), fin)
        Xte, yte, mte = features.build_dataset(
            store, calendrier.season_start(saison), calendrier.season_end(saison))
        if not len(Xte):
            continue
        ev = backtest.evaluables(mte)
        yield saison, Xtr, ytr, mtr, Xte[ev], yte[ev], [m for m, k in zip(mte, ev) if k]


def main():
    saisons = backtest.HONEST_SEASONS
    if "--saisons" in sys.argv:
        saisons = sys.argv[sys.argv.index("--saisons") + 1].split(",")

    store = features.FeatureStore(db.connect())
    print(f"Saisons evaluees : {', '.join(saisons)}")
    print("Chaque saison est predite par un modele n'ayant appris que sur les "
          "precedentes.\n")

    # Les jeux sont construits une fois et reutilises par tous les candidats : la
    # comparaison porte alors sur la seule configuration, pas sur un alea de tirage.
    prepares = list(jeux(store, saisons))
    resultats = {}

    for nom, reglages in CANDIDATS:
        par_saison = []
        for saison, Xtr, ytr, mtr, Xte, yte, mte in prepares:
            gbm = model.TempoModel(**reglages).fit(Xtr, ytr, mtr)
            par_saison.append((saison, evalue(gbm, Xte, yte, mte)))
            print(f"  {nom:>20} · {saison} : logloss "
                  f"{par_saison[-1][1]['logloss']:.3f}", flush=True)
        resultats[nom] = par_saison

    # La precision des alertes Rouge etait calculee sans etre affichee. C'est
    # pourtant la moitie de la question qu'on se pose devant la page : sur dix jours
    # annonces Rouge, combien le sont vraiment ?
    print(f"\n{'candidat':>20} {'logloss moy':>12} {'PIRE logloss':>13} "
          f"{'exactitude':>11} {'rappel R':>9} {'prec. R':>8} {'ecart calib':>12}")
    for nom, _ in CANDIDATS:
        rs = [r for _, r in resultats[nom]]
        print(f"{nom:>20} {np.mean([r['logloss'] for r in rs]):>12.3f} "
              f"{max(r['logloss'] for r in rs):>13.3f} "
              f"{np.mean([r['acc'] for r in rs]):>10.1%} "
              f"{np.mean([r['rappel_r'] for r in rs]):>8.0%} "
              f"{np.mean([r['prec_r'] for r in rs]):>7.0%} "
              f"{np.mean([r['calibration'] for r in rs]):>11.1%}")

    print(f"\nLe Blanc, couleur la plus mal vue (45 % de rappel au depart) :\n")
    print(f"{'candidat':>20} {'rappel B':>9} {'prec. B':>8} "
          f"{'B vu Bleu':>10} {'B vu Rouge':>11}")
    for nom, _ in CANDIDATS:
        rs = [r for _, r in resultats[nom]]
        print(f"{nom:>20} {np.mean([r['rappel_b'] for r in rs]):>8.0%} "
              f"{np.mean([r['prec_b'] for r in rs]):>7.0%} "
              f"{np.mean([r['blanc_vu_bleu'] for r in rs]):>9.0%} "
              f"{np.mean([r['blanc_vu_rouge'] for r in rs]):>10.0%}")

    ref = max(r["logloss"] for _, r in resultats[CANDIDATS[0][0]])
    print(f"\nDetail par saison (log-loss ; plus bas vaut mieux) :")
    entetes = [s for s, _ in resultats[CANDIDATS[0][0]]]
    print(f"{'candidat':>20} " + " ".join(f"{s:>11}" for s in entetes))
    for nom, _ in CANDIDATS:
        print(f"{nom:>20} " + " ".join(
            f"{r['logloss']:>11.3f}" for _, r in resultats[nom]))

    gagnant = min(CANDIDATS, key=lambda c: max(r["logloss"] for _, r in resultats[c[0]]))
    pire = max(r["logloss"] for _, r in resultats[gagnant[0]])
    print(f"\nMeilleur plancher : « {gagnant[0]} » ({pire:.3f} sur sa pire saison, "
          f"contre {ref:.3f} pour la reference).")
    if pire >= ref:
        print("Aucun candidat ne releve le plancher : garder la reference.")


if __name__ == "__main__":
    main()
