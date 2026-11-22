// Jackathon web UI: one input bar in, a live view of the whole build out.
// Plain JavaScript, no build step. The server streams every pipeline event
// over Server-Sent Events; this file turns them into the timeline, the
// activity feed, your decision cards and the result tiles.

const app = document.getElementById("app");

const STAGES = [
  ["intake", "Read the flyer"],
  ["research", "Research"],
  ["strategy", "Rank ideas"],
  ["choose", "Pick an idea", true],
  ["plan", "Plan the build", true],
  ["build", "Build"],
  ["test", "Test in a browser"],
  ["review", "Review the app", true],
  ["publish", "GitHub"],
  ["deploy", "Deploy"],
  ["docs", "Deck & document"],
  ["done", "Done"],
];
const AGENT_COLORS = {
  Intake: "#5eead4", Research: "#67e8f9", Strategist: "#fbbf24", Architect: "#c4b5fd",
  Backend: "#93c5fd", Frontend: "#f9a8d4", Storyteller: "#86efac",
};

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const $ = (sel, root = document) => root.querySelector(sel);

async function api(path, options = {}) {
  const res = await fetch(path, options);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `Request failed (${res.status})`);
  return body;
}

// ---------------------------------------------------------------- routing

let stream = null;
window.addEventListener("hashchange", route);

function route() {
  if (stream) { stream.close(); stream = null; }
  const match = location.hash.match(/^#\/run\/([a-z0-9-]+)$/);
  match ? showRun(match[1]) : showHome();
}

// ---------------------------------------------------------------- home

function showHome() {
  app.innerHTML = `
    <section class="hero">
      <span class="eyebrow">Your AI hackathon team</span>
      <h1>From hackathon name to<br><span>a shipped, pitched app.</span></h1>
      <p>Research, the winning idea, a working app, a live link, a GitHub repo, a pitch deck and a write-up. You make the calls; the agents do the work.</p>
    </section>
    <form class="bar" id="bar">
      <input type="text" id="text" placeholder="Type a hackathon name, or drop its flyer or PDF here" autocomplete="off" />
      <span id="file-chip"></span>
      <input type="file" id="file" accept=".png,.jpg,.jpeg,.webp,.pdf" hidden />
      <button type="button" class="icon-btn" id="attach" title="Attach a flyer or PDF">${ICON.clip}</button>
      <button type="submit" class="go" id="go" title="Start">${ICON.arrow}</button>
    </form>
    <div class="hint">PNG, JPG or PDF · or paste a flyer image</div>
    <div class="form-error" id="err"></div>
    <section class="recent" id="recent"></section>`;

  let file = null;
  const bar = $("#bar"), input = $("#text"), picker = $("#file");
  const setFile = (f) => {
    file = f;
    $("#file-chip").innerHTML = f ? `<span class="chip-file"><span>${esc(f.name)}</span><button type="button" title="Remove">✕</button></span>` : "";
    if (f) $("#file-chip button").onclick = () => setFile(null);
  };
  $("#attach").onclick = () => picker.click();
  picker.onchange = () => setFile(picker.files[0] || null);
  ["dragover", "dragenter"].forEach((t) => bar.addEventListener(t, (e) => { e.preventDefault(); bar.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((t) => bar.addEventListener(t, () => bar.classList.remove("drag")));
  bar.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]); });
  input.addEventListener("paste", (e) => {
    const img = [...e.clipboardData.files].find((f) => f.type.startsWith("image/"));
    if (img) { e.preventDefault(); setFile(img); }
  });
  input.focus();

  bar.onsubmit = async (e) => {
    e.preventDefault();
    if (!input.value.trim() && !file) { $("#err").textContent = "Type a hackathon name, or attach its flyer or PDF."; return; }
    const form = new FormData();
    form.append("text", input.value);
    if (file) form.append("file", file);
    $("#go").disabled = true;
    $("#err").textContent = "";
    try {
      const { run_id } = await api("/api/runs", { method: "POST", body: form });
      location.hash = `#/run/${run_id}`;
    } catch (err) {
      $("#err").textContent = err.message;
      $("#go").disabled = false;
    }
  };

  api("/api/runs").then((runs) => {
    if (!runs.length) return;
    $("#recent").innerHTML = `<h3>Recent runs</h3>` + runs.map((r) => `
      <a href="#/run/${esc(r.run_id)}"><span>${esc(r.product || r.hackathon)} <small>· ${esc(r.hackathon)}</small></span>
      <small>${esc(new Date(r.created_at).toLocaleString())}</small></a>`).join("");
  }).catch(() => {});
}

// ---------------------------------------------------------------- run view

async function showRun(runId) {
  app.innerHTML = `<div class="run-head"><div><div class="sub">Loading…</div></div></div>`;
  let data;
  try { data = await api(`/api/runs/${runId}`); } catch (err) {
    app.innerHTML = `<div class="card banner-error">${esc(err.message)} <a href="#/">Go home</a></div>`;
    return;
  }
  const view = { runId, state: data.state, status: data.status, question: data.question, stage: null, done: new Set() };

  app.innerHTML = `
    <div class="run-head">
      <div><div class="sub" id="sub"></div><h1 id="title"></h1></div>
      <div style="display:flex;gap:10px;align-items:center"><span id="resume"></span><span class="status" id="status"></span></div>
    </div>
    <div class="layout">
      <aside class="card timeline"><ol id="timeline"></ol></aside>
      <section>
        <div id="question"></div>
        <div id="outputs"></div>
        <div class="card"><h2>Live activity</h2><p class="lead small">Every search, file and check, as it happens.</p><div class="feed" id="feed"></div></div>
      </section>
    </div>`;
  renderHeader(view);
  renderTimeline(view);
  renderOutputs(view);
  if (view.question) renderQuestion(view);

  stream = new EventSource(`/api/runs/${runId}/events`);
  stream.onmessage = (msg) => handleEvent(view, JSON.parse(msg.data));
}

async function refresh(view) {
  const data = await api(`/api/runs/${view.runId}`);
  Object.assign(view, { state: data.state, status: data.status }); // questions come only from events
  renderHeader(view);
  renderOutputs(view);
}

function handleEvent(view, e) {
  const feed = $("#feed");
  if (!feed) return;
  switch (e.kind) {
    case "stage_started":
      if (view.stage && view.stage !== e.data.stage) view.done.add(view.stage);
      view.stage = e.data.stage;
      renderTimeline(view);
      addStage(feed, e.message);
      break;
    case "question":
      view.question = e.data;
      // Replayed history holds old, already-answered questions: only render if still open after the refresh.
      refresh(view).then(() => { if (view.question === e.data) renderQuestion(view); });
      break;
    case "answered":
      $("#question").innerHTML = "";
      view.question = null;
      view.status = "running";
      renderHeader(view);
      break;
    case "output":
      addRow(feed, "", e.message + (e.data.url ? ` ${e.data.url}` : ""), "good");
      refresh(view);
      break;
    case "finished":
      view.done.add(view.stage);
      view.stage = "done";
      view.done.add("done");
      renderTimeline(view);
      refresh(view);
      break;
    case "failed":
      addRow(feed, "", e.message, "err");
      refresh(view);
      break;
    case "idle":
      break;
    default:
      addEventRow(feed, e);
  }
}

function addEventRow(feed, e) {
  const who = e.agent || "";
  const d = e.data || {};
  switch (e.kind) {
    case "agent_started": return addRow(feed, who, `started · ${d.model || ""}`);
    case "agent_thought": return addRow(feed, who, e.message.slice(0, 280), "thought");
    case "tool_call": return addRow(feed, who, `→ ${e.message}`);
    case "tool_result":
      if (d.error) return addRow(feed, who, `✗ ${e.message}`, "err");
      if ((d.tool || "").startsWith("run_")) return addRow(feed, who, e.message, e.message.startsWith("PASSED") ? "good" : "err");
      return;
    case "agent_finished": return addRow(feed, who, `✓ ${e.message}`, "good");
    case "build_verified": return addRow(feed, who, d.ok ? "✓ verified: we re-ran the checks ourselves" : "✗ our own re-check failed", d.ok ? "good" : "err");
    case "smoke_result":
      addRow(feed, "Browser", e.message, d.ok ? "good" : "err");
      (d.pages || []).forEach((p) => addRow(feed, "", `${p.ok ? "✓" : "✗"} ${p.route} ${p.errors.join("; ").slice(0, 200)}`, p.ok ? "" : "err"));
      return;
    case "notice": return addRow(feed, "", e.message, "note");
    case "error": return addRow(feed, "", e.message, "err");
  }
}

function addRow(feed, who, text, cls = "") {
  const stick = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 60;
  const row = document.createElement("div");
  row.className = "row";
  row.innerHTML = `<div class="who" style="color:${AGENT_COLORS[who] || "var(--faint)"}">${esc(who)}</div><div class="what ${cls}">${esc(text)}</div>`;
  feed.appendChild(row);
  while (feed.children.length > 600) feed.removeChild(feed.firstChild);
  if (stick) feed.scrollTop = feed.scrollHeight;
}

function addStage(feed, text) {
  const el = document.createElement("div");
  el.className = "stage";
  el.textContent = text;
  feed.appendChild(el);
  feed.scrollTop = feed.scrollHeight;
}

// ---------------------------------------------------------------- header, timeline, outputs

function renderHeader(view) {
  const s = view.state;
  $("#title").textContent = s.plan ? s.plan.product_name : s.hackathon;
  $("#sub").textContent = s.plan ? `${s.hackathon}${s.chosen_index != null ? " · " + s.strategy.options[s.chosen_index].track : ""}` : "Hackathon";
  const labels = { running: "Working", waiting: "Waiting for you", done: "Done", error: "Stopped by an error", stopped: "Paused" };
  const el = $("#status");
  el.className = `status ${view.status}`;
  el.textContent = labels[view.status] || view.status;
  const canResume = view.status === "error" || view.status === "stopped";
  $("#resume").innerHTML = canResume ? `<button class="btn" id="resume-btn">Resume</button>` : "";
  if (canResume) $("#resume-btn").onclick = async () => {
    await api(`/api/runs/${view.runId}/resume`, { method: "POST" });
    route();
  };
}

function renderTimeline(view) {
  const hasFile = !!view.state.source?.file_name;
  $("#timeline").innerHTML = STAGES.filter(([key]) => key !== "intake" || hasFile).map(([key, label, you]) => {
    const state = view.stage === key && key !== "done" ? "active" : view.done.has(key) ? "done" : "";
    return `<li class="${state} ${you ? "you" : ""}"><span class="dot">${state === "done" ? "✓" : ""}</span>${esc(label)}${you ? '<span class="tag">YOU</span>' : ""}</li>`;
  }).join("");
}

function renderOutputs(view) {
  const o = view.state.outputs;
  const started = o.repo_url || o.deploy_url || o.deck || o.doc || view.status === "done" || ["publish", "deploy", "docs", "done"].includes(view.stage);
  if (!started) { $("#outputs").innerHTML = ""; return; }
  const file = (p) => `/api/runs/${view.runId}/file/${p}`;
  const deployNote = o.deploy_status === "running" ? "Live" : o.deploy_status === "building" ? "Building on Hugging Face…" : o.deploy_status;
  const tiles = [
    ["Live app", o.deploy_url, o.deploy_url, deployNote],
    ["GitHub repo", o.repo_url, o.repo_url, o.repo_url ? "Public repo" : "Not created yet"],
    ["Pitch deck", o.deck && file(o.deck), o.deck && "pitch-deck.pptx", o.deck ? "PowerPoint · with speaker notes" : "Being written…"],
    ["App document", o.doc && file(o.doc), o.doc && "app-document.pdf", o.doc ? "PDF · every screen and endpoint" : "Being written…"],
  ];
  const done = view.status === "done";
  $("#outputs").innerHTML = `
    <div class="card ${done ? "done-banner" : ""}">
      ${done ? `<span class="eyebrow">Done</span><h2>Your hackathon kit is ready</h2><p class="lead">Everything below is yours to submit and present.</p>` : `<h2>Your hackathon kit</h2><p class="lead">Links appear here as each piece is ready.</p>`}
      <div class="tiles">${tiles.map(([label, href, value, note]) => href
        ? `<a class="tile ready" href="${esc(href)}" target="_blank" rel="noopener"><span class="label">${label}</span><span class="value">${esc(value)}</span><span class="small muted">${esc(note)}</span></a>`
        : `<div class="tile pending"><span class="label">${label}</span><span class="value">${esc(note || "Pending")}</span></div>`).join("")}
      </div>
    </div>`;
}

// ---------------------------------------------------------------- your decisions

function renderQuestion(view) {
  const q = view.question;
  if (!q) return;
  const el = $("#question");
  if (q.kind === "choose") el.innerHTML = chooseCard(view);
  if (q.kind === "plan") el.innerHTML = planCard(view.state.plan);
  if (q.kind === "review") el.innerHTML = reviewCard(view, q.preview_url);
  renderHeader(view);
  el.scrollIntoView({ behavior: "smooth", block: "start" });

  const send = async (body, button) => {
    el.querySelectorAll("button").forEach((b) => (b.disabled = true));
    if (button) button.innerHTML = `<span class="spinner"></span> Sending`;
    try { await api(`/api/runs/${view.runId}/answer`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); }
    catch (err) { alert(err.message); el.querySelectorAll("button").forEach((b) => (b.disabled = false)); }
  };
  el.querySelectorAll("[data-pick]").forEach((b) => (b.onclick = () => send({ index: Number(b.dataset.pick) }, b)));
  const approve = $("#approve", el), change = $("#change", el), text = $("#feedback", el);
  if (approve) approve.onclick = () => send({ feedback: null }, approve);
  if (change) change.onclick = () => {
    if (!text.value.trim()) { text.focus(); text.placeholder = "Describe what you'd like changed first."; return; }
    send({ feedback: text.value.trim() }, change);
  };
}

function chooseCard(view) {
  const { research, strategy } = view.state;
  const facts = research ? `
    <p class="muted">${esc(research.summary)}</p>
    <div class="pills" style="margin-bottom:6px">${research.tracks.map((t) => `<span class="pill accent">${esc(t.name)}</span>`).join("")}</div>
    ${research.judging_criteria.length ? `<p class="small muted"><b>Judged on:</b> ${esc(research.judging_criteria.join(" · "))}</p>` : ""}` : "";
  return `
    <div class="card ask">
      <span class="eyebrow">Your call</span>
      <h2>Which idea should we build?</h2>
      ${facts}
      <div class="section-title">Ranked by how likely they are to win</div>
      <div class="options">${strategy.options.map((o, i) => `
        <div class="option">
          <div class="option-top">
            <div><h3>${esc(o.title)}</h3><div class="pills" style="margin-top:6px"><span class="pill accent">${esc(o.track)}</span>${i === 0 ? '<span class="pill">Top pick</span>' : ""}</div></div>
            <div class="score"><b>${o.scores.total.toFixed(1)}</b><small>out of 10</small></div>
          </div>
          <div class="muted">${esc(o.solution)}</div>
          <div class="small"><b>Wow moment:</b> <span class="muted">${esc(o.wow_moment)}</span></div>
          <div class="pills">${[["Judge fit", o.scores.judge_fit], ["Impact", o.scores.impact], ["Feasible", o.scores.feasibility], ["Novel", o.scores.novelty], ["Demo", o.scores.demo_wow]].map(([k, v]) => `<span class="pill">${k} ${v}</span>`).join("")}</div>
          <div><button class="btn ${i === 0 ? "primary" : ""}" data-pick="${i}">Build this</button></div>
        </div>`).join("")}
      </div>
      <p class="small muted" style="margin-top:14px">${esc(strategy.recommendation)}</p>
    </div>`;
}

function planCard(plan) {
  return `
    <div class="card ask">
      <span class="eyebrow">Your call</span>
      <h2>${esc(plan.product_name)}: here's the plan</h2>
      <p class="lead">${esc(plan.one_liner)}</p>
      <p class="muted">${esc(plan.architecture)}</p>
      <div class="section-title">Screens</div>
      <div class="pills">${plan.screens.map((s) => `<span class="pill accent">${esc(s.name)} · ${esc(s.route)}</span>`).join("")}</div>
      <div class="section-title">API contract</div>
      <table><tr><th>Method</th><th>Path</th><th>What it does</th></tr>
        ${plan.endpoints.map((e) => `<tr><td><span class="method ${e.method}">${e.method}</span></td><td><code>${esc(e.path)}</code></td><td class="muted">${esc(e.summary)}</td></tr>`).join("")}
      </table>
      <div class="section-title">Demo script</div>
      <ol class="steps">${plan.demo_script.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>
      ${plan.cut_scope.length ? `<p class="small muted"><b>Left out to fit the time:</b> ${esc(plan.cut_scope.join("; "))}</p>` : ""}
      <div class="section-title">Want anything changed?</div>
      <textarea id="feedback" placeholder="e.g. Add a map view of the beds, and drop the analytics page"></textarea>
      <div class="actions"><button class="btn primary" id="approve">Approve and build</button><button class="btn" id="change">Request changes</button></div>
    </div>`;
}

function reviewCard(view, previewUrl) {
  const { smoke, builds } = view.state;
  const shot = (p) => `/api/runs/${view.runId}/file/${p.screenshot}`;
  const pages = smoke ? smoke.pages.map((p) => `
    <a class="shot" href="${esc(p.screenshot ? shot(p) : "#")}" target="_blank" rel="noopener">
      ${p.screenshot ? `<img src="${esc(shot(p))}" alt="${esc(p.route)}">` : ""}
      <div><span>${esc(p.route)}</span><span class="${p.ok ? "ok" : "bad"}">${p.ok ? "✓ works" : "✗ errors"}</span></div>
    </a>`).join("") : "";
  const verdict = !smoke ? "" : smoke.ok
    ? `<p class="ok">✓ We opened every screen in a real browser. No errors.</p>`
    : `<p class="bad">✗ Some problems remain:</p><pre class="small muted" style="white-space:pre-wrap">${esc((smoke.problem || "") + smoke.pages.filter((p) => !p.ok).map((p) => `${p.route}: ${p.errors.join("; ")}`).join("\n"))}</pre>`;
  return `
    <div class="card ask">
      <span class="eyebrow">Your call</span>
      <h2>The app is built. Take it for a spin.</h2>
      <p class="lead">Try it yourself, then ship it, or tell the engineers what to change.</p>
      ${previewUrl ? `<div class="actions" style="margin:0 0 16px"><a class="btn primary" href="${esc(previewUrl)}" target="_blank" rel="noopener">Open the app ${ICON.ext}</a></div>` : ""}
      ${verdict}
      <div class="shots">${pages}</div>
      <div class="section-title">What the engineers built</div>
      ${Object.entries(builds).map(([area, r]) => `<p class="small"><b style="text-transform:capitalize">${esc(area)}</b> <span class="${r.verified ? "ok" : "bad"}">${r.verified ? "✓ checks pass" : "✗ checks fail"}</span><br><span class="muted">${esc(r.report.summary)}</span></p>`).join("")}
      <div class="section-title">Anything specific you want changed?</div>
      <textarea id="feedback" placeholder="e.g. Make the alert cards bigger and add a dark/light toggle"></textarea>
      <div class="actions"><button class="btn primary" id="approve">Ship it: GitHub, deploy, deck & PDF</button><button class="btn" id="change">Request changes</button></div>
    </div>`;
}

// ---------------------------------------------------------------- icons

const ICON = {
  clip: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.4 11.1l-9.2 9.2a6 6 0 01-8.5-8.5l9.2-9.2a4 4 0 015.7 5.7l-9.2 9.2a2 2 0 01-2.8-2.8l8.5-8.5"/></svg>`,
  arrow: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>`,
  ext: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M7 17L17 7M8 7h9v9"/></svg>`,
};

route(); // start (last, so everything above is defined)
