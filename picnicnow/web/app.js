/* PicNicNow – webapp zonder build-stap. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const euro = (c) => (c == null ? "" : "€" + (c / 100).toFixed(2).replace(".", ","));
const DAYS = ["ma", "di", "wo", "do", "vr", "za", "zo"];
const DAY_NAMES = { ma: "Maandag", di: "Dinsdag", wo: "Woensdag", do: "Donderdag", vr: "Vrijdag", za: "Zaterdag", zo: "Zondag" };
const LEVELS = { 1: "Vertrouwd", 2: "Bekend", 3: "Kant-en-klaar", 4: "Nieuw & haalbaar", 5: "Nieuw & uitdagend" };
const MODES = ["auto", "kiezen", "zelf"];
const MODE_LABEL = { auto: "auto", kiezen: "zelf kiezen", zelf: "zelf halen" };

const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* privé-modus */ } },
};

let S = null;          // laatste state van de server
let me = store.get("me", null);
let ws = null;

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------
async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json" };
  const pin = store.get("pin", "");
  if (pin) headers["x-pin"] = pin;
  const res = await fetch(path, { ...opts, headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
  if (res.status === 401) {
    const p = prompt("Pincode:");
    if (p) { store.set("pin", p); return api(path, opts); }
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.detail || res.statusText);
    err.data = data;
    throw err;
  }
  return data;
}

function toast(msg, ms = 2600) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), ms);
}

async function guarded(fn, okMsg) {
  try {
    const r = await fn();
    if (okMsg) toast(typeof okMsg === "function" ? okMsg(r) : okMsg);
    return r;
  } catch (e) {
    toast("⚠︎ " + e.message, 4000);
    if (e.data && e.data.twofa) openPicnicLogin();
  }
}

// ---------------------------------------------------------------------------
// Tabs, week, wie
// ---------------------------------------------------------------------------
function showTab(name) {
  $$(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + name));
  store.set("tab", name);
  if (name === "baby") loadBaby();
  if (name === "deals") loadDeals();
  if (name === "menu") loadKnown();
}

function shiftWeek(week, delta) {
  const [y, w] = week.split("-W").map(Number);
  // maandag van ISO-week
  const jan4 = new Date(Date.UTC(y, 0, 4));
  const monday = new Date(jan4);
  monday.setUTCDate(jan4.getUTCDate() - ((jan4.getUTCDay() + 6) % 7) + (w - 1) * 7 + delta * 7);
  const d = new Date(Date.UTC(monday.getUTCFullYear(), monday.getUTCMonth(), monday.getUTCDate() + 3));
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const wk = Math.ceil(((d - yearStart) / 86400000 + 1) / 7);
  return `${d.getUTCFullYear()}-W${String(wk).padStart(2, "0")}`;
}

function weekLabel(week) {
  const [y, w] = week.split("-W").map(Number);
  const jan4 = new Date(Date.UTC(y, 0, 4));
  const monday = new Date(jan4);
  monday.setUTCDate(jan4.getUTCDate() - ((jan4.getUTCDay() + 6) % 7) + (w - 1) * 7);
  const sunday = new Date(monday);
  sunday.setUTCDate(monday.getUTCDate() + 6);
  return `wk ${w} · ${monday.getUTCDate()}–${sunday.getUTCDate()}/${sunday.getUTCMonth() + 1}`;
}

function personClass(name) {
  const names = (S && S.profile.names) || [];
  const i = names.indexOf(name);
  return i === 1 ? "p2" : "p1";
}

function chooseWho(force = false) {
  const names = (S && S.profile.names) || ["Ik", "Partner"];
  if (me && names.includes(me) && !force) return updateSpeaker();
  const box = $("#whoChoices");
  box.innerHTML = "";
  for (const n of [...names, "Wij samen"]) {
    const b = document.createElement("button");
    b.textContent = n;
    b.onclick = () => { me = n; store.set("me", n); $("#whoDialog").close(); updateSpeaker(); connectWS(true); };
    box.appendChild(b);
  }
  $("#whoDialog").showModal();
}

function updateSpeaker() {
  const b = $("#speakerBtn");
  b.textContent = (me || "?").slice(0, 1).toUpperCase();
  b.className = "speaker " + personClass(me);
  b.title = `Je praat als ${me}. Tik om te wisselen.`;
}

// ---------------------------------------------------------------------------
// Live verbinding
// ---------------------------------------------------------------------------
function connectWS(reconnect = false) {
  if (ws && !reconnect) return;
  if (ws) try { ws.close(); } catch { /* */ }
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const pin = store.get("pin", "");
  ws = new WebSocket(`${proto}://${location.host}/ws?name=${encodeURIComponent(me || "Iemand")}${pin ? "&pin=" + encodeURIComponent(pin) : ""}`);
  ws.onmessage = (ev) => {
    const e = JSON.parse(ev.data);
    if (e.type === "state") refresh();
    if (e.type === "presence") renderOnline(e.online);
    if (e.type === "message") addMessage(e);
    if (e.type === "typing") $("#typing").hidden = !e.on;
    if (e.type === "chat_reset") { $("#messages").innerHTML = ""; renderEmptyChat(); }
    if (e.type === "transcript" && e.speaker !== me) showTranscript(e.speaker, e.text);
  };
  ws.onclose = () => { ws = null; setTimeout(() => connectWS(), 2500); };
}

function renderOnline(names) {
  $("#online").innerHTML = names.map((n) => `<i title="${esc(n)}" style="background:var(--${personClass(n) === "p2" ? "p2" : "p1"})">${esc(n.slice(0, 1))}</i>`).join("");
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
async function refresh() {
  S = await api("/api/state");
  $("#weekLabel").textContent = weekLabel(S.week);
  $("#demoBadge").hidden = !S.demo;
  const a = S.assistant;
  $("#assistantOff").hidden = a.mode === "claude";
  $("#assistantOff").innerHTML = `<b>Gratis commandomodus</b> (${esc(a.reason)}): korte opdrachten werken zonder AI, bv.
    <i>“zet melk en 2 komkommers op de lijst”</i>, <i>“woensdag shakshuka”</i>, <i>“iets nieuws?”</i>. Typ <i>help</i> voor meer.`;
  if (renderQuick.mode !== a.mode) renderQuick(a.mode);
  renderMenu();
  renderList();
  $("#listCount").textContent = S.summary.open || "";
}

// ---------------------------------------------------------------------------
// Gesprek
// ---------------------------------------------------------------------------
const QUICK = [
  ["🗓 Plan de week", "Laten we het weekmenu maken. Stel 5 avondmaaltijden voor: mix van vertrouwd, één kant-en-klaar en één nieuwe, en zorg dat restjes opgaan."],
  ["😌 Iets vertrouwds", "Wat kunnen we makkelijk maken dat we goed kennen? (niveau 1–2)"],
  ["📦 Kant-en-klaar", "Welke kant-en-klare opties zijn er deze week? (niveau 3)"],
  ["✨ Iets nieuws", "Verras ons met iets nieuws dat doordeweeks haalbaar is (niveau 4)."],
  ["👩‍🍳 Uitdaging", "We hebben zin in een uitdaging voor het weekend (niveau 5)."],
  ["🍼 Baby mee-eten", "Wat kan de baby deze week mee-eten, en wat zijn goede baby-lunches?"],
  ["🔁 Vaste boodschappen", "Welke vaste boodschappen moeten er weer bij? Zet ze op de lijst."],
  ["💶 Aanbiedingen", "Zijn er dure dingen die we nu goedkoper elders kunnen halen?"],
];

const QUICK_CMD = [
  ["✨ Suggesties", "suggesties"],
  ["😌 Iets vertrouwds", "iets vertrouwds?"],
  ["📦 Kant-en-klaar", "kant-en-klaar ideeën"],
  ["🆕 Iets nieuws", "iets nieuws?"],
  ["👩‍🍳 Uitdaging", "iets uitdagends voor het weekend?"],
  ["🍼 Baby-ideeën", "wat kan de baby eten"],
  ["🔁 Vaste boodschappen", "vaste boodschappen"],
  ["🧩 Combineren", "combineertips"],
  ["💶 Aanbiedingen", "aanbiedingen"],
];

function renderQuick(mode = "claude") {
  renderQuick.mode = mode;
  $("#quickPrompts").innerHTML = "";
  for (const [label, text] of mode === "claude" ? QUICK : QUICK_CMD) {
    const b = document.createElement("button");
    b.textContent = label;
    b.onclick = () => sendChat(text);
    $("#quickPrompts").appendChild(b);
  }
}

function renderEmptyChat() {
  if ($("#messages").children.length) return;
  $("#messages").innerHTML = `<div class="empty">Praat samen over de week – typ, of houd 🎙 ingedrukt.<br>
    Zet <b>Luistermodus</b> aan om één telefoon op tafel mee te laten luisteren.</div>`;
}

function addMessage(m) {
  const box = $("#messages");
  $(".empty", box)?.remove();
  const div = document.createElement("div");
  const isUser = m.role === "user";
  div.className = `msg ${isUser ? "user " + personClass(m.speaker) : "assistant"}`;
  div.innerHTML = `<span class="who">${esc(isUser ? m.speaker || "Wij" : "Assistent")}</span>${esc(m.display)}`;
  box.appendChild(div);
  div.scrollIntoView({ behavior: "smooth", block: "end" });
  if (!isUser && $("#speakReplies").checked) speak(m.display);
}

async function loadChat() {
  const data = await api("/api/chat");
  $("#messages").innerHTML = "";
  data.messages.forEach((m) => addMessage({ ...m, display: m.display }));
  renderEmptyChat();
}

async function sendChat(text, respond = true) {
  text = (text || "").trim();
  if (!text && respond === false) return;
  showTab("chat");
  await guarded(() => api("/api/chat", { method: "POST", body: { speaker: me || "Wij", text: text || null, respond } }));
  if (!ws || ws.readyState !== 1) await guarded(loadChat);  // geen live verbinding: zelf verversen
}

// --- spraak ---------------------------------------------------------------
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null, listening = false, pttActive = false, buffer = [], silenceTimer = null;
const WAKE = /\b(assistent|picnic|hé? hulp)\b/i;

function speak(text) {
  if (!("speechSynthesis" in window)) return;
  const u = new SpeechSynthesisUtterance(text.replace(/[*_#]/g, ""));
  u.lang = "nl-NL";
  speechSynthesis.cancel();
  speechSynthesis.speak(u);
}

function makeRecognizer(continuous) {
  const r = new SR();
  r.lang = "nl-NL";
  r.interimResults = true;
  r.continuous = continuous;
  return r;
}

function showTranscript(who, text) {
  const t = $("#transcript");
  t.hidden = !text;
  t.textContent = text ? `${who || ""} 🎙 ${text}` : "";
}

function startPTT() {
  if (!SR) return toast("Spraakherkenning wordt niet ondersteund in deze browser (probeer Chrome of Safari).");
  if (listening) return;
  pttActive = true;
  rec = makeRecognizer(false);
  let finalText = "";
  rec.onresult = (e) => {
    let interim = "";
    for (const r of e.results) (r.isFinal ? (finalText = r[0].transcript) : (interim += r[0].transcript));
    showTranscript(me, finalText || interim);
    ws?.readyState === 1 && ws.send(JSON.stringify({ type: "transcript", speaker: me, text: finalText || interim }));
  };
  rec.onend = () => {
    $("#micBtn").classList.remove("rec");
    showTranscript("", "");
    if (finalText.trim()) sendChat(finalText);
    pttActive = false;
  };
  $("#micBtn").classList.add("rec");
  rec.start();
}

function stopPTT() { if (pttActive && rec) rec.stop(); }

function toggleListen(on) {
  if (!SR) { $("#listenMode").checked = false; return toast("Spraakherkenning niet beschikbaar in deze browser."); }
  listening = on;
  if (!on) { rec?.stop(); flushListen(false); showTranscript("", ""); return; }
  toast("Luistermodus aan: ik schrijf mee en reageer na een pauze of als je 'assistent' zegt.", 4000);
  const loop = () => {
    if (!listening) return;
    rec = makeRecognizer(true);
    rec.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) {
          const text = r[0].transcript.trim();
          if (text) {
            buffer.push(text);
            sendChat(text, false);       // vastleggen, nog niet reageren
            if (WAKE.test(text)) flushListen(true);
          }
        } else interim += r[0].transcript;
      }
      showTranscript(me || "Wij", interim);
      ws?.readyState === 1 && ws.send(JSON.stringify({ type: "transcript", speaker: me, text: interim }));
      clearTimeout(silenceTimer);
      silenceTimer = setTimeout(() => flushListen(true), 9000);  // ~9 s stilte: assistent haakt in
    };
    rec.onend = () => listening && setTimeout(loop, 250);       // browsers stoppen soms vanzelf
    rec.onerror = (e) => { if (e.error === "not-allowed") { listening = false; $("#listenMode").checked = false; toast("Microfoon geweigerd."); } };
    rec.start();
  };
  loop();
}

function flushListen(respond) {
  clearTimeout(silenceTimer);
  if (!buffer.length) return;
  buffer = [];
  if (respond) sendChat("", true);
}

// ---------------------------------------------------------------------------
// Menu
// ---------------------------------------------------------------------------
function babyLine(row) {
  if (!row.baby) return "";
  const b = row.baby;
  const warn = b.warnings.some((w) => ["verboden", "garen"].includes(w.level));
  const first = b.steps[0] || "";
  return `<div class="babyline ${warn ? "warn" : ""}">🍼 ${esc(b.verdict)} – ${esc(first)}</div>`;
}

function renderMenu() {
  const box = $("#days");
  box.innerHTML = "";
  for (const d of DAYS) {
    const rows = S.plan.filter((r) => r.day === d);
    const el = document.createElement("div");
    el.className = "day";
    el.innerHTML = `<header>${DAY_NAMES[d]}<small></small></header>`;
    for (const r of rows) {
      const name = r.meal ? r.meal.name : r.title;
      const lvl = r.meal ? `<span class="lvl lvl${r.meal.level}" title="${esc(LEVELS[r.meal.level])}">${r.meal.level}</span>` : "";
      const slot = document.createElement("div");
      slot.className = "slot" + (r.cooked ? " cooked" : "");
      slot.innerHTML = `<div class="name">${lvl}<b>${esc(name)}</b>
          <div class="meta">${esc(r.slot)}${r.meal && r.meal.prep_minutes ? " · " + r.meal.prep_minutes + " min" : ""}${r.servings ? " · " + r.servings + " pers." : ""}</div>
          ${r.slot === "diner" ? babyLine(r) : ""}</div>
        <button class="icon" title="Gekookt">${r.cooked ? "✅" : "☐"}</button>
        <button class="icon danger" title="Weghalen">✕</button>`;
      const [cookBtn, delBtn] = $$("button", slot);
      cookBtn.onclick = () => guarded(() => api(`/api/plan/${d}/${encodeURIComponent(r.slot)}/cooked`, { method: "POST" }), "Lekker! Genoteerd als gekookt.");
      delBtn.onclick = () => guarded(() => api(`/api/plan/${d}/${encodeURIComponent(r.slot)}`, { method: "DELETE" }));
      if (r.meal) $(".name", slot).onclick = () => openMeal(r.meal.id);
      el.appendChild(slot);
    }
    const add = document.createElement("button");
    add.className = "addslot";
    add.textContent = rows.some((r) => r.slot === "diner") ? "+ baby/lunch" : "+ avondeten kiezen";
    add.onclick = () => openSuggest(d, rows.some((r) => r.slot === "diner") ? "baby-lunch" : "diner");
    el.appendChild(add);
    box.appendChild(el);
  }
  $("#tips").innerHTML = S.tips.length
    ? S.tips.map((t) => `<div class="tip ${esc(t.type)}"><b>${esc(t.title)}</b>${esc(t.detail)}</div>`).join("")
    : `<p class="muted small">Plan een paar gerechten, dan geef ik tips om ze slim te combineren.</p>`;
}

// --- sheet helpers ----------------------------------------------------------
function openSheet(title, html) {
  $("#sheetTitle").textContent = title;
  $("#sheetBody").innerHTML = html;
  const d = $("#sheet");
  if (!d.open) d.showModal();
  return $("#sheetBody");
}
function closeSheet() { $("#sheet").close(); }

async function openSuggest(day, slot) {
  let level = store.get("lvl", 0);
  const body = openSheet(`${DAY_NAMES[day]} – ${slot}`, `
    ${slot === "diner" ? `<div class="seg" id="lvlSeg">${[0, 1, 2, 3, 4, 5].map((l) => `<button data-l="${l}">${l ? l : "alles"}</button>`).join("")}</div>
    <p class="muted small" id="lvlInfo"></p>` : ""}
    <div class="row"><input id="sugQ" placeholder="${slot === "diner" ? "Zoek of typ een eigen gerecht…" : "Bv. 'pastinaakpuree' of 'broodje hummus'"}"><button class="primary" id="sugFree">Zet erin</button></div>
    <div id="sugList"></div>`);
  const load = async () => {
    $$("#lvlSeg button", body).forEach((b) => b.classList.toggle("on", Number(b.dataset.l) === level));
    if ($("#lvlInfo", body)) $("#lvlInfo", body).textContent = level ? `Niveau ${level}: ${LEVELS[level]}` : "Alle niveaus, slim gesorteerd op restjes, variatie en baby.";
    const q = $("#sugQ", body).value.trim();
    let html = "";
    if (slot.startsWith("baby")) {
      const b = await api("/api/baby");
      const ideas = Object.entries(b.ideas).flatMap(([moment, list]) => list.map((x) => [moment, x]));
      html = ideas.map(([m, x]) => `<div class="sug" data-title="${esc(x)}"><b>${esc(x)}</b><small>${esc(m)}</small></div>`).join("");
    } else if (level === 3) {
      const r = await api("/api/meals/ready");
      html = r.map((o) => `<div class="sug" data-id="${o.meal_id}"><span class="lvl lvl3">3</span><b>${esc(o.name)}</b>
          <small>${euro(o.price_cents)}${o.is_organic ? " · bio" : ""} · ${o.times_bought ? o.times_bought + "x eerder besteld" : esc(o.source)}</small></div>`).join("");
    } else {
      const r = await api(`/api/meals/suggest?count=12${level ? "&level=" + level : ""}${q ? "&q=" + encodeURIComponent(q) : ""}`);
      html = r.map((s) => `<div class="sug" data-id="${s.id}"><span class="lvl lvl${s.level}">${s.level}</span><b>${esc(s.name)}</b>
          <small>${s.prep_minutes ? s.prep_minutes + " min · " : ""}${esc(s.reasons.join(" · ") || s.level_label)}</small></div>`).join("");
    }
    $("#sugList", body).innerHTML = html || `<p class="muted">Geen suggesties${level === 1 || level === 2 ? " – markeer onder ‘Menu’ welke gerechten jullie vaak maken." : "."}</p>`;
    $$(".sug", body).forEach((el) => (el.onclick = async () => {
      const payload = el.dataset.id ? { day, slot, meal_id: Number(el.dataset.id) } : { day, slot, title: el.dataset.title };
      await guarded(() => api("/api/plan", { method: "POST", body: payload }), el.dataset.id ? "Op het menu – ingrediënten staan op de lijst." : "Gepland.");
      closeSheet();
    }));
  };
  $$("#lvlSeg button", body).forEach((b) => (b.onclick = () => { level = Number(b.dataset.l); store.set("lvl", level); load(); }));
  let t;
  $("#sugQ", body).oninput = () => { clearTimeout(t); t = setTimeout(load, 300); };
  $("#sugFree", body).onclick = async () => {
    const title = $("#sugQ", body).value.trim();
    if (!title) return;
    if (slot === "diner") {
      closeSheet();
      return sendChat(S.assistant.mode === "claude"
        ? `Zet "${title}" op ${DAY_NAMES[day].toLowerCase()} (${slot}) en zet de ingrediënten op de lijst.`
        : `${DAY_NAMES[day].toLowerCase()} ${title}`);
    }
    await guarded(() => api("/api/plan", { method: "POST", body: { day, slot, title } }), "Gepland.");
    closeSheet();
  };
  load();
}

async function openMeal(id) {
  const m = await api(`/api/meals/${id}`);
  const a = m.baby_adapt;
  openSheet(m.name, `
    <p><span class="lvl lvl${m.level}">${m.level}</span>${esc(m.level_label)}${m.prep_minutes ? " · " + m.prep_minutes + " min" : ""}${m.times_cooked ? " · " + m.times_cooked + "x gekookt" : ""}</p>
    <h3>Ingrediënten (${m.servings} pers.)</h3>
    <ul>${m.ingredients.map((i) => `<li>${esc([i.qty, i.unit, i.name].filter(Boolean).join(" "))}${i.pantry ? ' <span class="muted small">(voorraad)</span>' : ""}</li>`).join("")}</ul>
    <h3>Baby – ${esc(a.verdict)}</h3>
    <ul>${a.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>
    ${a.warnings.length ? `<ul>${a.warnings.map((w) => `<li class="babyline warn">⚠︎ ${esc(w.msg)}</li>`).join("")}</ul>` : ""}
    <div class="seg" id="famSeg">${["vaak", "soms", "nieuw"].map((f) => `<button data-f="${f}" class="${m.familiarity === f ? "on" : ""}">${f === "vaak" ? "maken we vaak" : f === "soms" ? "soms" : "nieuw voor ons"}</button>`).join("")}</div>`);
  $$("#famSeg button").forEach((b) => (b.onclick = async () => {
    await guarded(() => api(`/api/meals/${id}/familiarity`, { method: "POST", body: { familiarity: b.dataset.f } }), "Bijgewerkt.");
    openMeal(id);
  }));
}

async function loadKnown() {
  const likely = await api("/api/meals/likely-known");
  $("#likelyKnown").innerHTML = likely.length
    ? `<p class="muted small">Op basis van jullie Picnic-bestellingen koken jullie dit waarschijnlijk al:</p>` +
      likely.map((l) => `<div class="opt" data-id="${l.id}"><div class="main"><b>${esc(l.meal)}</b><small>${Math.round(l.coverage * 100)}% van de verse ingrediënten bestellen jullie al</small></div>
        <button class="ghost small" data-f="vaak">vaak</button><button class="ghost small" data-f="soms">soms</button></div>`).join("")
    : "";
  $$("#likelyKnown .opt button").forEach((b) => (b.onclick = async () => {
    await guarded(() => api(`/api/meals/${b.closest(".opt").dataset.id}/familiarity`, { method: "POST", body: { familiarity: b.dataset.f } }), "Genoteerd.");
    loadKnown();
  }));
  const renderAll = async () => {
    const q = $("#knownSearch").value.trim().toLowerCase();
    const all = await api("/api/meals");
    const rows = all.filter((m) => m.kind === "zelf" && (!q || m.name.toLowerCase().includes(q))).slice(0, q ? 30 : 12);
    $("#knownList").innerHTML = rows.map((m) => `<div class="opt" data-id="${m.id}"><div class="main"><span class="lvl lvl${m.level}">${m.level}</span>${esc(m.name)}</div>
        <select>${["vaak", "soms", "nieuw"].map((f) => `<option ${m.familiarity === f ? "selected" : ""}>${f}</option>`).join("")}</select></div>`).join("");
    $$("#knownList select").forEach((s) => (s.onchange = () => guarded(() => api(`/api/meals/${s.closest(".opt").dataset.id}/familiarity`, { method: "POST", body: { familiarity: s.value } }), "Bijgewerkt.")));
  };
  $("#knownSearch").oninput = renderAll;
  renderAll();
}

// ---------------------------------------------------------------------------
// Lijst
// ---------------------------------------------------------------------------
function renderList() {
  const sm = S.summary;
  $("#summary").innerHTML = `
    <div><b>${sm.open}</b><span>open · ${sm.in_cart} in mandje</span></div>
    <div><b>${euro(sm.estimated_cents) || "–"}</b><span>geschat (Picnic)</span></div>
    <div><b>${Math.round(sm.organic_share * 100)}%</b><span>biologisch</span></div>`;
  const items = S.list.filter((i) => i.mode !== "zelf");
  const self = S.list.filter((i) => i.mode === "zelf");
  const groups = {};
  for (const i of items) (groups[i.category || "Overig"] ||= []).push(i);
  $("#list").innerHTML = items.length ? "" : `<div class="empty">De lijst is nog leeg. Praat met de assistent, plan gerechten of voeg vaste boodschappen toe.</div>`;
  for (const [cat, rows] of Object.entries(groups)) {
    const sec = document.createElement("div");
    sec.className = "cat";
    sec.innerHTML = `<h4>${esc(cat)}</h4>`;
    rows.forEach((i) => sec.appendChild(itemRow(i)));
    $("#list").appendChild(sec);
  }
  $("#selfList").innerHTML = "";
  if (self.length) {
    const sec = document.createElement("div");
    sec.className = "cat";
    sec.innerHTML = `<h4>Zelf halen (markt / winkel)</h4>`;
    self.forEach((i) => sec.appendChild(itemRow(i)));
    $("#selfList").appendChild(sec);
  }
}

function itemRow(i) {
  const el = document.createElement("div");
  el.className = "item " + i.status;
  const qty = `${+i.quantity.toFixed(1)}${i.unit ? " " + i.unit : ""}`;
  const prod = i.product_name
    ? `${esc(i.product_name)}${i.is_organic ? '<span class="bio">BIO</span>' : ""}${i.product_count > 1 ? " ×" + i.product_count : ""}`
    : i.mode === "kiezen" ? "👆 tik om zelf te kiezen" : i.mode === "zelf" ? esc(i.note || "") : "nog geen product";
  el.innerHTML = `
    <button class="check ${i.status === "in_mandje" || i.status === "gekocht" ? "on" : ""}" title="Afvinken"></button>
    <div class="main"><b>${esc(i.name)}</b> <span class="muted small">${esc(qty)}</span><div class="prod">${prod}</div></div>
    <span class="price">${i.price_cents && i.mode !== "zelf" ? euro(i.price_cents * (i.product_count || 1)) : ""}</span>
    <button class="mode ${i.mode}" title="Wie kiest het product?">${MODE_LABEL[i.mode]}</button>`;
  const [check, , mode] = [$(".check", el), null, $(".mode", el)];
  check.onclick = () => guarded(() => api(`/api/list/${i.id}`, { method: "PATCH", body: { status: i.status === "open" ? "gekocht" : "open" } }));
  mode.onclick = () => guarded(() => api(`/api/list/${i.id}`, { method: "PATCH", body: { mode: MODES[(MODES.indexOf(i.mode) + 1) % 3] } }));
  $(".main", el).onclick = () => openItem(i);
  return el;
}

async function openItem(i) {
  const body = openSheet(i.name, `
    <div class="seg" id="modeSeg">${MODES.map((m) => `<button data-m="${m}" class="${i.mode === m ? "on" : ""}">${MODE_LABEL[m]}</button>`).join("")}</div>
    <p class="muted small">Auto = de app kiest op basis van jullie bestelgeschiedenis en bio-voorkeur. Zelf kiezen = jij kiest hieronder. Zelf halen = niet via Picnic.</p>
    <div class="row"><input id="itQty" type="number" step="any" value="${i.quantity}"><input id="itUnit" value="${esc(i.unit || "")}" placeholder="eenheid"></div>
    <label class="toggle"><input type="checkbox" id="itBio" ${i.is_organic ? "checked" : ""}> per se biologisch</label>
    <label class="toggle" style="margin-left:12px"><button class="ghost small" id="itRule">Altijd zo voor “${esc(i.name)}”</button></label>
    <h3>Kies product</h3><div id="opts"><p class="muted">Opties laden…</p></div>
    <div class="actions"><button class="ghost danger" id="itDel">Van de lijst</button><button class="primary" id="itSave">Opslaan</button></div>`);
  let mode = i.mode;
  $$("#modeSeg button", body).forEach((b) => (b.onclick = () => { mode = b.dataset.m; $$("#modeSeg button", body).forEach((x) => x.classList.toggle("on", x === b)); }));
  $("#itDel", body).onclick = async () => { await guarded(() => api(`/api/list/${i.id}`, { method: "DELETE" })); closeSheet(); };
  $("#itRule", body).onclick = () => guarded(() => api("/api/mode-rules", { method: "POST", body: { keyword: i.name, mode } }), `Onthouden: ${i.name} → ${MODE_LABEL[mode]}`);
  $("#itSave", body).onclick = async () => {
    await guarded(() => api(`/api/list/${i.id}`, { method: "PATCH", body: {
      mode, quantity: Number($("#itQty", body).value) || i.quantity, unit: $("#itUnit", body).value || null,
      organic: $("#itBio", body).checked ? true : (i.is_organic ? false : null) } }), "Opgeslagen.");
    closeSheet();
  };
  const opts = await api(`/api/list/${i.id}/options`).catch(() => []);
  $("#opts", body).innerHTML = opts.length ? opts.map((o) => `
    <div class="opt ${o.id === i.product_id ? "on" : ""}" data-id="${esc(o.id)}"><div class="main"><b>${esc(o.name)}</b>${o.is_organic ? '<span class="bio">BIO</span>' : ""}
      <small>${esc(o.unit_quantity || "")}${o.times_bought ? ` · ${o.times_bought}x eerder besteld` : " · nieuw"}${o.on_promo ? " · actie" : ""}</small></div>
      <b>${euro(o.price_cents)}</b></div>`).join("") : `<p class="muted">Geen producten gevonden.</p>`;
  $$(".opt", body).forEach((el) => (el.onclick = async () => {
    await guarded(() => api(`/api/list/${i.id}/choose`, { method: "POST", body: { product_id: el.dataset.id } }), "Gekozen.");
    closeSheet();
  }));
}

// ---------------------------------------------------------------------------
// Baby
// ---------------------------------------------------------------------------
async function loadBaby() {
  const b = await api("/api/baby");
  const st = b.stage;
  $("#babyStage").innerHTML = `<h2>${esc(S.profile.baby_name)}${st.months != null ? ` · ${st.months} mnd` : ""}</h2>
    <p><b>${esc(st.label)}</b> – ${esc(st.texture)}${st.portion !== "-" ? ` (${esc(st.portion)})` : ""}</p>
    <p class="muted small">${esc(st.notes)}</p>
    ${st.months == null ? `<p class="small">Vul de geboortedatum of leeftijd in bij ⚙︎ voor advies op maat.</p>` : ""}
    ${b.taste && (b.taste.spice_ok || b.taste.likes || b.taste.avg === "af en toe") ? `<p class="small">👅 ${[
      b.taste.spice_ok ? "eet mild gekruid mee" : "", b.taste.likes ? "lust: " + esc(b.taste.likes) : "",
      b.taste.avg === "af en toe" ? "AVG af en toe (op smaak gebracht)" : ""].filter(Boolean).join(" · ")}</p>` : ""}`;
  $("#babyIdeas").innerHTML = `<div class="ideas">${Object.entries(b.ideas).map(([m, list]) =>
    `<div class="card"><b>${esc(m)}</b><ul>${list.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`).join("")}</div>`;
  $("#allergens").innerHTML = b.allergens.map((a) => {
    const cls = a.status === "nog niet" ? "nog" : a.status === "gestart" ? "gestart" : "gedaan";
    return `<div class="card allergen"><div class="main"><b>${esc(a.name)}</b> <span class="status ${cls}">${esc(a.status)}${a.times_given ? " · " + a.times_given + "x" : ""}</span>
      ${a.urgent ? `<div class="urgent">Nu een goed moment om te starten (vóór 8 mnd)</div>` : ""}
      <div class="muted small">${esc(a.advice)}</div></div>
      <button class="ghost small" data-n="${esc(a.name)}">+ gegeven</button></div>`;
  }).join("");
  $$("#allergens button").forEach((btn) => (btn.onclick = async () => {
    const reaction = prompt(`${btn.dataset.n} gegeven. Reactie? (leeg = geen)`) || null;
    await guarded(() => api("/api/baby/log", { method: "POST", body: { name: btn.dataset.n, reaction } }), "Genoteerd.");
    loadBaby();
  }));
  $("#babySource").textContent = b.source;
}

// ---------------------------------------------------------------------------
// Besparen
// ---------------------------------------------------------------------------
async function loadDeals() {
  if (!$("#deals").children.length) $("#deals").innerHTML = `<p class="muted">Prijzen vergelijken…</p>`;
  const d = await api("/api/deals");
  $("#dealsRefreshed").textContent = d.refreshed ? "Bijgewerkt: " + d.refreshed.replace("T", " ").slice(0, 16) : "Nog niet ververst";
  $("#deals").innerHTML = d.opportunities.length ? d.opportunities.map((o) => `
    <div class="card deal"><div class="main">
      <b>${esc(o.product.name)}</b> <span class="muted small">Picnic ${euro(o.product.price_cents)}${o.product.unit_quantity ? " / " + esc(o.product.unit_quantity) : ""}</span><br>
      <span class="store ${esc(o.store)}">${esc(o.store_name)}</span> ${o.deal.url ? `<a href="${esc(o.deal.url)}" target="_blank" rel="noopener">${esc(o.deal.title)}</a>` : esc(o.deal.title)}
      ${o.deal.label ? `<span class="muted small">· ${esc(o.deal.label)}</span>` : ""}${o.deal.unit ? `<span class="muted small"> · ${esc(o.deal.unit)}</span>` : ""}
      ${o.organic_mismatch ? `<div class="babyline warn">Let op: niet biologisch</div>` : ""}
      ${o.deal.valid_until ? `<div class="muted small">t/m ${esc(String(o.deal.valid_until).slice(0, 10))}</div>` : ""}
    </div><div class="save">−${euro(o.saving_cents)}<br><span class="muted small">${o.per_unit ? "per verpakking" : "per stuk"}</span></div></div>`).join("")
    : `<div class="empty">Nog geen besparingskansen gevonden. Ververs de aanbiedingen of synchroniseer eerst je Picnic-historie (⚙︎).</div>`;
  $("#watchlist").innerHTML = d.watchlist.map((w) => `<div class="small">${esc(w.name)} – ${euro(w.price_cents)} · ${w.times}x besteld</div>`).join("") || "<p class='muted'>Nog geen historie.</p>";
}

// ---------------------------------------------------------------------------
// Instellingen
// ---------------------------------------------------------------------------
async function openSettings() {
  const p = S.profile;
  const a = S.assistant;
  const status = await api("/api/picnic/status").catch(() => ({}));
  const body = openSheet("Instellingen", `
    <div class="field"><label>Namen (komma-gescheiden)</label><input id="sNames" value="${esc(p.names.join(", "))}"></div>
    <div class="row"><div class="field" style="flex:1"><label>Naam baby</label><input id="sBaby" value="${esc(p.baby_name)}"></div>
      <div class="field" style="flex:1"><label>Geboortedatum</label><input id="sBirth" type="date" value="${esc(p.baby_birthdate || "")}"></div></div>
    <div class="row"><div class="field" style="flex:1"><label>…of leeftijd (maanden)</label><input id="sAge" type="number" min="0" max="48" placeholder="${S.baby_stage.months ?? ""}"></div>
      <div class="field" style="flex:1"><label>Aardappel-groente-vlees</label><select id="sAvg">
        <option value="normaal" ${p.baby_avg !== "af en toe" ? "selected" : ""}>gewoon</option>
        <option value="af en toe" ${p.baby_avg === "af en toe" ? "selected" : ""}>af en toe (geen fan)</option></select></div></div>
    <label class="toggle"><input type="checkbox" id="sSpice" ${p.baby_spice_ok ? "checked" : ""}> Baby eet al mild gekruid mee (kerrie, knoflook, peper…)</label>
    <div class="field"><label>Baby lust graag</label><input id="sLikes" value="${esc(p.baby_likes || "")}" placeholder="bv. kerrie, knoflook, rijst, pasta, tomaat, mediterraan"></div>
    <div class="field"><label>Baby – overige notities</label><input id="sBabyNotes" value="${esc(p.baby_notes || "")}" placeholder="bv. lust geen spinazie, eet graag zelf"></div>
    <div class="row"><div class="field" style="flex:1"><label>Personen (volwassen porties)</label><input id="sPersons" type="number" min="1" value="${p.persons}"></div>
      <div class="field" style="flex:1"><label>Weekbudget (€)</label><input id="sBudget" type="number" min="0" value="${p.budget_week ?? ""}"></div></div>
    <div class="field"><label>Biologisch</label><select id="sOrganic">
      <option value="0" ${p.organic_level == 0 ? "selected" : ""}>Geen voorkeur</option>
      <option value="1" ${p.organic_level == 1 ? "selected" : ""}>Bio als het niet veel duurder is</option>
      <option value="2" ${p.organic_level == 2 ? "selected" : ""}>Altijd bio als het kan</option></select></div>
    <div class="field"><label>Dieet / voorkeuren (bv. 'partner eet geen varkensvlees', 'max 2x vlees per week')</label><textarea id="sDiet" rows="2">${esc(p.diet_notes || "")}</textarea></div>
    <div class="field"><label>Keuken & tijd (bv. 'doordeweeks max 30 min', 'airfryer')</label><textarea id="sKitchen" rows="2">${esc(p.kitchen_notes || "")}</textarea></div>
    <div class="actions"><button class="primary" id="sSave">Opslaan</button><button class="ghost" id="sWho">Ik ben iemand anders</button></div>
    <h3>Assistent & kosten</h3>
    <p class="small">${a.mode === "claude" ? "Claude actief ✓" : "Gratis commandomodus – " + esc(a.reason)}
      · deze maand ${a.usage.calls} aanroepen, <b>$${a.usage.cost_usd.toFixed(2)}</b>${a.budget_usd ? ` van $${Number(a.budget_usd).toFixed(2)}` : ""}</p>
    <div class="row"><div class="field" style="flex:2"><label>Model</label><select id="sModel">${a.models.map((m) => `<option value="${m.id}" ${m.id === a.model ? "selected" : ""}>${esc(m.label)}</option>`).join("")}</select></div>
      <div class="field" style="flex:1"><label>Maandbudget ($, 0 = geen)</label><input id="sBudgetAi" type="number" min="0" step="0.5" value="${a.budget_usd ?? 5}"></div></div>
    <p class="muted small">Boven het budget schakelt de app vanzelf over op de gratis commandomodus.</p>
    <h3>Picnic</h3>
    <p class="small">${status.demo ? "Demo-modus (geen account ingesteld in .env)." : status.ready ? "Verbonden ✓" : "Niet ingelogd."}
      ${status.products ? ` · ${status.products} producten in historie` : ""}${status.last_sync ? ` · laatste sync ${esc(status.last_sync.replace("T", " ").slice(0, 16))}` : ""}</p>
    <div class="actions"><button class="ghost" id="sSync">Bestelgeschiedenis ophalen</button>${!status.demo && !status.ready ? `<button class="ghost" id="sLogin">Inloggen</button>` : ""}</div>
    <h3>Zelf kiezen / zelf halen</h3>
    <div class="small">${Object.entries(S.mode_rules).map(([k, v]) => `${esc(k)} → ${esc(MODE_LABEL[v])}`).join("<br>") || "Nog geen regels."}</div>
    <div class="row"><input id="sRuleKw" placeholder="bv. avocado"><select id="sRuleMode">${MODES.map((m) => `<option value="${m}">${MODE_LABEL[m]}</option>`).join("")}</select><button class="ghost" id="sRule">+</button></div>`);
  $("#sSave", body).onclick = async () => {
    await guarded(() => api("/api/profile", { method: "POST", body: {
      names: $("#sNames", body).value.split(",").map((s) => s.trim()).filter(Boolean),
      baby_name: $("#sBaby", body).value.trim() || "Baby",
      baby_birthdate: $("#sAge", body).value !== "" ? new Date(Date.now() - Number($("#sAge", body).value) * 30.44 * 864e5).toISOString().slice(0, 10)
                                                    : $("#sBirth", body).value || null,
      persons: Number($("#sPersons", body).value) || 2,
      budget_week: $("#sBudget", body).value ? Number($("#sBudget", body).value) : null,
      organic_level: Number($("#sOrganic", body).value),
      diet_notes: $("#sDiet", body).value, kitchen_notes: $("#sKitchen", body).value,
      baby_spice_ok: $("#sSpice", body).checked, baby_likes: $("#sLikes", body).value.trim(),
      baby_avg: $("#sAvg", body).value, baby_notes: $("#sBabyNotes", body).value.trim(),
      model: $("#sModel", body).value, monthly_budget_usd: Number($("#sBudgetAi", body).value) || 0 } }), "Opgeslagen.");
    closeSheet();
  };
  $("#sWho", body).onclick = () => { closeSheet(); chooseWho(true); };
  $("#sSync", body).onclick = () => guarded(() => api("/api/picnic/sync", { method: "POST" }), (r) => `${r.deliveries} leveringen, ${r.products} producten.`);
  $("#sLogin", body)?.addEventListener("click", () => guarded(() => api("/api/picnic/login", { method: "POST" }), "Ingelogd."));
  $("#sRule", body).onclick = async () => {
    const kw = $("#sRuleKw", body).value.trim();
    if (!kw) return;
    await guarded(() => api("/api/mode-rules", { method: "POST", body: { keyword: kw, mode: $("#sRuleMode", body).value } }), "Onthouden.");
    openSettings();
  };
}

function openPicnicLogin() {
  const body = openSheet("Picnic-verificatie", `
    <p>Picnic vraagt om een eenmalige code.</p>
    <div class="actions"><button class="ghost" data-c="SMS">Stuur sms</button><button class="ghost" data-c="EMAIL">Stuur e-mail</button></div>
    <div class="row"><input id="otp" inputmode="numeric" placeholder="Code"><button class="primary" id="otpOk">Bevestig</button></div>`);
  $$("[data-c]", body).forEach((b) => (b.onclick = () => guarded(() => api("/api/picnic/2fa/request", { method: "POST", body: { channel: b.dataset.c } }), "Code verstuurd.")));
  $("#otpOk", body).onclick = async () => {
    await guarded(() => api("/api/picnic/2fa/verify", { method: "POST", body: { code: $("#otp", body).value.trim() } }), "Verbonden met Picnic ✓");
    closeSheet();
  };
}

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------
function bind() {
  $$(".tabs button").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));
  $("#prevWeek").onclick = () => guarded(async () => { await api("/api/week", { method: "POST", body: { week: shiftWeek(S.week, -1) } }); await loadChat(); });
  $("#nextWeek").onclick = () => guarded(async () => { await api("/api/week", { method: "POST", body: { week: shiftWeek(S.week, 1) } }); await loadChat(); });
  $("#openSettings").onclick = openSettings;
  $("#sheetClose").onclick = closeSheet;
  $("#speakerBtn").onclick = () => chooseWho(true);
  $("#chatForm").onsubmit = (e) => { e.preventDefault(); const v = $("#chatInput").value; $("#chatInput").value = ""; if (v.trim()) sendChat(v); };
  const mic = $("#micBtn");
  mic.addEventListener("pointerdown", (e) => { e.preventDefault(); startPTT(); });
  ["pointerup", "pointerleave", "pointercancel"].forEach((ev) => mic.addEventListener(ev, stopPTT));
  $("#listenMode").onchange = (e) => toggleListen(e.target.checked);
  $("#speakReplies").checked = store.get("speak", false);
  $("#speakReplies").onchange = (e) => store.set("speak", e.target.checked);
  $("#askBtn").onclick = () => { buffer = []; sendChat("", true); };
  $("#newChat").onclick = () => confirm("Nieuw gesprek beginnen? Menu en lijst blijven staan.") && guarded(() => api("/api/chat/new", { method: "POST" }));
  $("#addItemForm").onsubmit = async (e) => {
    e.preventDefault();
    const name = $("#addItemName").value.trim();
    if (!name) return;
    await guarded(() => api("/api/list", { method: "POST", body: { name, quantity: Number($("#addItemQty").value) || 1, added_by: me } }));
    $("#addItemName").value = ""; $("#addItemQty").value = "";
  };
  $("#basicsBtn").onclick = () => guarded(() => api("/api/list/basics", { method: "POST" }), (r) => r.length ? `${r.length} vaste boodschappen toegevoegd.` : "Niets dat nu op is volgens jullie ritme.");
  $("#resolveBtn").onclick = () => guarded(() => api("/api/list/resolve", { method: "POST" }), (r) => `${r.matched} producten gekozen${r.unmatched.length ? `, niet gevonden: ${r.unmatched.join(", ")}` : ""}.`);
  $("#cartBtn").onclick = () => confirm("Alles (behalve 'zelf kiezen' zonder keuze en 'zelf halen') in het Picnic-mandje zetten? Je bestelt daarna zelf in de Picnic-app.") &&
    guarded(() => api("/api/cart/push", { method: "POST" }), (r) => `${r.added.length} in mandje${r.skipped.length ? `, ${r.skipped.length} overgeslagen` : ""}${r.errors.length ? `, ${r.errors.length} fouten` : ""}.`);
  $("#refreshDeals").onclick = async (e) => {
    e.target.disabled = true;
    await guarded(() => api("/api/deals/refresh", { method: "POST" }), (r) => Object.entries(r).map(([k, v]) => `${k}: ${v.ok ? v.deals : "✗"}`).join(" · "));
    e.target.disabled = false;
    loadDeals();
  };
}

(async function init() {
  bind();
  renderQuick();
  await guarded(refresh);
  chooseWho();
  updateSpeaker();
  await guarded(loadChat);
  connectWS();
  showTab(store.get("tab", "chat"));
})();
