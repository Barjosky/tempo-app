"""Serveur web : previsions 10 jours + historique de suivi."""
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

sys.path.insert(0, str(Path(__file__).parent))
import config
from src import db
from src.sources import calendrier

app = Flask(__name__, static_folder=str(config.SITE_DIR), static_url_path="")
COLOR_NAMES = {1: "Bleu", 2: "Blanc", 3: "Rouge"}
LIVE_VERSIONS = "model_version NOT LIKE 'backtest%'"


def season_summary(conn, today=None):
    today = today or date.today()
    season = calendrier.season_of(today)
    rows = conn.execute(
        "SELECT color, COUNT(*) n FROM days WHERE season = ? AND color IS NOT NULL GROUP BY color",
        (season,)).fetchall()
    counts = {r["color"]: r["n"] for r in rows}
    return {
        "season": season,
        "rouge_used": counts.get(3, 0), "rouge_left": config.QUOTA_ROUGE - counts.get(3, 0),
        "blanc_used": counts.get(2, 0), "blanc_left": config.QUOTA_BLANC - counts.get(2, 0),
        "bleu_used": counts.get(1, 0),
    }


@app.get("/")
def index():
    return send_from_directory(str(config.SITE_DIR), "index.html")


@app.get("/api/forecast")
def forecast():
    conn = db.connect()
    last_run = conn.execute(
        f"SELECT MAX(run_date) FROM predictions WHERE {LIVE_VERSIONS}").fetchone()[0]
    items = []
    if last_run:
        # J+0 : la couleur du jour, connue et non predite. Sans elle la page
        # commence a demain, alors que c'est aujourd'hui qu'on consomme.
        today_row = conn.execute(
            "SELECT date, color FROM days WHERE date = ? AND color IS NOT NULL",
            (last_run,)).fetchone()
        if today_row:
            items.append({
                "date": today_row["date"], "horizon": 0,
                "weekday": date.fromisoformat(today_row["date"]).weekday(),
                "color": today_row["color"], "color_name": COLOR_NAMES[today_row["color"]],
                "p": None, "official": True, "confidence": 1.0,
            })
        rows = conn.execute(
            f"""SELECT p.*, d.color AS actual, d.is_holiday
                FROM predictions p LEFT JOIN days d ON d.date = p.target_date
                WHERE p.run_date = ? AND {LIVE_VERSIONS} ORDER BY p.horizon""",
            (last_run,)).fetchall()
        for r in rows:
            official = r["actual"] is not None
            items.append({
                "date": r["target_date"], "horizon": r["horizon"],
                "weekday": date.fromisoformat(r["target_date"]).weekday(),
                "color": r["actual"] if official else r["predicted_color"],
                "color_name": COLOR_NAMES[r["actual"] if official else r["predicted_color"]],
                "p": [r["p_bleu"], r["p_blanc"], r["p_rouge"]],
                "official": official,
                "confidence": max(r["p_bleu"], r["p_blanc"], r["p_rouge"]),
            })
    # 'forecast' d'abord (les jours a venir), puis l'observe pour J+0.
    weather = {}
    for source in ("era5", "forecast"):
        for r in conn.execute(
                "SELECT date, tmean, tmin, tmax FROM weather WHERE source = ?", (source,)):
            weather[r["date"]] = dict(r)
    for it in items:
        w = weather.get(it["date"])
        it["tmean"] = round(w["tmean"], 1) if w else None
        it["tmin"] = round(w["tmin"], 1) if w else None
        it["tmax"] = round(w["tmax"], 1) if w else None
    return jsonify({"run_date": last_run, "days": items, "season": season_summary(conn)})


@app.get("/api/history")
def history():
    """Predictions figees confrontees a la couleur reellement tombee."""
    conn = db.connect()
    horizon = request.args.get("horizon", type=int)
    limit = request.args.get("limit", default=400, type=int)
    source = request.args.get("source", default="all")
    where = ["d.color IS NOT NULL"]
    params = []
    if horizon:
        where.append("p.horizon = ?")
        params.append(horizon)
    if source == "live":
        where.append(LIVE_VERSIONS)
    elif source == "backtest":
        where.append("model_version LIKE 'backtest%'")
    rows = conn.execute(
        f"""SELECT p.*, d.color AS actual FROM predictions p
            JOIN days d ON d.date = p.target_date
            WHERE {' AND '.join(where)}
            ORDER BY p.target_date DESC, p.horizon LIMIT ?""",
        (*params, limit)).fetchall()
    return jsonify([{
        "target_date": r["target_date"], "horizon": r["horizon"],
        "predicted": r["predicted_color"], "predicted_name": COLOR_NAMES[r["predicted_color"]],
        "actual": r["actual"], "actual_name": COLOR_NAMES[r["actual"]],
        "correct": r["predicted_color"] == r["actual"],
        "p": [r["p_bleu"], r["p_blanc"], r["p_rouge"]],
        "official": bool(r["is_official"]),
        "backtest": r["model_version"].startswith("backtest"),
    } for r in rows])


@app.get("/api/accuracy")
def accuracy():
    """Precision par horizon + matrice de confusion, sur les predictions figees."""
    conn = db.connect()
    source = request.args.get("source", default="all")
    horizon = request.args.get("horizon", type=int)
    cond, params = "", []
    if source == "live":
        cond = f"AND {LIVE_VERSIONS}"
    elif source == "backtest":
        cond = "AND model_version LIKE 'backtest%'"
    if horizon:
        cond += " AND p.horizon = ?"
        params.append(horizon)
    rows = conn.execute(
        f"""SELECT p.horizon, p.predicted_color, p.p_rouge, d.color AS actual
            FROM predictions p JOIN days d ON d.date = p.target_date
            WHERE d.color IS NOT NULL AND p.is_official = 0 {cond}""", params).fetchall()
    by_h, confusion = {}, [[0] * 3 for _ in range(3)]
    for r in rows:
        h = by_h.setdefault(r["horizon"], {"n": 0, "ok": 0, "rouge_total": 0, "rouge_found": 0})
        h["n"] += 1
        h["ok"] += r["predicted_color"] == r["actual"]
        if r["actual"] == 3:
            h["rouge_total"] += 1
            h["rouge_found"] += r["predicted_color"] == 3
        confusion[r["actual"] - 1][r["predicted_color"] - 1] += 1
    out = [{
        "horizon": h, "n": v["n"], "accuracy": v["ok"] / v["n"] if v["n"] else None,
        "rouge_recall": v["rouge_found"] / v["rouge_total"] if v["rouge_total"] else None,
        "rouge_total": v["rouge_total"],
    } for h, v in sorted(by_h.items())]
    return jsonify({"by_horizon": out, "confusion": confusion, "n": len(rows)})


@app.get("/api/backtest")
def backtest_report():
    files = sorted(config.REPORTS_DIR.glob("backtest_*.json"))
    if not files:
        return jsonify({})
    return jsonify(json.loads(files[-1].read_text(encoding="utf-8")))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5173, debug=False)
