const COLORS = { 1: "#3b82f6", 2: "#e9edf2", 3: "#ef4444" };
const NAMES = { 1: "Bleu", 2: "Blanc", 3: "Rouge" };
const DOW = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"];
const state = { source: "all", horizon: "", period: "all" };

const fmtDate = (iso) => {
  const d = new Date(iso + "T00:00:00");
  return { dow: DOW[(d.getDay() + 6) % 7], num: d.getDate(), month: d.toLocaleDateString("fr-FR", { month: "short" }) };
};
const pct = (x) => (x == null ? "—" : Math.round(x * 100) + "%");
const euro = (x) => x.toFixed(4).replace(".", ",");
const deg = (x) => String(x).replace(".", ",") + "\u00b0";

/* ---------- acces aux donnees ----------
   La page tourne dans deux contextes : en local derriere le serveur Flask, et en
   statique (Netlify) ou les memes reponses sont figees dans web/data/. On tente
   l'API une fois ; si elle ne repond pas, on bascule sur les fichiers pour de bon. */
let staticMode = false;

function staticFile(endpoint, params) {
  if (endpoint === "forecast") return "forecast";
  const parts = [endpoint, params.source || "all"];
  if (params.period && params.period !== "all") parts.push(params.period);
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
/* La grille tarifaire vient de l'API (config.TARIFFS). Une page servie par un
   export anterieur a son ajout n'en a pas : on affiche alors les couleurs sans prix
   plutot que d'inventer un bareme perime. */
let tariffs = null;

function renderTariffGrid() {
  const body = document.querySelector("#tariff-grid tbody");
  const foot = document.getElementById("tariff-foot");
  if (!tariffs) {
    document.getElementById("tariff-grid").hidden = true;
    foot.textContent = "Grille tarifaire indisponible.";
    return;
  }
  body.innerHTML = [1, 2, 3].map((c) => {
    const t = tariffs.by_color[c];
    return `<tr data-color="${c}">
      <th scope="row"><span class="dot" style="background:${COLORS[c]}"></span>${NAMES[c]}</th>
      <td>${euro(t.hc)}</td><td class="hp">${euro(t.hp)}</td></tr>`;
  }).join("");
  foot.innerHTML = `€/kWh · ${tariffs.label}<br>barème du ${tariffs.effective}
    · heures creuses ${tariffs.offpeak_hours}`;
}

/* Le rapport au jour Bleu dit mieux que le prix brut ce que coute un jour Rouge. */
function ratioToBleu(color) {
  if (!tariffs || color === 1) return null;
  const r = tariffs.by_color[color].hp / tariffs.by_color[1].hp;
  return r < 1.15 ? null : "×" + r.toFixed(1).replace(".", ",");
}

function tariffBlock(color) {
  if (!tariffs) return "";
  const t = tariffs.by_color[color];
  const ratio = ratioToBleu(color);
  return `
    <div class="tariff">
      <div class="t-row"><span>Pleines</span>
        <b>${euro(t.hp)} €</b>${ratio ? `<em>${ratio}</em>` : ""}</div>
      <div class="t-row t-hc"><span>Creuses</span><b>${euro(t.hc)} €</b></div>
    </div>`;
}

/* Un jour Rouge probable est l'information qui fait agir : on la sort des cartes. */
function renderAlert(days) {
  const strip = document.getElementById("alert-strip");
  const rouges = days.filter((d) => d.color === 3);
  if (!rouges.length) { strip.innerHTML = ""; return; }
  const items = rouges.map((d) => {
    const f = fmtDate(d.date);
    const quand = d.horizon === 0 ? "aujourd'hui" : `${f.dow} ${f.num} ${f.month}`;
    return d.official ? `<b>${quand}</b> (officiel)`
                      : `<b>${quand}</b> (${pct(d.p[2])})`;
  });
  strip.innerHTML = `<div class="alert">
      <span class="alert-mark" aria-hidden="true"></span>
      <div><strong>${rouges.length} jour${rouges.length > 1 ? "s" : ""} Rouge</strong>
        sur les 10 prochains jours : ${items.join(" · ")}.
        ${tariffs ? `Heures pleines à <b>${euro(tariffs.by_color[3].hp)} €</b>/kWh.` : ""}</div>
    </div>`;
}

function fillGauge(color, used, quota) {
  document.getElementById(color + "-left").textContent = quota - used;
  document.getElementById(color + "-used").textContent = used;
  document.getElementById(color + "-quota").textContent = quota;
  document.getElementById(color + "-bar").style.width = (used / quota) * 100 + "%";
}

async function loadForecast() {
  const data = await loadJson("forecast");
  document.getElementById("run-date").textContent = data.run_date || "aucun";
  tariffs = data.tariffs || null;
  renderTariffGrid();

  const s = data.season;
  if (s) {
    document.getElementById("season-label").textContent = s.season;
    // bleu_left/quota_* datent de la meme version que les tarifs : on retombe sur
    // les quotas contractuels si l'export servi est plus ancien.
    fillGauge("bleu", s.bleu_used, s.quota_bleu || 300);
    fillGauge("blanc", s.blanc_used, s.quota_blanc || 43);
    fillGauge("rouge", s.rouge_used, s.quota_rouge || 22);
  }

  const box = document.getElementById("days");
  if (!data.days.length) {
    box.innerHTML = '<p class="empty">Aucune pr\u00e9diction. Lancez <code>python collector.py --train</code>.</p>';
    return;
  }
  renderAlert(data.days);
  box.innerHTML = data.days.map((d, i) => {
    const f = fmtDate(d.date);
    const temp = d.tmean == null ? "" :
      `<div class="temp"><span>temp.</span> ${deg(d.tmean)}
         <span>(${deg(d.tmin)} / ${deg(d.tmax)})</span></div>`;
    return `
      <article class="day" data-color="${d.color}" style="--col:${COLORS[d.color]};animation-delay:${i * 45}ms">
        <div class="day-head">
          <div class="date"><span class="dow">${f.dow}</span> <span class="dnum">${f.num}</span>
            <span class="dow">${f.month}</span></div>
          <span class="horizon">${d.horizon === 0 ? "auj." : "J+" + d.horizon}</span>
        </div>
        <div class="verdict">
          <span class="chip"></span><b>${d.color_name}</b>
          ${d.official ? '<span class="tag">officiel RTE</span>' : ""}
        </div>
        ${tariffBlock(d.color)}
        ${!d.p ? "" : `
        <div class="proba">
          <div class="pbar">
            <i class="b" style="width:${d.p[0] * 100}%"></i>
            <i class="w" style="width:${d.p[1] * 100}%"></i>
            <i class="r" style="width:${d.p[2] * 100}%"></i>
          </div>
          <div class="pmeta">
            <span>Bleu ${pct(d.p[0])}</span><span>Blanc ${pct(d.p[1])}</span>
            <span class="${d.p[2] > 0.15 ? "hot" : ""}">Rouge ${pct(d.p[2])}</span>
          </div>
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
/* Le denominateur decide de ce que « 90 % de reussite » veut dire : sur l'annee
   entiere il est porte par des mois ou la reponse est Bleu d'avance. */
const PERIOD_NOTES = {
  all: "Toutes les prédictions, y compris d'avril à octobre où la réponse est Bleu " +
       "d'avance : le taux de réussite y est mécaniquement flatté.",
  hiver: "Novembre à mars, la fenêtre où un jour Rouge est possible.",
  eligibles: "Uniquement les jours où le Rouge est contractuellement possible " +
             "(lundi-vendredi, novembre à mars, hors jours fériés). C'est le seul " +
             "dénominateur où le modèle a vraiment un choix à faire.",
};
document.querySelectorAll("#period-filter button").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll("#period-filter button")
      .forEach((x) => x.classList.toggle("is-active", x === b));
    state.period = b.dataset.period;
    loadHistory();
  });
});

const horizonSelect = document.getElementById("horizon-filter");
for (let h = 1; h <= 10; h++) horizonSelect.add(new Option("J+" + h, h));
horizonSelect.addEventListener("change", () => { state.horizon = horizonSelect.value; loadHistory(); });

let historyToken = 0;

async function loadHistory() {
  const token = ++historyToken;
  const params = { source: state.source, period: state.period };
  if (state.horizon) params.horizon = state.horizon;
  document.getElementById("period-note").textContent = PERIOD_NOTES[state.period];
  const [rows, acc] = await Promise.all([
    loadJson("history", { ...params, limit: 200 }),
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

  renderReliability(acc.reliability || []);

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

/* Fiabilite : l'ecart entre ce qui est annonce et ce qui tombe. Deux barres par
   tranche valent mieux qu'une courbe ici -- on lit l'ecart, pas la tendance. */
function renderReliability(bins) {
  const box = document.getElementById("reliability");
  if (!bins.length) {
    box.innerHTML = '<p class="empty">Pas encore assez de prédictions pour mesurer.</p>';
    return;
  }
  const max = Math.max(...bins.map((b) => Math.max(b.predicted, b.observed)), 0.1);
  box.innerHTML = bins.map((b) => {
    const gap = Math.abs(b.predicted - b.observed);
    return `<div class="rel-row${gap > 0.2 ? " is-off" : ""}">
      <span class="rel-bin">${b.bin}</span>
      <span class="rel-bars">
        <i class="rel-said" style="width:${(b.predicted / max) * 100}%"></i>
        <i class="rel-got" style="width:${(b.observed / max) * 100}%"></i>
      </span>
      <span class="rel-val">${pct(b.predicted)} → ${pct(b.observed)}</span>
      <span class="rel-n">n=${b.n}</span>
    </div>`;
  }).join("") +
    '<div class="rel-key"><span><i class="rel-said"></i>annoncé</span>' +
    '<span><i class="rel-got"></i>observé</span></div>';
}

loadForecast();
