"""Page HTML du testeur interactif (servi par FastAPI sur GET /ui)."""

UI_HTML = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Gambler — Testeur</title>
<style>
:root {
  --bg:#0f1117; --card:#1a1d27; --border:#2d3148;
  --accent:#6c63ff; --green:#22c55e; --red:#ef4444; --yellow:#f59e0b;
  --text:#e2e8f0; --muted:#64748b;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;min-height:100vh}

header{
  background:var(--card);border-bottom:1px solid var(--border);
  padding:.85rem 1.5rem;display:flex;align-items:center;gap:1rem;flex-wrap:wrap;
}
header h1{font-size:1.15rem;font-weight:800;color:var(--accent);white-space:nowrap}
.controls{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;margin-left:auto}

input[type=date]{
  background:var(--bg);border:1px solid var(--border);color:var(--text);
  padding:.45rem .75rem;border-radius:6px;font-size:.88rem;
}
input[type=date]::-webkit-calendar-picker-indicator{filter:invert(.5)}

button{
  background:var(--accent);color:#fff;border:none;
  padding:.45rem 1.1rem;border-radius:6px;font-size:.88rem;
  cursor:pointer;font-weight:700;transition:opacity .15s;
}
button:hover{opacity:.85}
button:disabled{opacity:.35;cursor:not-allowed}

main{padding:1.25rem 1.5rem}

.grid{display:grid;grid-template-columns:1fr 1fr;gap:1.25rem}
@media(max-width:680px){.grid{grid-template-columns:1fr}}

.card{background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden}
.card-header{
  padding:.6rem 1rem;background:rgba(108,99,255,.08);
  border-bottom:1px solid var(--border);font-weight:700;font-size:.8rem;
  text-transform:uppercase;letter-spacing:.06em;color:var(--accent);
}
.card-body{padding:1rem}

.race-meta{font-size:.78rem;color:var(--muted);margin-bottom:.9rem;line-height:1.7}
.race-meta span{margin-right:.8rem}
.badge{
  display:inline-block;background:rgba(108,99,255,.2);color:var(--accent);
  padding:.15rem .55rem;border-radius:20px;font-size:.72rem;font-weight:700;
}

/* Combo principale */
.prono-main{
  background:rgba(108,99,255,.1);border:1px solid rgba(108,99,255,.3);
  border-radius:8px;padding:.75rem 1rem;margin-bottom:1rem;
}
.prono-main .label{font-size:.72rem;color:var(--muted);margin-bottom:.5rem}
.horse-nums{display:flex;gap:.45rem;flex-wrap:wrap}
.num-badge{
  width:2.1rem;height:2.1rem;border-radius:50%;
  display:flex;align-items:center;justify-content:center;
  font-weight:800;font-size:.9rem;background:var(--accent);color:#fff;
  transition:background .2s;
}
.num-badge.match{background:var(--green)}
.num-badge.miss{background:var(--red)}
.score-fl{float:right;font-size:.72rem;color:var(--accent);font-weight:700}

/* Liste combos */
.combo-list{list-style:none}
.combo-list li{
  display:flex;align-items:center;padding:.3rem 0;
  border-bottom:1px solid rgba(255,255,255,.04);gap:.45rem;font-size:.82rem;
}
.combo-list li:last-child{border:none}
.combo-rank{color:var(--muted);width:1.5rem;font-size:.72rem;flex-shrink:0}
.combo-nums{display:flex;gap:.3rem}
.combo-num{
  background:var(--border);width:1.55rem;height:1.55rem;border-radius:50%;
  display:flex;align-items:center;justify-content:center;
  font-size:.72rem;font-weight:700;
}
.combo-num.match{background:rgba(34,197,94,.25);color:var(--green)}
.combo-score{margin-left:auto;color:var(--muted);font-size:.72rem;flex-shrink:0}
.combo-win{color:var(--green);font-size:.72rem;font-weight:800;margin-left:.4rem}

/* Arrivée */
.arrival-list{list-style:none}
.arrival-list li{
  display:flex;align-items:center;padding:.4rem 0;
  border-bottom:1px solid rgba(255,255,255,.04);gap:.6rem;
}
.arrival-list li:last-child{border:none}
.arrival-pos{color:var(--muted);font-size:.72rem;width:2rem;text-align:right;flex-shrink:0}
.arrival-num{
  width:2.1rem;height:2.1rem;border-radius:50%;
  display:flex;align-items:center;justify-content:center;
  font-weight:800;font-size:.9rem;background:var(--border);flex-shrink:0;
}
.arrival-num.hit{background:rgba(34,197,94,.25);color:var(--green);border:1px solid var(--green)}
.arrival-tag{font-size:.72rem;color:var(--green)}

/* Comparaison */
.full-width{grid-column:1/-1}
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:1rem}
.stat{text-align:center;padding:.75rem .5rem}
.stat-value{font-size:1.8rem;font-weight:900;line-height:1}
.stat-label{font-size:.72rem;color:var(--muted);margin-top:.35rem;line-height:1.4}
.stat-value.good{color:var(--green)}
.stat-value.bad{color:var(--red)}
.stat-value.mid{color:var(--yellow)}
.jackpot{
  text-align:center;padding:1rem;
  color:var(--green);font-weight:800;font-size:1.1rem;
  border-top:1px solid rgba(34,197,94,.2);margin-top:.75rem;
}

/* Candidats */
.cand-table{width:100%;border-collapse:collapse;font-size:.76rem;margin-top:.75rem}
.cand-table th{
  color:var(--muted);text-align:left;padding:.25rem .4rem;
  border-bottom:1px solid var(--border);font-weight:600;
}
.cand-table td{padding:.25rem .4rem;border-bottom:1px solid rgba(255,255,255,.03)}
.cand-table tr:last-child td{border:none}
.bar-wrap{background:rgba(255,255,255,.06);border-radius:3px;height:5px;min-width:60px}
.bar{height:5px;border-radius:3px;background:var(--accent)}

.commentary{font-size:.76rem;color:var(--muted);margin-top:.75rem;line-height:1.5;font-style:italic}
.error-msg{color:var(--red);font-size:.84rem;padding:.5rem 0}
.loading-msg{color:var(--muted);font-size:.84rem;padding:.5rem 0}
.empty{color:var(--muted);font-size:.84rem;text-align:center;padding:2rem 0}

details{margin-top:.75rem}
summary{
  font-size:.76rem;color:var(--muted);cursor:pointer;
  list-style:none;padding:.3rem 0;
}
summary:hover{color:var(--text)}
</style>
</head>
<body>
<header>
  <h1>🎯 Gambler — Testeur</h1>
  <div class="controls">
    <input type="date" id="datePicker">
    <button id="btnRun">Lancer</button>
  </div>
</header>
<main>
  <div id="statusBar"></div>
  <div class="grid" id="grid" style="display:none">

    <!-- Pronostic -->
    <div class="card">
      <div class="card-header">Pronostic généré</div>
      <div class="card-body" id="pronoBody"><div class="empty">—</div></div>
    </div>

    <!-- Arrivée -->
    <div class="card">
      <div class="card-header">Arrivée officielle</div>
      <div class="card-body" id="arrivalBody"><div class="empty">—</div></div>
    </div>

    <!-- Comparaison -->
    <div class="card full-width" id="compareCard" style="display:none">
      <div class="card-header">Analyse du résultat</div>
      <div class="card-body" id="compareBody"></div>
    </div>

  </div>
</main>

<script>
const API_KEY = "%%API_KEY%%";

// Date par défaut = aujourd'hui
const dp = document.getElementById("datePicker");
dp.value = new Date().toISOString().slice(0,10);
dp.max   = new Date().toISOString().slice(0,10);

let pronoData = null;
let arrivalData = null;

document.getElementById("btnRun").addEventListener("click", run);

async function post(path, body) {
  const r = await fetch(path, {
    method:"POST",
    headers:{"Content-Type":"application/json","x-api-key":API_KEY},
    body:JSON.stringify(body),
  });
  if (!r.ok) {
    const e = await r.json().catch(()=>({detail:r.statusText}));
    throw new Error(e.detail || r.statusText);
  }
  return r.json();
}

function status(msg, err=false) {
  document.getElementById("statusBar").innerHTML =
    msg ? `<p class="${err?"error-msg":"loading-msg"}">${msg}</p>` : "";
}

async function run() {
  const date = dp.value;
  if (!date) return;
  const btn = document.getElementById("btnRun");
  btn.disabled = true;
  pronoData = null; arrivalData = null;

  document.getElementById("grid").style.display = "grid";
  document.getElementById("compareCard").style.display = "none";
  document.getElementById("pronoBody").innerHTML  = '<div class="loading-msg">Génération en cours…</div>';
  document.getElementById("arrivalBody").innerHTML = '<div class="loading-msg">Récupération de l\'arrivée…</div>';
  status("Appels API en cours…");

  const [pr, ar] = await Promise.allSettled([
    post("/api/generate",      {date}),
    post("/api/results/fetch", {date}),
  ]);

  if (pr.status === "fulfilled") {
    pronoData = pr.value;
  } else {
    document.getElementById("pronoBody").innerHTML =
      `<div class="error-msg">⚠ ${pr.reason.message}</div>`;
  }

  if (ar.status === "fulfilled") {
    arrivalData = ar.value;
  } else {
    document.getElementById("arrivalBody").innerHTML =
      `<div class="error-msg">⚠ ${ar.reason.message}</div>`;
  }

  if (pronoData) renderProno(pronoData, arrivalData);
  if (arrivalData) renderArrival(arrivalData, pronoData);

  if (pronoData && arrivalData && (arrivalData.arrival_order||[]).length) {
    renderCompare(pronoData, arrivalData);
    document.getElementById("compareCard").style.display = "block";
  }

  status("");
  btn.disabled = false;
}

/* ── Pronostic ──────────────────────────────────────────────────────────── */
function renderProno(data, arrival) {
  const proposals = data.proposals || [];
  if (!proposals.length) {
    document.getElementById("pronoBody").innerHTML = '<div class="empty">Aucun pronostic généré.</div>';
    return;
  }
  const race   = data.race || {};
  const main   = proposals[0];
  const size   = main.nums.length;
  const arrTop = new Set((arrival?.arrival_order||[]).slice(0, size));
  const arrAll = new Set(arrival?.arrival_order||[]);

  let h = `<div class="race-meta">
    <span>📍 ${esc(race.hippodrome||"—")}</span>
    <span>🏇 ${esc(race.race_name||"—")}</span>
    <span>📏 ${race.distance ? race.distance+"m" : "—"}</span>
    <span>🕐 ${esc(race.start_time||"—")}</span>
    <span class="badge">${esc(race.type||"—")}</span>
  </div>`;

  // Combo principale
  h += `<div class="prono-main">
    <div class="label">
      COMBINAISON N°1 — <strong>${esc(main.title)}</strong>
      <span class="score-fl">${main.score}%</span>
    </div>
    <div class="horse-nums" style="margin-top:.5rem">`;
  for (const n of main.nums) {
    const cls = arrAll.size===0 ? "" : arrTop.has(n) ? "match" : "miss";
    h += `<div class="num-badge ${cls}">${n}</div>`;
  }
  h += `</div></div>`;

  // Top 10 combos
  h += `<div style="font-size:.72rem;color:var(--muted);margin-bottom:.4rem">Top 10 combinaisons</div>
  <ul class="combo-list">`;
  for (let i=0; i<Math.min(10, proposals.length); i++) {
    const p = proposals[i];
    const win = arrTop.size===size && p.nums.every(n=>arrTop.has(n));
    h += `<li>
      <span class="combo-rank">#${i+1}</span>
      <span class="combo-nums">`;
    for (const n of p.nums) {
      h += `<span class="combo-num ${arrTop.has(n)?"match":""}">${n}</span>`;
    }
    h += `</span>
      <span class="combo-score">${p.score}%</span>
      ${win?'<span class="combo-win">✓ EXACT</span>':""}
    </li>`;
  }
  h += `</ul>`;

  // Détail candidats (collapsible)
  const sel = main.selection;
  if (sel?.candidates?.length) {
    h += `<details><summary>Détail des candidats (${sel.candidates.length})</summary>
    <table class="cand-table"><thead><tr>
      <th>N°</th><th>Nom</th><th>λ</th><th>Cotes</th><th>Perf</th><th>Presse</th><th>LLM</th>
    </tr></thead><tbody>`;
    const maxL = Math.max(...sel.candidates.map(c=>c.lambda));
    for (const c of sel.candidates) {
      const pct = maxL>0 ? Math.round(c.lambda/maxL*100) : 0;
      h += `<tr>
        <td><strong>${c.num}</strong></td>
        <td>${esc(c.nom)}</td>
        <td>
          <div class="bar-wrap"><div class="bar" style="width:${pct}%"></div></div>
        </td>
        <td>${fmt(c.sig_cotes)}</td>
        <td>${fmt(c.sig_perf)}</td>
        <td>${fmt(c.sig_presse)}</td>
        <td>${fmt(c.sig_llm)}</td>
      </tr>`;
    }
    h += `</tbody></table></details>`;
  }

  if (data.commentary) {
    h += `<div class="commentary">${esc(data.commentary)}</div>`;
  }

  document.getElementById("pronoBody").innerHTML = h;
}

/* ── Arrivée ────────────────────────────────────────────────────────────── */
function renderArrival(data, prono) {
  const arr = data.arrival_order || [];
  if (!arr.length) {
    document.getElementById("arrivalBody").innerHTML =
      '<div class="empty">Arrivée non disponible pour cette date.</div>';
    return;
  }
  const pronoNums = new Set(prono?.proposals?.[0]?.nums || []);
  const size = prono?.proposals?.[0]?.nums?.length || 5;

  let h = `<div class="race-meta">
    <span>📅 ${esc(data.date||"—")}</span>
    <span>🏁 ${esc(data.race_type||"—")}</span>
    <span style="color:var(--muted);font-size:.72rem">Source : ${esc(data.source||"—")}</span>
  </div><ul class="arrival-list">`;

  for (let i=0; i<arr.length; i++) {
    const n   = arr[i];
    const hit = pronoNums.has(n) && i < size;
    h += `<li>
      <span class="arrival-pos">${i+1}${i===0?"er":"e"}</span>
      <span class="arrival-num ${hit?"hit":""}">${n}</span>
      ${hit ? '<span class="arrival-tag">✓ pronostic</span>' : ""}
    </li>`;
  }
  h += `</ul>`;

  document.getElementById("arrivalBody").innerHTML = h;
}

/* ── Comparaison ────────────────────────────────────────────────────────── */
function renderCompare(prono, arrival) {
  const main    = prono.proposals?.[0] || {};
  const arr     = arrival.arrival_order || [];
  const size    = main.nums?.length || 0;
  const topArr  = arr.slice(0, size);

  const hits    = (main.nums||[]).filter(n=>topArr.includes(n)).length;
  const pct     = size>0 ? Math.round(hits/size*100) : 0;
  const cls     = pct===100?"good":pct>=60?"mid":"bad";

  // Cherche si UNE des 20 combos est exacte
  const winner  = (prono.proposals||[]).find(p=>
    p.nums.length===size &&
    p.nums.every(n=>topArr.includes(n)) &&
    topArr.every(n=>p.nums.includes(n))
  );

  let h = `<div class="stat-grid">
    <div class="stat">
      <div class="stat-value ${cls}">${hits}/${size}</div>
      <div class="stat-label">Chevaux corrects<br>(combo n°1 vs top ${size})</div>
    </div>
    <div class="stat">
      <div class="stat-value ${cls}">${pct}%</div>
      <div class="stat-label">Taux de réussite</div>
    </div>
    <div class="stat">
      <div class="stat-value ${winner?"good":"bad"}">${winner?"✓":"✗"}</div>
      <div class="stat-label">${winner
        ? "Combo exacte : " + esc(winner.title)
        : "Aucune combo exacte dans le top 20"}</div>
    </div>
    <div class="stat">
      <div class="stat-value">${prono.proposals?.length||0}</div>
      <div class="stat-label">Combos évaluées</div>
    </div>
  </div>`;

  if (pct === 100) {
    h += `<div class="jackpot">🎉 COMBINAISON N°1 PARFAITE !</div>`;
  } else if (winner) {
    h += `<div class="jackpot" style="color:var(--yellow)">
      🏆 Combinaison gagnante trouvée en position #${(prono.proposals||[]).indexOf(winner)+1}
    </div>`;
  }

  document.getElementById("compareBody").innerHTML = h;
}

/* ── Helpers ────────────────────────────────────────────────────────────── */
function esc(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}
function fmt(v) {
  if (v===undefined||v===null) return "—";
  return typeof v==="number" ? v.toFixed(3) : esc(String(v));
}
</script>
</body>
</html>"""
