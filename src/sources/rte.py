"""Indisponibilites de production publiees par RTE (data.rte-france.com).

Seule source du projet qui demande une cle. Tout le reste tourne sans, et cette
source-ci doit donc rester facultative : sans identifiants, les fonctions rendent une
liste vide, la colonne sort NaN, et le modele s'entraine quand meme.

CE QU'ELLE APPORTE. Le cote offre est aujourd'hui devine a partir de ce que le parc a
RECEMMENT produit -- un proxy retrospectif. Si douze reacteurs s'arretent mardi, on
l'apprend mardi. Cette API publie le calendrier des arrets, programmes et fortuits :
c'est de l'information sur le futur, pas une extrapolation du passe. Un jour Rouge nait
d'une MARGE tendue, donc de la demande FACE A l'offre, et l'offre etait le terme faible.

LE PIEGE A EVITER. La meme que pour la meteo : demander aujourd'hui « qu'est-ce qui
etait indisponible le 12 janvier » rend le savoir d'AUJOURD'HUI, y compris les arrets
declares apres coup. Un backtest bati la-dessus se mentirait. D'ou `publication_date` :
on ne retient que ce qui etait publie a la date ou la prediction aurait ete faite.
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import config

TOKEN_URL = "https://digital.iservices.rte-france.com/token/oauth/"
BASE = ("https://digital.iservices.rte-france.com"
        "/open_api/unavailability_additional_information/v7")

_jeton = {"valeur": None, "expire": 0.0}


def identifiants():
    """Le couple client, en base64, tel que RTE l'attend dans l'en-tete Basic.

    Deux formes acceptees : la chaine deja encodee (RTE_BASIC_AUTH), ou le couple
    separe (RTE_CLIENT_ID / RTE_CLIENT_SECRET) qu'on encode ici.
    """
    brut = os.environ.get("RTE_BASIC_AUTH", "").strip()
    if brut:
        return brut
    cid = os.environ.get("RTE_CLIENT_ID", "").strip()
    secret = os.environ.get("RTE_CLIENT_SECRET", "").strip()
    if cid and secret:
        return base64.b64encode(f"{cid}:{secret}".encode()).decode()
    return ""


def disponible():
    return bool(identifiants())


def jeton():
    """Jeton OAuth, garde en memoire jusqu'a une minute avant son expiration."""
    if _jeton["valeur"] and time.time() < _jeton["expire"]:
        return _jeton["valeur"]
    creds = identifiants()
    if not creds:
        return None
    req = urllib.request.Request(
        TOKEN_URL, data=b"", method="POST",
        headers={"Authorization": f"Basic {creds}",
                 "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as r:
        payload = json.loads(r.read().decode())
    _jeton["valeur"] = payload["access_token"]
    _jeton["expire"] = time.time() + float(payload.get("expires_in", 3600)) - 60
    return _jeton["valeur"]


def _get(chemin, params, retries=3):
    """Une page de resultats. Rend (donnees, jeton_de_continuation)."""
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    for essai in range(retries):
        try:
            entetes = {"Authorization": f"Bearer {jeton()}",
                       "Accept": "application/json"}
            if params.get("_suite"):
                entetes["continuation_token"] = params["_suite"]
            req = urllib.request.Request(f"{BASE}{chemin}?{qs}", headers=entetes)
            with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as r:
                return json.loads(r.read().decode()), r.headers.get("continuation_token")
        except urllib.error.HTTPError as e:
            # 429 : quota par seconde depasse. Les autres codes ne s'arrangeront pas
            # en reessayant -- on les laisse remonter pour qu'ils soient visibles.
            if e.code != 429 or essai == retries - 1:
                raise
            time.sleep(5 * (essai + 1))
        except Exception:
            if essai == retries - 1:
                raise
            time.sleep(3 * (essai + 1))


def arrets(debut, fin, fuel=None, date_type="EVENT_DATE"):
    """Indisponibilites de production chevauchant [debut, fin].

    `date_type` decide du sens de la fenetre : EVENT_DATE pour les arrets qui ont lieu
    dans la periode, PUBLICATION_DATE pour ceux qui y ont ete DECLARES -- c'est ce
    second mode qui permet de reconstituer ce qu'on savait a une date donnee.
    """
    if not disponible():
        return []
    out, suite = [], None
    while True:
        params = {
            "start_date": debut.strftime("%Y-%m-%dT00:00:00+02:00"),
            "end_date": fin.strftime("%Y-%m-%dT00:00:00+02:00"),
            "date_type": date_type,
            "last_version": "true",
            "fuel_type": fuel,
            "_suite": suite,
        }
        payload, suite = _get("/generation_unavailabilities", params)
        out += (payload or {}).get("generation_unavailabilities", [])
        if not suite:
            return out
