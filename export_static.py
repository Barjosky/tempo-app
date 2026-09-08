"""Fige les reponses de l'API en fichiers JSON, pour un hebergement statique (Netlify).

On interroge le serveur Flask via son client de test plutot que de reecrire les
requetes SQL : la page statique sert donc exactement les memes donnees que la
version locale, sans risque de divergence entre les deux implementations.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config
from app import app

OUT = config.SITE_DIR / "data"
SOURCES = ["all", "live", "backtest"]


def dump(client, name, url):
    resp = client.get(url)
    if resp.status_code != 200:
        raise SystemExit(f"{url} a repondu {resp.status_code}")
    (OUT / f"{name}.json").write_text(
        json.dumps(resp.get_json(), separators=(",", ":")), encoding="utf-8")
    return len(resp.data)


def main():
    # On ne vide que les JSON : le dossier peut contenir autre chose (sur le depot
    # publie, il est a la racine du site).
    OUT.mkdir(parents=True, exist_ok=True)
    for ancien in OUT.glob("*.json"):
        ancien.unlink()
    total = 0
    with app.test_client() as client:
        total += dump(client, "forecast", "/api/forecast")
        for source in SOURCES:
            total += dump(client, f"history_{source}",
                          f"/api/history?source={source}&limit=400")
            total += dump(client, f"accuracy_{source}", f"/api/accuracy?source={source}")
            for h in range(1, config.MAX_HORIZON + 1):
                total += dump(client, f"history_{source}_h{h}",
                              f"/api/history?source={source}&horizon={h}&limit=400")
                total += dump(client, f"accuracy_{source}_h{h}",
                              f"/api/accuracy?source={source}&horizon={h}")
        try:
            total += dump(client, "backtest", "/api/backtest")
        except SystemExit:
            pass  # aucun rapport de backtest publie : la page s'en passe

    (OUT / "meta.json").write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }), encoding="utf-8")

    files = len(list(OUT.glob("*.json")))
    print(f"{files} fichiers ecrits dans {OUT} ({total / 1024:.0f} Ko)")


if __name__ == "__main__":
    main()
