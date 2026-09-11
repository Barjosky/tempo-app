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


class ErreurRTE(Exception):
    """Refus de l'API, avec ce qu'elle en dit -- pas seulement son code."""

    def __init__(self, code, corps, url=""):
        self.code, self.corps, self.url = code, corps, url
        super().__init__(f"HTTP {code} — {corps}")


def _corps(e):
    try:
        brut = e.read().decode("utf-8", "replace").strip()
    except Exception:
        return "(corps illisible)"
    try:
        return json.dumps(json.loads(brut), ensure_ascii=False)[:600]
    except Exception:
        return brut[:600] or "(corps vide)"


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
    # `_suite` est interne : RTE attend le jeton de continuation dans un EN-TETE, et
    # refuse tout parametre de requete qu'il ne connait pas.
    qs = urllib.parse.urlencode({k: v for k, v in params.items()
                                 if v is not None and not k.startswith("_")})
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
                # RTE explique ses refus dans le CORPS de la reponse. Le laisser
                # tomber pour ne garder que « 400 » revient a jeter le diagnostic et
                # a deviner ensuite : l'erreur remonte donc avec son explication.
                raise ErreurRTE(e.code, _corps(e), f"{BASE}{chemin}?{qs}") from None
            time.sleep(5 * (essai + 1))
        except Exception:
            if essai == retries - 1:
                raise
            time.sleep(3 * (essai + 1))


def arrets(debut, fin, fuel=None, date_type="EVENT_DATE", derniere_version=True):
    """Indisponibilites de production, dans la fenetre [debut, fin].

    `date_type` decide du sens de la fenetre : EVENT_DATE pour les arrets qui ont LIEU
    dans la periode, PUBLICATION_DATE pour ceux qui y ont ete DECLARES.

    `derniere_version` est le parametre qui decide de l'honnetete d'un backtest. A vrai,
    RTE ne rend que l'etat FINAL de chaque arret -- corrige, prolonge, parfois annule
    apres coup. Reconstituer le passe avec ca ferait entrer du futur dans les features.
    Pour la collecte historique on le met donc a faux et on garde toutes les versions,
    chacune avec sa date de publication ; c'est au moment de construire les features
    qu'on choisit la plus recente publiee AVANT la date de prediction.
    """
    if not disponible():
        return []
    out, suite = [], None
    while True:
        params = {
            # RTE refuse un decalage horaire explicite (« +02:00 ») et n'accepte que
            # le suffixe Z : UNADINFO_GENUN_F03, « does not follow the format
            # described in the user guide ». Mesure faite, pas supposee. En UTC les
            # dates sont d'ailleurs sans ambiguite d'une saison a l'autre.
            "start_date": debut.strftime("%Y-%m-%dT00:00:00Z"),
            "end_date": fin.strftime("%Y-%m-%dT00:00:00Z"),
            "date_type": date_type,
            "last_version": "true" if derniere_version else "false",
            "fuel_type": fuel,
            "_suite": suite,
        }
        payload, suite = _get("/generation_unavailabilities", params)
        out += (payload or {}).get("generation_unavailabilities", [])
        if not suite:
            return out


def _jour(texte):
    """Le jour calendaire d'un horodatage RTE, en ISO. None si absent ou illisible."""
    if not texte:
        return None
    return texte[:10] if len(texte) >= 10 and texte[4] == "-" else None


def paliers(evenement, fetched_at=""):
    """Aplati un arret en lignes de base : un palier de puissance = une ligne.

    Un arret long ne retire pas la meme puissance du premier au dernier jour. RTE le dit
    dans `values` ; s'en tenir a (start_date, end_date, puissance maximale) surestimerait
    la puissance perdue sur toute la duree. Sans `values`, on retombe sur la fenetre
    entiere et sur la puissance installee, faute de mieux.
    """
    ident = evenement.get("identifier")
    pub = _jour(evenement.get("publication_date"))
    if not ident or not pub:
        # Sans date de publication, la ligne est inutilisable en point-in-time : on ne
        # saurait pas a partir de quand elle etait connue. La jeter vaut mieux que de
        # lui inventer une date.
        return []
    installee = evenement.get("affected_asset_or_unit_installed_capacity")
    commun = {
        "identifier": ident,
        "version": int(evenement.get("version") or 0),
        "publication_date": pub,
        "fuel_type": evenement.get("fuel_type"),
        "unavailability_type": evenement.get("unavailability_type"),
        "event_status": evenement.get("event_status"),
        "unit_name": evenement.get("affected_asset_or_unit_name"),
        "installed_mw": float(installee) if installee is not None else None,
        "fetched_at": fetched_at,
    }
    lignes, vus = [], set()
    for v in (evenement.get("values") or []):
        debut, fin = _jour(v.get("start_date")), _jour(v.get("end_date"))
        indispo = v.get("unavailable_capacity")
        if not debut or not fin or indispo is None or debut in vus:
            continue
        vus.add(debut)
        lignes.append(dict(commun, palier_start=debut, palier_end=fin,
                           unavailable_mw=float(indispo)))
    if lignes:
        return lignes
    debut, fin = _jour(evenement.get("start_date")), _jour(evenement.get("end_date"))
    if not debut or not fin:
        return []
    return [dict(commun, palier_start=debut, palier_end=fin,
                 unavailable_mw=commun["installed_mw"])]
