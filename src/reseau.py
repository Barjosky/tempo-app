"""Reessai des appels reseau : distinguer ce qui passe tout seul de ce qui ne passera pas.

LE 20 SEPTEMBRE 2026, une poignee de main TLS vers Open-Meteo a expire et le passage
est mort. Les trois clients du projet reessayaient alors trois fois en attendant 3 s
puis 6 s -- NEUF SECONDES de patience, quand chaque tentative brule soixante secondes
de timeout. Une coupure reseau qui dure plus de dix secondes n'etait couverte par rien.

Deuxieme defaut du meme code : il reessayait N'IMPORTE QUELLE erreur. Un HTTP 400 --
une requete malformee -- etait retente trois fois alors qu'il ne s'arrangera jamais,
ce qui retarde le diagnostic sans rien sauver.

D'ou deux notions separees :

  - EST-CE TRANSITOIRE ? Une coupure reseau, une poignee de main qui expire, un 5xx,
    un 429 passent tout seuls. Un 4xx non : il dit que la demande est fausse.
    Chaque client classe SES erreurs, parce que lui seul sait ce qu'elles veulent dire.

  - COMBIEN DE TEMPS INSISTER ? Un budget, pas un nombre de tentatives. Compter les
    tentatives ne dit rien du temps reellement passe a attendre, qui est la seule
    chose qui compte face a une coupure : c'est elle qu'on essaie d'enjamber.
"""
import time

# Attentes successives, en secondes. La somme (110 s) est la fenetre de coupure qu'on
# sait enjamber ; au-dela, le budget arrete les frais. Elles croissent parce qu'une
# panne encore la apres trente secondes a peu de chances de partir dans la seconde.
ATTENTES = (5, 15, 30, 60)

# Plafond du temps passe A ATTENDRE entre deux tentatives, hors appels eux-memes.
# Deux minutes couvrent le genre de trou mesure le 20 septembre sans immobiliser le
# passage : une panne plus longue est une vraie panne, et le passage doit le dire.
BUDGET = 120


def reessayer(tentative, transitoire, budget=BUDGET, dormir=None):
    """Rejoue `tentative` tant que l'echec est du genre qui passe tout seul.

    `transitoire(exc)` tranche : vrai, on attend et on recommence ; faux, l'erreur
    remonte immediatement. Le budget compte le temps DEJA attendu, de sorte qu'une
    coupure longue s'arrete net au lieu de s'etirer indefiniment.
    """
    # `dormir` se resout ICI et non en valeur par defaut : un defaut est evalue a la
    # DEFINITION, ce qui fige `time.sleep` une fois pour toutes et rend la fonction
    # intestable par substitution -- le test attendait alors vraiment ses deux minutes.
    dormir = dormir or time.sleep
    attendu = 0.0
    for pause in ATTENTES + (None,):
        try:
            return tentative()
        except Exception as exc:
            # Derniere pause a None : plus de tentative apres celle-ci, l'erreur sort.
            if pause is None or not transitoire(exc) or attendu + pause > budget:
                raise
            dormir(pause)
            attendu += pause


def transitoire_reseau(exc):
    """Les pannes de tuyau : coupure, DNS, poignee de main qui expire, socket ferme.

    Aucune ne dit quoi que ce soit sur la validite de la demande -- elles disent que
    le message n'est pas passe. C'est exactement ce qu'il faut reessayer.

    `HTTPError` est volontairement absent : il HERITE de `URLError`, et le laisser
    entrer ferait passer un 400 pour une panne reseau. Les codes HTTP se classent
    chez l'appelant, avec `transitoire_http`.
    """
    import http.client
    import socket
    import urllib.error
    if isinstance(exc, urllib.error.HTTPError):
        return False
    return isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError,
                            socket.timeout, http.client.HTTPException))


def transitoire_http(code):
    """429 demande explicitement d'attendre ; 5xx est une panne du serveur.

    Tout le reste des codes d'erreur vient de la demande elle-meme et sera refuse a
    l'identique dans dix secondes.
    """
    return code == 429 or code >= 500
