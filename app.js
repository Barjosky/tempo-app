const COLORS = { 1: "#3b82f6", 2: "#e9edf2", 3: "#ef4444" };
const NAMES = { 1: "Bleu", 2: "Blanc", 3: "Rouge" };
const DOW = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"];
/* Le perimetre par defaut est le plus honnete, pas le plus flatteur : sur l'annee
   entiere le taux est porte par des mois ou la reponse est Bleu d'avance. Ouvrir sur
   90 % quand le chiffre qui engage est 72 % reviendrait a mettre en avant celui qu'on
   sait trompeur. */
const state = { source: "all", horizon: "", period: "eligibles", missOnly: false };

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
  // `no-cache` force la REVALIDATION a chaque chargement : le navigateur redemande, le
  // serveur repond 304 si rien n'a bouge, et la vraie copie sinon. Sans ca, un
  // navigateur peut servir la prevision de la veille aussi longtemps qu'il la juge
  // fraiche -- et c'est arrive : la page a continue d'afficher les jours dedoubles
  // alors que le correctif etait publie depuis un quart d'heure.
  //
  // L'empreinte d'index.html ne couvrait que le script et la feuille de style. Elle
  // protegeait donc tout SAUF la partie qui change deux fois par jour.
  const r = await fetch(`data/${staticFile(endpoint, params)}.json`, { cache: "no-cache" });
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

/* Le prix ne figure sur la carte que s'il DIFFERE du Bleu. Les onze cartes
   repetaient sinon le meme tarif a l'identique, alors que la colonne de gauche porte
   deja la grille complete des trois couleurs, en permanence. Un jour ordinaire n'a
   rien a dire sur le prix ; un jour cher, si. */
function tariffBlock(color) {
  if (!tariffs || color === 1) return "";
  const t = tariffs.by_color[color];
  const ratio = ratioToBleu(color);
  return `
    <div class="tariff">
      <div class="t-main">
        <b>${euro(t.hp)}\u202f€</b>${ratio ? `<em>${ratio}</em>` : ""}
        <span>en heures pleines</span>
      </div>
      <div class="t-row t-hc"><span>Creuses</span><b>${euro(t.hc)}\u202f€</b></div>
    </div>`;
}

/* Une journee est « calme » quand il n'y a rien a decider : Bleu, et sans hesitation
   serieuse. Sa carte se replie alors, pour que les journees qui comptent ressortent
   d'elles-memes au lieu d'etre noyees dans un mur de cartes identiques. */
function estCalme(d) {
  if (d.color !== 1) return false;
  if (!d.p) return true;
  return d.p[1] < 0.12 && d.p[2] < 0.05;
}

/* Un jour Rouge probable est l'information qui fait agir : on la sort des cartes.

   TROIS DEFAUTS CORRIGES ICI, tous vus au rendu d'un jeu d'hiver -- invisibles en
   septembre, ou tout est Bleu :
     - des qu'un Rouge apparaissait, les jours Blanc DISPARAISSAIENT du bandeau. La page
       etait donc plus bavarde quand il n'y avait rien a dire que quand il y avait
       quelque chose. Un Blanc coute pourtant 1,2 fois le tarif Bleu en heures pleines ;
     - « Tout reste au tarif Bleu » s'affichait meme en annoncant trois jours Blanc,
       dans la meme phrase ;
     - « sur les 10 prochains jours » etait ecrit en dur d'un cote et valait
       `days.length` de l'autre, qui compte AUSSI aujourd'hui. Les deux etaient faux. */
function renderAlert(days) {
  const strip = document.getElementById("alert-strip");
  // Aujourd'hui n'est pas un jour « prochain » : seules les echeances comptent.
  const aVenir = days.filter((d) => d.horizon > 0).length;
  const rouges = days.filter((d) => d.color === 3);
  const blancs = days.filter((d) => d.color === 2);
  const nomme = (d) => {
    const f = fmtDate(d.date);
    const quand = d.horizon === 0 ? "aujourd'hui" : `${f.dow} ${f.num} ${f.month}`;
    const sur = (i) => d.official ? "(officiel)" : `(${pct(d.p[i])})`;
    return `<b>${quand}</b> ${sur(d.color - 1)}`;
  };

  if (!rouges.length && !blancs.length) {
    // La reponse a « dois-je m'inquieter ? » doit se lire sans parcourir onze cartes.
    // Quand il n'y a rien, on le dit.
    strip.innerHTML = `<div class="calm">
        <span class="calm-mark" aria-hidden="true"></span>
        <div><strong>Rien à signaler</strong> sur les ${aVenir} prochains jours :
          aucun jour Blanc ni Rouge, tout reste au tarif Bleu.</div>
      </div>`;
    return;
  }

  const parts = [];
  if (rouges.length) {
    parts.push(`<strong class="r">${rouges.length} jour${rouges.length > 1 ? "s" : ""} Rouge</strong> :
                ${rouges.map(nomme).join(" · ")}`);
  }
  if (blancs.length) {
    parts.push(`<strong class="b">${blancs.length} jour${blancs.length > 1 ? "s" : ""} Blanc</strong> :
                ${blancs.map(nomme).join(" · ")}`);
  }
  // Le prix annonce est celui de la couleur la plus chere presente, pas le Rouge par
  // defaut : un bandeau qui ne contient que des Blanc ne doit pas afficher 0,7295 €.
  const pire = rouges.length ? 3 : 2;
  const prix = tariffs
    ? ` Heures pleines à <b>${euro(tariffs.by_color[pire].hp)} €</b>/kWh.` : "";
  const classe = rouges.length ? "alert" : "alert is-blanc";
  strip.innerHTML = `<div class="${classe}">
      <span class="alert-mark" aria-hidden="true"></span>
      <div>Sur les ${aVenir} prochains jours — ${parts.join(" · ")}.${prix}</div>
    </div>`;
}

/* La barre represente ce qui RESTE, comme son titre. Elle montrait le consomme :
   « BLEUS RESTANTS 288 » surmontait donc une barre quasi vide, le chiffre et le
   dessin racontant l'inverse l'un de l'autre. */
function fillGauge(color, used, quota) {
  document.getElementById(color + "-left").textContent = quota - used;
  document.getElementById(color + "-used").textContent = used;
  document.getElementById(color + "-quota").textContent = quota;
  document.getElementById(color + "-bar").style.width = ((quota - used) / quota) * 100 + "%";
}

async function loadForecast() {
  const data = await loadJson("forecast");
  document.getElementById("run-date").textContent = data.run_date || "aucun";
  // La date seule ne dit pas la fraicheur : le 11 septembre a 8 h et le 11 septembre a
  // 22 h sont deux pages tres differentes, et l'une des deux est peut-etre a jeter.
  // `derniere_maj` est l'instant ou la collecte a tourne, donc ou ces predictions ont
  // ete calculees -- pas celui ou le fichier a ete ecrit.
  majAffichee(data.derniere_maj);
  // Le rechargement automatique reste : il ne promet rien, il constate.
  guetterNouvellesDonnees();
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
    const calme = estCalme(d);
    // La temperature explique la prevision : elle monte dans l'en-tete, au lieu
    // d'etre reléguee en bas de carte en petit.
    const temp = d.tmean == null ? "" :
      `<span class="t-now">${deg(d.tmean)}</span>
       <span class="t-range">${deg(d.tmin)} / ${deg(d.tmax)}</span>`;
    // Detailler « Bleu 100 % · Blanc 0 % · Rouge 0 % » n'apprend rien. La barre et
    // le detail n'apparaissent que s'il y a vraiment une hesitation a montrer.
    const proba = (!d.p || calme) ? "" : `
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
        </div>`;
    return `
      <article class="day${calme ? " is-calme" : ""}" data-color="${d.color}"
               style="--col:${COLORS[d.color]};animation-delay:${i * 40}ms">
        <div class="day-head">
          <div class="date"><span class="dow">${f.dow}</span> <span class="dnum">${f.num}</span>
            <span class="dow">${f.month}</span></div>
          <span class="horizon">${d.horizon === 0 ? "auj." : "J+" + d.horizon}</span>
        </div>
        <div class="verdict">
          <span class="chip"></span><b>${d.color_name}</b>
          ${d.official ? '<span class="tag">officiel RTE</span>' : ""}
        </div>
        ${temp ? `<div class="temp">${temp}</div>` : ""}
        ${tariffBlock(d.color)}
        ${proba}
      </article>`;
  }).join("");
}

/* ---------- fraicheur ----------
   Le compte a rebours vers le prochain calcul a ete retire : il promettait une heure
   que GitHub Actions ne tient pas. Mesure faite sur les passages reels, le cron part
   avec deux a quatre heures de retard, et de facon variable d'un creneau a l'autre --
   annoncer une heure precise revenait a annoncer celle de quelqu'un d'autre.

   Ne reste que ce qui est VRAI par construction : quand le dernier calcul a eu lieu.
   Le lecteur en tire lui-meme ce qu'il veut savoir -- la page est-elle fraiche -- sans
   qu'on lui promette rien sur la suite. La cadence continue d'etre mesuree et publiee
   (`data.cadence`), elle ne sert simplement plus a promettre. */

/* Les horodatages du serveur sont en UTC. Certains portent leur decalage, d'autres non
   -- et une chaine sans fuseau est interpretee comme une heure LOCALE par le
   navigateur, ce qui decalerait tout de deux heures en ete. On force donc le Z absent. */
function horodatage(iso) {
  if (!iso) return null;
  const t = new Date(/[Z+]|-\d\d:\d\d$/.test(iso.slice(10)) ? iso : iso + "Z");
  return isNaN(t) ? null : t;
}

/* L'heure du dernier calcul, dans le fuseau du lecteur. La date seule ne dit pas la
   fraicheur : le 12 septembre a 8 h et le 12 septembre a 22 h sont deux pages tres
   differentes. */
function majAffichee(iso) {
  const el = document.getElementById("run-at");
  if (!el) return;
  const t = horodatage(iso);
  el.textContent = t
    ? "à " + t.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })
    : "";
}

/* On ne recharge pas « a l'heure dite » : le job peut avoir pris du retard, et on
   servirait alors les donnees de la veille en croyant les rafraichir. On recharge
   quand la date de generation a REELLEMENT change. */
let guet = null;
let metaInitial = null;

function guetterNouvellesDonnees() {
  if (guet) return;
  guet = setInterval(async () => {
    try {
      const r = await fetch("data/meta.json?" + Date.now(), { cache: "no-store" });
      if (!r.ok) return;
      const meta = (await r.json()).generated_at;
      if (metaInitial === null) { metaInitial = meta; return; }
      if (meta !== metaInitial) location.reload();
    } catch (e) {
      /* hors ligne ou servie par l'API : on retentera au prochain tour */
    }
  }, 120000);
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

const missToggle = document.getElementById("miss-only");
if (missToggle) {
  missToggle.addEventListener("click", () => { state.missOnly = !state.missOnly; loadHistory(); });
}

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
  const alertes = acc.by_horizon.reduce((s, h) => s + (h.rouge_flagged || 0), 0);
  document.getElementById("stats").innerHTML = `
    <div class="stat"><span class="label">prédictions évaluées</span><b>${total}</b>
      <small>hors annonces officielles</small></div>
    <div class="stat"><span class="label">taux de réussite</span>
      <b>${total ? pct(ok / total) : "—"}</b><small>toutes couleurs confondues</small></div>
    <div class="stat"><span class="label">rouges anticipés</span>
      <b>${rouges ? pct(rougesFound / rouges) : "—"}</b>
      <small>${Math.round(rougesFound)} / ${rouges} jours rouges</small></div>
    <div class="stat" data-warn="${alertes && rougesFound / alertes < 0.5 ? "1" : ""}">
      <span class="label">alertes justifiées</span>
      <b>${alertes ? pct(rougesFound / alertes) : "—"}</b>
      <small>${Math.round(rougesFound)} / ${alertes} jours annoncés Rouge</small></div>`;

  renderHorizons(acc.by_horizon);

  const labels = ["Bleu", "Blanc", "Rouge"];
  const max = Math.max(1, ...acc.confusion.flat());
  document.getElementById("confusion").innerHTML =
    ['<div class="h"></div>', ...labels.map((l) => `<div class="h">${l}</div>`)].join("") +
    acc.confusion.map((row, i) =>
      `<div class="h">${labels[i]}</div>` + row.map((v, j) =>
        `<div class="${i === j ? "diag" : ""}" style="background:rgba(59,130,246,${0.08 + 0.5 * v / max})">${v}</div>`
      ).join("")).join("");

  renderCalibration(acc.reliability || []);

  // Les lignes justes se ressemblent toutes ; ce sont les ecarts qu'on vient lire.
  // Elles sont donc marquees, comptees, et isolables d'un clic.
  const rates = rows.filter((r) => !(r.correct || r.official));
  const shown = state.missOnly ? rates : rows;
  const toggle = document.getElementById("miss-only");
  if (toggle) {
    toggle.classList.toggle("is-active", state.missOnly);
    toggle.textContent = state.missOnly
      ? `Tout afficher (${rows.length})`
      : `Erreurs seulement (${rates.length})`;
    toggle.disabled = !rates.length && !state.missOnly;
  }

  const tbody = document.querySelector("#history-table tbody");
  tbody.innerHTML = shown.length ? shown.map((r) => `
    <tr class="${r.correct || r.official ? "" : "miss"}">
      <td>${r.target_date}</td><td>J+${r.horizon}</td>
      <td><span class="dot" style="background:${COLORS[r.predicted]}"></span>${r.predicted_name}</td>
      <td><span class="dot" style="background:${COLORS[r.actual]}"></span>${r.actual_name}</td>
      <td>${pct(r.p[0])}</td><td>${pct(r.p[1])}</td><td>${pct(r.p[2])}</td>
      <td class="src">${r.official ? "officiel" : r.backtest ? "backtest" : "temps réel"}</td>
    </tr>`).join("")
    : `<tr><td colspan="8" class="empty">${state.missOnly
        ? "Aucune erreur sur les lignes affichées."
        : "Aucune prédiction évaluable pour ce filtre."}</td></tr>`;
}

/* Une suite de dix barres partant toutes de zero rendait la decroissance invisible :
   91 % et 89 % donnent deux barres identiques a l'oeil. Ce qui se raconte ici est une
   TENDANCE le long des echeances, donc une ligne -- forme ou une echelle qui ne part
   pas de zero est licite, a condition de l'afficher, ce que fait l'axe. */
function renderHorizons(rows) {
  const box = document.getElementById("horizon-bars");
  if (!rows.length) { box.innerHTML = '<p class="empty">Pas encore de données.</p>'; return; }

  const vals = rows.map((h) => h.accuracy || 0);
  // L'echelle se cale sur les donnees, arrondie aux 10 points, et reste affichee.
  const lo = Math.max(0, Math.floor(Math.min(...vals) * 10) / 10 - 0.05);
  const hi = Math.min(1, Math.ceil(Math.max(...vals) * 10) / 10 + 0.05);
  const W = 560, H = 190, ML = 46, MR = 52, MT = 14, MB = 30;
  const x = (i) => ML + (i / Math.max(1, rows.length - 1)) * (W - ML - MR);
  const y = (v) => MT + (1 - (v - lo) / (hi - lo)) * (H - MT - MB);

  const ticks = [lo, (lo + hi) / 2, hi].map((v) => `
    <line x1="${ML}" x2="${W - MR}" y1="${y(v)}" y2="${y(v)}" class="grid"/>
    <text x="${ML - 8}" y="${y(v) + 4}" class="ax" text-anchor="end">${Math.round(v * 100)}%</text>`).join("");

  const path = rows.map((h, i) => `${i ? "L" : "M"}${x(i)},${y(h.accuracy || 0)}`).join(" ");
  const pts = rows.map((h, i) => `
    <circle cx="${x(i)}" cy="${y(h.accuracy || 0)}" r="4.5" class="pt"/>
    <circle cx="${x(i)}" cy="${y(h.accuracy || 0)}" r="13" class="hit"
      data-tip="J+${h.horizon} — réussite ${pct(h.accuracy)} · rouges anticipés ${pct(h.rouge_recall)} · alertes justifiées ${pct(h.rouge_precision)} · ${h.n} prédictions"/>`).join("");

  const xlab = rows.map((h, i) =>
    (i === 0 || i === rows.length - 1 || (i + 1) % 3 === 0)
      ? `<text x="${x(i)}" y="${H - 8}" class="ax" text-anchor="middle">J+${h.horizon}</text>` : "").join("");

  // Une seule serie : pas de legende, le titre la nomme. Seul l'extremite est
  // etiquetee, plutot qu'un nombre sur chaque point.
  const last = rows.length - 1;
  box.innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" class="chart" role="img"
         aria-label="Taux de réussite par échéance, de J+1 à J+${rows[last].horizon}">
      ${ticks}${xlab}
      <path d="${path}" class="line"/>
      ${pts}
      <text x="${x(last) + 10}" y="${y(rows[last].accuracy || 0) + 4}" class="endlab">${pct(rows[last].accuracy)}</text>
      <text x="${x(0) + 10}" y="${y(rows[0].accuracy || 0) - 10}" class="endlab">${pct(rows[0].accuracy)}</text>
    </svg>`;
  attachTips(box);
}

/* Fiabilite : ce qui est annonce contre ce qui tombe. Le texte parlait de « rester
   pres de la diagonale » alors qu'aucune diagonale n'etait dessinee -- deux barres
   par tranche ne la montrent pas. C'est donc une vraie courbe de calibration, ou
   l'ecart a la diagonale SE VOIT au lieu de se calculer. */
function renderCalibration(bins) {
  const box = document.getElementById("reliability");
  if (!bins.length) {
    box.innerHTML = '<p class="empty">Pas encore assez de prédictions pour mesurer.</p>';
    return;
  }
  // Le repere est carre : les deux axes portent la meme grandeur, une echelle qui
  // les etirerait differemment rendrait la diagonale mensongere.
  const S = 320, M = 52, MT = 14, BOT = 34, inner = S - M - MT;
  const x = (v) => M + v * inner;
  const y = (v) => MT + (1 - v) * inner;

  const ticks = [0, 0.25, 0.5, 0.75, 1].map((v) => `
    <line x1="${x(v)}" x2="${x(v)}" y1="${y(0)}" y2="${y(1)}" class="grid"/>
    <line x1="${x(0)}" x2="${x(1)}" y1="${y(v)}" y2="${y(v)}" class="grid"/>
    <text x="${x(v)}" y="${y(0) + 16}" class="ax" text-anchor="middle">${v * 100}%</text>
    <text x="${M - 8}" y="${y(v) + 4}" class="ax" text-anchor="end">${v * 100}%</text>`).join("");

  // Le rayon porte l'effectif : une tranche a 12 000 predictions ne pese pas comme
  // une tranche a 119, et une courbe a points egaux le laisserait croire.
  const nMax = Math.max(...bins.map((b) => b.n));
  const r = (n) => 4 + 6 * Math.sqrt(n / nMax);
  const pts = bins.map((b) => {
    const off = Math.abs(b.predicted - b.observed) > 0.15;
    return `<circle cx="${x(b.predicted)}" cy="${y(b.observed)}" r="${r(b.n)}"
              class="dot${off ? " is-off" : ""}"
              data-tip="Annoncé ${pct(b.predicted)} → observé ${pct(b.observed)} · ${b.n} prédictions"/>`;
  }).join("");

  box.innerHTML = `
    <svg viewBox="0 0 ${S} ${S - MT + BOT}" class="chart calib" role="img"
         aria-label="Courbe de calibration : probabilité de Rouge annoncée contre fréquence observée">
      ${ticks}
      <line x1="${x(0)}" y1="${y(0)}" x2="${x(1)}" y2="${y(1)}" class="ideal"/>
      <text x="${x(0.30)}" y="${y(0.54)}" class="ideal-lab">calibration parfaite</text>
      ${pts}
      <text x="${x(0.5)}" y="${y(0) + 34}" class="ax-title" text-anchor="middle">probabilité annoncée</text>
      <text transform="rotate(-90)" x="${-y(0.5)}" y="12" class="ax-title"
            text-anchor="middle">fréquence observée</text>
    </svg>`;
  attachTips(box);
}

/* Une infobulle par marque : un graphique HTML est interactif par nature, et les
   valeurs exactes n'ont pas a encombrer le dessin pour rester accessibles. */
function attachTips(box) {
  let tip = document.getElementById("chart-tip");
  if (!tip) {
    tip = document.createElement("div");
    tip.id = "chart-tip";
    tip.className = "chart-tip";
    tip.hidden = true;
    document.body.appendChild(tip);
  }
  box.querySelectorAll("[data-tip]").forEach((el) => {
    el.addEventListener("mouseenter", (e) => {
      tip.textContent = el.dataset.tip;
      tip.hidden = false;
      const b = e.target.getBoundingClientRect();
      tip.style.left = Math.min(window.innerWidth - 260, b.left + window.scrollX) + "px";
      tip.style.top = (b.top + window.scrollY - 38) + "px";
    });
    el.addEventListener("mouseleave", () => { tip.hidden = true; });
  });
}

loadForecast();
