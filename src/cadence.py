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

Ce module renverse la charge de la preuve : le cron dit ce qu'on DEMANDE, la mediane
des retards observes dit ce qu'on OBTIENT, et c'est la somme qui s'affiche. Tant qu'il
n'y a pas assez de mesures, on ne promet rien -- la page se rabat sur « derniere mise
a jour il y a X », qui est vrai par construction.
"""
from datetime import datetime, timezone

# En dessous, la mediane ne veut rien dire : deux passages ne font pas une cadence.
MINIMUM_MESURES = 3

# Au-dela de cette dispersion (minutes entre le plus tot et le plus tard), l'horaire
# n'est pas previsible et annoncer une heure precise serait mentir avec une decimale.
DISPERSION_MAX = 120

# Un retard superieur a ca ne s'attribue plus a coup sur au bon cron : les deux
# passages de la journee sont separes de 6 h 30, et au-dela on ne sait plus lequel on
# regarde. L'observation est ecartee plutot que rangee au hasard.
RETARD_MAX = 360


# Les quatre passages programmes releves le 11 septembre 2026 dans l'API GitHub
# Actions, a l'origine de tout ce module. Ce sont des MESURES, pas des valeurs de
# confort : sans elles la page resterait sans cadence pendant deux jours, le temps que
# la table `runs` se remplisse. Elles s'effacent d'elles-memes des que la base a de quoi
# mesurer seule -- voir `avec_amorce`, qui ne les ajoute que tant qu'il manque des
# observations.
AMORCE = [
    "2026-09-09T14:41:54+00:00",
    "2026-09-10T14:32:06+00:00",
    "2026-09-11T14:30:53+00:00",
    "2026-09-11T19:27:58+00:00",
]


def avec_amorce(passages, horaires_utc):
    """Complete les passages observes par l'amorce, tant qu'ils ne suffisent pas.

    Le seuil est le nombre d'observations qu'il faudrait pour mesurer chaque passage
    seul. Au-dela, l'amorce est ecartee : ce sont les mesures du systeme lui-meme qui
    doivent decider, pas un releve fige dans le code.
    """
    if len(passages) >= MINIMUM_MESURES * max(1, len(horaires_utc)):
        return list(passages)
    return list(passages) + [a for a in AMORCE if a not in set(passages)]


def _minute(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _mediane(valeurs):
    v = sorted(valeurs)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def retards_par_cron(passages, horaires_utc):
    """Retard observe (minutes) de chaque passage, range sous le cron qu'il suit.

    Le cron retenu est celui dont le passage est le plus PROCHE EN AVAL : un job ne
    peut qu'etre en retard, jamais en avance, donc le retard se lit comme une distance
    orientee. Les retards trop grands ne sont attribues a personne.
    """
    out = {h: [] for h in horaires_utc}
    for brut in passages:
        try:
            t = datetime.fromisoformat(brut)
        except ValueError:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        t = t.astimezone(timezone.utc)
        observe = t.hour * 60 + t.minute
        candidats = [(((observe - _minute(h)) % 1440), h) for h in horaires_utc]
        retard, cron = min(candidats)
        if retard <= RETARD_MAX:
            out[cron].append(retard)
    return out


def observee(passages, horaires_utc):
    """Horaires reellement constates, un par cron, avec ce qui permet d'en douter.

    `fiable` a faux veut dire : pas assez de mesures, ou trop dispersees. La page ne
    doit alors afficher aucun compte a rebours -- pas un compte a rebours approximatif.
    """
    mesures = retards_par_cron(passages, horaires_utc)
    passages_out = []
    for cron in horaires_utc:
        vus = mesures.get(cron, [])
        dispersion = (max(vus) - min(vus)) if vus else None
        retard = int(round(_mediane(vus))) if vus else None
        fiable = (len(vus) >= MINIMUM_MESURES and dispersion is not None
                  and dispersion <= DISPERSION_MAX)
        attendu = (_minute(cron) + retard) % 1440 if retard is not None else None
        passages_out.append({
            "cron_utc": cron,
            "attendu_utc": f"{attendu // 60:02d}:{attendu % 60:02d}" if attendu is not None else None,
            "retard_median_min": retard,
            "dispersion_min": dispersion,
            "mesures": len(vus),
            "fiable": fiable,
        })
    # Un seul passage mesurable suffit pour annoncer quelque chose : la page vise le
    # prochain passage FIABLE et ignore les autres. Exiger que les deux le soient
    # priverait d'un compte a rebours juste a cause d'un horaire encore mal connu.
    return {"passages": passages_out,
            "fiable": any(p["fiable"] for p in passages_out),
            "minimum_mesures": MINIMUM_MESURES}
