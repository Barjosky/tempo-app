"""Ce que la prediction rapporte reellement, en euros.

    python analyse_euros.py                 # relit les probabilites en cache
    python analyse_euros.py --refit         # les recalcule (~10 min)
    python analyse_euros.py --kwh 12        # 12 kWh decalables par jour
    python analyse_euros.py --gene 2.50     # 2,50 EUR de gene par alerte inutile

Le README disait que le cout d'une fausse alerte « n'appartient qu'a l'utilisateur »
et n'etait donc pas mesurable. La moitie du probleme l'est pourtant : depuis que la
grille tarifaire est dans `config`, le gain d'un Rouge correctement anticipe se chiffre
exactement -- decaler un kWh d'heure pleine a heure creuse un jour Rouge vaut
0,7295 - 0,1615 = 0,568 EUR. Ce qui reste non chiffrable, c'est la GENE de s'etre
organise pour rien. On ne l'invente donc pas : on balaye sa valeur, et on montre
comment le seuil optimal se deplace avec elle. A chacun de lire la ligne qui lui
ressemble.

Strategie simulee : quand le modele annonce Rouge a l'echeance h, on decale `kwh`
kilowattheures des heures pleines vers les heures creuses de la journee. Le gain
depend de la couleur REELLEMENT tombee, pas de celle annoncee.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import config
from analyse_seuils import SEASONS, compute_probs

# Consommation typiquement decalable en une journee : lave-linge, seche-linge,
# lave-vaisselle, ballon d'eau chaude, recharge d'un vehicule. Reglable en ligne
# de commande, parce que ce nombre n'appartient qu'a l'utilisateur lui aussi.
KWH_DEFAUT = 8.0
GENES = [0.0, 0.50, 1.00, 2.00, 5.00]


def _arg(nom, defaut):
    if nom in sys.argv:
        return float(sys.argv[sys.argv.index(nom) + 1])
    return defaut


def gain_par_kwh(couleur):
    """Ecart heure pleine / heure creuse du jour : ce que vaut un kWh decale."""
    t = config.TARIFFS[couleur]
    return t["hp"] - t["hc"]


def simule(data, seuil, horizon, kwh, gene):
    """Euros gagnes sur l'ensemble des saisons, a un seuil et une echeance donnes.

    Un jour non signale ne rapporte rien : on n'a rien decale, on a paye plein tarif.
    Un jour signale rapporte l'ecart HP/HC de sa couleur REELLE, moins la gene si
    l'alerte etait inutile -- une alerte sur un jour Bleu fait quand meme economiser
    trois centimes du kWh, mais elle derange pour presque rien.
    """
    total, alertes, justes, rouges_rates = 0.0, 0, 0, 0
    for season in SEASONS:
        y = data[f"{season}_y"]
        p = data[f"{season}_probs"][:, 2]
        h = data[f"{season}_horizon"]
        sel = h == horizon
        for couleur, proba in zip(y[sel], p[sel]):
            if proba < seuil:
                if couleur == config.ROUGE:
                    rouges_rates += 1
                continue
            alertes += 1
            total += kwh * gain_par_kwh(int(couleur))
            if couleur == config.ROUGE:
                justes += 1
            else:
                total -= gene
    n = len(SEASONS)
    return {"euros_par_saison": total / n, "alertes": alertes / n,
            "justes": justes / n, "rouges_rates": rouges_rates / n}


def main():
    kwh = _arg("--kwh", KWH_DEFAUT)
    gene_demandee = _arg("--gene", None) if "--gene" in sys.argv else None
    path = config.REPORTS_DIR / "probs_par_saison.npz"
    if "--refit" in sys.argv or not path.exists():
        config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        print("Rejeu des saisons :")
        compute_probs(path)
    data = np.load(path)

    ecart = gain_par_kwh(config.ROUGE)
    print(f"Grille du {config.TARIFF_EFFECTIVE} — decaler 1 kWh un jour Rouge vaut "
          f"{ecart:.4f} EUR".replace(".", ","))
    print(f"Hypothese : {kwh:.0f} kWh decalables par jour.\n")

    seuils = np.arange(0.05, 1.0, 0.05)
    genes = [gene_demandee] if gene_demandee is not None else GENES

    print("Seuil optimal selon ce que coute une alerte inutile, a l'echeance J+1 :")
    print(f"{'gene/alerte':>12} {'seuil':>7} {'EUR/saison':>12} "
          f"{'alertes':>9} {'dont justes':>12} {'rouges rates':>13}")
    for gene in genes:
        scores = [(t, simule(data, t, 1, kwh, gene)) for t in seuils]
        best_t, best = max(scores, key=lambda s: s[1]["euros_par_saison"])
        print(f"{gene:>11.2f}€ {best_t:>7.2f} {best['euros_par_saison']:>11.0f}€ "
              f"{best['alertes']:>9.0f} {best['justes']:>12.0f} {best['rouges_rates']:>13.0f}")

    seuil = config.ROUGE_ALERT_THRESHOLD
    print(f"\nSeuil actuellement configure ({seuil:.2f}), par echeance, "
          f"a {GENES[2]:.2f}€ de gene :")
    print(f"{'echeance':>9} {'EUR/saison':>12} {'alertes':>9} {'dont justes':>12} "
          f"{'rouges rates':>13}")
    for h in range(1, config.MAX_HORIZON + 1):
        r = simule(data, seuil, h, kwh, GENES[2])
        print(f"{'J+' + str(h):>9} {r['euros_par_saison']:>11.0f}€ {r['alertes']:>9.0f} "
              f"{r['justes']:>12.0f} {r['rouges_rates']:>13.0f}")

    # Comparer les euros bruts induirait en erreur : decaler sa conso rapporte un
    # peu TOUS les jours, donc « tout decaler tous les jours » gagne forcement le
    # plus d'euros -- au prix d'une annee entiere de contrainte. Ce que le modele
    # apporte n'est pas le total, c'est le rendement de chaque jour de gene consenti.
    tout = simule(data, 0.0, 1, kwh, 0.0)
    config_j1 = simule(data, seuil, 1, kwh, 0.0)
    oracle = config.QUOTA_ROUGE * kwh * ecart
    print("\nCe que rapporte un jour de contrainte, selon la strategie (echeance J+1) :")
    print(f"{'strategie':>26} {'EUR/saison':>12} {'jours genes':>13} {'EUR/jour gene':>15}")
    for label, r in (("tout decaler, tous les jours", tout),
                     (f"suivre le modele (seuil {seuil:.2f})", config_j1)):
        rendement = r["euros_par_saison"] / r["alertes"] if r["alertes"] else 0
        print(f"{label:>26} {r['euros_par_saison']:>11.0f}€ {r['alertes']:>13.0f} "
              f"{rendement:>14.2f}€")
    print(f"{'oracle (les 22 vrais Rouge)':>26} {oracle:>11.0f}€ "
          f"{config.QUOTA_ROUGE:>13} {ecart * kwh:>14.2f}€")


if __name__ == "__main__":
    main()
