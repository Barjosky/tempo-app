const COLORS = { 1: "#3b82f6", 2: "#e9edf2", 3: "#ef4444" };
const NAMES = { 1: "Bleu", 2: "Blanc", 3: "Rouge" };
const DOW = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"];
const state = { source: "all", horizon: "" };

const fmtDate = (iso) => {
  const d = new Date(iso + "T00:00:00");
  return { dow: DOW[(d.getDay() + 6) % 7], num: d.getDate(), month: d.toLocaleDateString("fr-FR", { month: "short" }) };
};
const pct = (x) => (x == null ? "—" : Math.round(x * 100) + "%");

/* ---------- acces aux donnees ----------
   La page tourne dans deux contextes : en local derriere le serveur Flask, et en
   statique (Netlify) ou les memes reponses sont figees dans web/data/. On tente
   l'API une fois ; si elle ne repond pas, on bascule sur les fichiers pour de bon. */
let staticMode = false;

function staticFile(endpoint, params) {
  if (endpoint === "forecast") return "forecast";
  const parts = [endpoint, params.source || "all"];
  if (params.horizon) parts.push("h" + params.horizon);
  return parts.join("_");
}

async function loadJson(endpoint, params = {}) {
  if (!staticMode) {
    try {
      const qs = new URLSearchParams(params).toString();
      const r = await fetch(`/api/${endpoint}${qs ? "?" + qs : ""}`);
      if (r.ok) return await r.json();
    } catch (e) {
      /* pas de serveur : on passe en statique */
    }
    staticMode = true;
  }
  const r = await fetch(`data/${staticFile(endpoint, params)}.json`);
  if (!r.ok) throw new Error(`donnees indisponibles : ${endpoint}`);
  return r.json();
}

/* ---------- onglets ---------- */
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("is-active", t === tab));
    document.querySelectorAll(".panel").forEach((p) => {
      p.classList.toggle("is-active", p.id === "panel-" + tab.dataset.tab);
    });
    if (tab.dataset.tab === "history") loadHistory();
  });
});

/* ---------- previsions ---------- */
async function loadForecast() {
  const data = await loadJson("forecast");
  document.getElementById("run-date").textContent = data.run_date || "aucun";
  const s = data.season;
  if (s) {
    document.getElementById("season-label").textContent = s.season;
    document.getElementById("rouge-left").textContent = s.rouge_left;
    document.getElementById("blanc-left").textContent = s.blanc_left;
    document.getElementById("rouge-used").textContent = s.rouge_used;
    document.getElementById("blanc-used").textContent = s.blanc_used;
    document.getElementById("rouge-bar").style.width = (s.rouge_used / 22) * 100 + "%";
    document.getElementById("blanc-bar").style.width = (s.blanc_used / 43) * 100 + "%";
  }

  const box = document.getElementById("days");
  if (!data.days.length) {
    box.innerHTML = '<p class="empty">Aucune prédiction. Lancez <code>python collector.py --train</code>.</p>';
    return;
  }
  box.innerHTML = data.days.map((d, i) => {
    const f = fmtDate(d.date);
    const temp = d.tmean == null ? "" :
      `<div class="temp">température <b>${d.tmean}°</b> <span>(${d.tmin}° / ${d.tmax}°)</span></div>`;
    return `
      <article class="day" data-color="${d.color}" style="--col:${COLORS[d.color]};animation-delay:${i * 45}ms">
        <div class="day-head">
          <div><span class="dow">${f.dow}</span> <span class="dnum">${f.num}</span>
            <span class="dow">${f.month}</span></div>
          <span class="horizon">${d.horizon === 0 ? "aujourd'hui" : "J+" + d.horizon}</span>
        </div>
        <div class="verdict">
          <span class="chip"></span><b>${d.color_name}</b>
          ${d.official ? '<span class="tag">officiel RTE</span>' : ""}
        </div>
        ${!d.p ? "" : `
        <div class="pbar">
          <i class="b" style="width:${d.p[0] * 100}%"></i>
          <i class="w" style="width:${d.p[1] * 100}%"></i>
          <i class="r" style="width:${d.p[2] * 100}%"></i>
        </div>
        <div class="pmeta">
          <span>B ${pct(d.p[0])}</span><span>Bl ${pct(d.p[1])}</span>
          <span style="color:${d.p[2] > 0.15 ? "#ef4444" : ""}">R ${pct(d.p[2])}</span>
        </div>`}
        ${temp}
      </article>`;
  }).join("");
}

/* ---------- historique ---------- */
document.querySelectorAll("#source-filter button").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll("#source-filter button").forEach((x) => x.classList.toggle("is-active", x === b));
    state.source = b.dataset.source;
    loadHistory();
  });
});
const horizonSelect = document.getElementById("horizon-filter");
for (let h = 1; h <= 10; h++) horizonSelect.add(new Option("J+" + h, h));
horizonSelect.addEventListener("change", () => { state.horizon = horizonSelect.value; loadHistory(); });

let historyToken = 0;

async function loadHistory() {
  const token = ++historyToken;
  const params = { source: state.source };
  if (state.horizon) params.horizon = state.horizon;
  const [rows, acc] = await Promise.all([
    loadJson("history", { ...params, limit: 400 }),
    loadJson("accuracy", params),
  ]);
  if (token !== historyToken) return; // un filtre plus recent a ete demande entre-temps

  // Statistiques sur l'ensemble evalue, pas sur les seules lignes affichees.
  const total = acc.by_horizon.reduce((s, h) => s + h.n, 0);
  const ok = acc.by_horizon.reduce((s, h) => s + h.accuracy * h.n, 0);
  const rouges = acc.by_horizon.reduce((s, h) => s + h.rouge_total, 0);
  const rougesFound = acc.by_horizon.reduce((s, h) => s + (h.rouge_recall || 0) * h.rouge_total, 0);
  document.getElementById("stats").innerHTML = `
    <div class="stat"><span class="label">prédictions évaluées</span><b>${total}</b>
      <small>hors annonces officielles</small></div>
    <div class="stat"><span class="label">taux de réussite</span>
      <b>${total ? pct(ok / total) : "—"}</b><small>toutes couleurs confondues</small></div>
    <div class="stat"><span class="label">rouges anticipés</span>
      <b>${rouges ? pct(rougesFound / rouges) : "—"}</b>
      <small>${Math.round(rougesFound)} / ${rouges} jours rouges</small></div>`;

  document.getElementById("horizon-bars").innerHTML = acc.by_horizon.length
    ? acc.by_horizon.map((h) => `
      <div class="hbar"><span>J+${h.horizon}</span>
        <span class="t"><i style="width:${(h.accuracy || 0) * 100}%"></i></span>
        <span class="v">${pct(h.accuracy)} · R ${pct(h.rouge_recall)}</span></div>`).join("")
    : '<p class="empty">Pas encore de données.</p>';

  const labels = ["Bleu", "Blanc", "Rouge"];
  const max = Math.max(1, ...acc.confusion.flat());
  document.getElementById("confusion").innerHTML =
    ['<div class="h"></div>', ...labels.map((l) => `<div class="h">${l}</div>`)].join("") +
    acc.confusion.map((row, i) =>
      `<div class="h">${labels[i]}</div>` + row.map((v, j) =>
        `<div class="${i === j ? "diag" : ""}" style="background:rgba(59,130,246,${0.08 + 0.5 * v / max})">${v}</div>`
      ).join("")).join("");

  const tbody = document.querySelector("#history-table tbody");
  tbody.innerHTML = rows.length ? rows.map((r) => `
    <tr class="${r.correct || r.official ? "" : "miss"}">
      <td>${r.target_date}</td><td>J+${r.horizon}</td>
      <td><span class="dot" style="background:${COLORS[r.predicted]}"></span>${r.predicted_name}</td>
      <td><span class="dot" style="background:${COLORS[r.actual]}"></span>${r.actual_name}</td>
      <td>${pct(r.p[0])}</td><td>${pct(r.p[1])}</td><td>${pct(r.p[2])}</td>
      <td class="src">${r.official ? "officiel" : r.backtest ? "backtest" : "temps réel"}</td>
    </tr>`).join("")
    : '<tr><td colspan="8" class="empty">Aucune prédiction évaluable pour ce filtre.</td></tr>';
}

loadForecast();
