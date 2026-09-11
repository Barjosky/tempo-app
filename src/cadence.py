"""Quand la page se met-elle VRAIMENT a jour ?

Un `cron` GitHub Actions est un horaire SOUHAITE, pas un engagement. Mesure faite sur
les passages programmes du 9 au 11 septembre 2026 :

    cron 10:30 UTC  ->  parti a 14:41, 14:32, 14:30   (~4 h de retard)
    cron 17:00 UTC  ->  parti a 19:27                 (~2 h 30 de retard)

Quatre heures, reproduites a la minute pres trois jours de suite : ce n'est pas un
alea, c'est la cadence reelle du service. Un compte a rebours calcule sur l'horaire
demande tombait donc a zero, affichait « en cours » un quart d'heure, puis repartait
vers le passage suivant -- alors que rien n'avait bouge et ne bougerait pas avant des
heures. Il annoncait une promesse que personne ne tient.

POURQUOI CE MODULE NE REGARDE PLUS LES CRONS DU TOUT. Une premiere version rangeait
chaque passage observe sous le cron qu'il suivait de plus pres. Ca tenait tant que les
crons etaient largement espaces ; ca s'effondre des qu'on en ajoute un. Un cron a 06:30
retarde de quatre heures s'execute a 10:30 -- exactement l'heure d'un autre cron, qui
se voit alors crediter d'un retard nul, et le compteur annonce une heure trop tot.

Les heures observees sont donc regroupees POUR ELLES-MEMES, sans reference a ce qui a
ete demande : deux passages a moins de deux heures l'un de l'autre appartiennent au
meme rendez-vous quotidien, au-dela ce sont deux rendez-vous distincts. La page annonce
alors ce que le systeme FAIT, quoi qu'on lui ait demande -- et le jour ou les crons
changent, rien ici n'est a mettre a jour.

Tant qu'il n'y a pas assez de mesures, on ne promet rien : la page se rabat sur
« derniere mise a jour il y a X », qui est vrai par construction.
"""
from datetime import datetime, timezone

# En dessous, la mediane ne veut rien dire : deux passages ne font pas une cadence.
MINIMUM_MESURES = 3

# Au-dela de cette dispersion (minutes entre le plus tot et le plus tard), l'horaire
# n'est pas previsible et annoncer une heure precise serait mentir avec une decimale.
DISPERSION_MAX = 120

# Deux passages separes de plus de ca sont deux rendez-vous differents. En dessous,
# c'est le meme, parti plus ou moins tot. Les passages du projet sont espaces de
# plusieurs heures ; deux heures les separent sans ambiguite.
SEUIL_GROUPE = 120

# Les quatre passages programmes releves le 11 septembre 2026 dans l'API GitHub
# Actions, a l'origine de ce module. Ce sont des MESURES, pas des valeurs de confort :
# sans elles la page resterait sans cadence pendant deux jours, le temps que la table
# `runs` se remplisse. Elles s'effacent d'elles-memes des que la base a de quoi mesurer
# seule -- voir `avec_amorce`.
AMORCE = [
    "2026-09-09T14:41:54+00:00",
    "2026-09-10T14:32:06+00:00",
    "2026-09-11T14:30:53+00:00",
    "2026-09-11T19:27:58+00:00",
]


def avec_amorce(passages, minimum=None):
    """Complete les passages observes par l'amorce, tant qu'ils ne suffisent pas.

    Au-dela du seuil, l'amorce est ecartee : ce sont les mesures du systeme lui-meme qui
    doivent decider, pas un releve fige dans le code il y a six mois.
    """
    seuil = MINIMUM_MESURES * 2 if minimum is None else minimum
    if len(passages) >= seuil:
        return list(passages)
    return list(passages) + [a for a in AMORCE if a not in set(passages)]


def _minute_du_jour(brut):
    """Minute UTC dans la journee, ou None si l'horodatage est illisible."""
    try:
        t = datetime.fromisoformat(brut)
    except (ValueError, TypeError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    t = t.astimezone(timezone.utc)
    return t.hour * 60 + t.minute


def _mediane(valeurs):
    v = sorted(valeurs)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def groupes(minutes):
    """Regroupe des heures de la journee en rendez-vous distincts, sur le cercle des 24 h.

    Le cercle compte : un passage a 23h50 et un a 00h10 sont le meme rendez-vous, a vingt
    minutes pres, et une comparaison naive les mettrait aux deux bouts de la journee.
    On coupe donc aux ECARTS, la ou le silence depasse le seuil.
    """
    m = sorted(x for x in minutes if x is not None)
    n = len(m)
    if n <= 1:
        return [m] if m else []
    ecarts = [(m[(i + 1) % n] - m[i]) % 1440 for i in range(n)]
    coupures = [i for i, e in enumerate(ecarts) if e > SEUIL_GROUPE]
    if not coupures:
        # Tout se tient : un seul rendez-vous, etale.
        return [m]
    out = []
    for k, coupure in enumerate(coupures):
        debut = (coupure + 1) % n
        fin = coupures[(k + 1) % len(coupures)]
        groupe, i = [], debut
        while True:
            groupe.append(m[i])
            if i == fin:
                break
            i = (i + 1) % n
        out.append(groupe)
    return out


def observee(passages):
    """Horaires reellement constates, avec ce qui permet d'en douter.

    `fiable` a faux veut dire : pas assez de mesures, ou trop dispersees. La page ne doit
    alors afficher aucun compte a rebours -- pas un compte a rebours approximatif.
    """
    minutes = [_minute_du_jour(p) for p in passages]
    rendez_vous = []
    for groupe in groupes(minutes):
        # Rapporte au premier element : un groupe peut enjamber minuit, et une mediane
        # calculee sur les minutes brutes tomberait alors a l'oppose de la journee.
        base = groupe[0]
        rel = [(x - base) % 1440 for x in groupe]
        attendu = int(base + _mediane(rel)) % 1440
        dispersion = max(rel) - min(rel)
        rendez_vous.append({
            "attendu_utc": f"{attendu // 60:02d}:{attendu % 60:02d}",
            "dispersion_min": dispersion,
            "mesures": len(groupe),
            "fiable": len(groupe) >= MINIMUM_MESURES and dispersion <= DISPERSION_MAX,
        })
    rendez_vous.sort(key=lambda r: r["attendu_utc"])
    # Un seul rendez-vous mesurable suffit pour annoncer quelque chose : la page vise le
    # prochain passage FIABLE et ignore les autres.
    return {"passages": rendez_vous,
            "fiable": any(r["fiable"] for r in rendez_vous),
            "minimum_mesures": MINIMUM_MESURES}
