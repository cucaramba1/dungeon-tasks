"use strict";
// Dungeon Tasks PWA. Без фреймворков: состояние приходит одним запросом /api/state.

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch {} },
};

// ключ доступа: один раз открыть /?key=..., дальше живёт в localStorage
const urlKey = new URLSearchParams(location.search).get("key");
if (urlKey) store.set("key", urlKey);
const KEY = urlKey || store.get("key") || "";

let S = null;           // последнее состояние
let tab = store.get("tab") || "today";
let raidTimer = null;

async function api(path, opts = {}) {
  const headers = { "X-Key": KEY, ...(opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : {}) };
  const res = await fetch(path, { ...opts, headers: { ...headers, ...(opts.headers || {}) } });
  if (res.status === 401) { toast("🔒 Нужен ключ: открой ссылку с ?key=…"); throw new Error("401"); }
  if (!res.ok) { toast("⚠️ Ошибка " + res.status); throw new Error(res.status); }
  return res.json();
}
const post = (p, body) => api(p, { method: "POST", body: body instanceof FormData ? body : JSON.stringify(body || {}) });

function toast(text, ms = 3200) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = text;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), ms);
}

async function load() {
  try { S = await api("/api/state"); render(); } catch (e) { console.warn(e); }
}

// ---------- рендер ----------
function render() {
  renderHero();
  const badge = $("#inboxBadge");
  badge.hidden = !S.inbox.length;
  badge.textContent = S.inbox.length;
  ({ today: renderToday, map: renderMap, inbox: renderInbox, raid: renderRaid, hero: renderHeroTab })[tab]();
}

function renderHero() {
  const h = S.hero;
  const xpIn = h.xp - h.cur_at, xpNeed = h.next_at - h.cur_at;
  $("#hero").innerHTML = `
    <div class="lvl">${h.level}</div>
    <div class="name">${esc(h.title)}</div>
    <div class="gold">🪙 ${h.gold}</div>
    <div class="bars">
      <div class="bar hp"><i style="width:${(100 * h.hp) / h.max_hp}%"></i></div>
      <div class="bar xp"><i style="width:${(100 * xpIn) / xpNeed}%"></i></div>
      <div class="bar-label"><span>❤️ ${h.hp}/${h.max_hp}</span><span>⭐ ${xpIn}/${xpNeed} XP</span></div>
    </div>`;
}

function taskHTML(t, opts = {}) {
  const meta = [];
  if (t.when) meta.push(`<span class="${t.overdue ? "late" : ""}">${esc(t.when)}</span>`);
  if (t.dur) meta.push(`<span>⏱ ${esc(t.dur)}</span>`);
  if (t.repeat) meta.push(`<span>🔁 ${esc(t.repeat)}</span>`);
  if (t.is_habit) meta.push("<span>🕯 ритуал</span>");
  if (t.is_boss) meta.push("<span>🐉 босс</span>");
  const q = t.quadrant ? `q${t.quadrant}` : "";
  return `<div class="task ${q} ${t.is_boss ? "boss" : ""}" data-id="${t.id}" ${opts.drag ? 'draggable="true"' : ""}>
    <button class="check" data-done="${t.id}" aria-label="Сделано"></button>
    <div class="body" data-open="${t.id}">
      <div class="title">${esc(t.title)}</div>
      ${meta.length ? `<div class="meta">${meta.join("")}</div>` : ""}
    </div>
    ${opts.noZone ? "" : `<span class="zone">${t.icon}</span>`}
  </div>`;
}

const list = (items, empty, opts) =>
  items.length ? `<div class="list">${items.map((t) => taskHTML(t, opts)).join("")}</div>` : `<div class="empty">${empty}</div>`;

// свёрнут ли чек-лист: запоминаем на день; по умолчанию свёрнут, если закрыт целиком
const foldKey = (kind) => `cl_fold:${kind}:${S.now.slice(0, 10)}`;
function isFolded(kind, allDone) {
  const v = store.get(foldKey(kind));
  return v === null ? allDone : v === "1";
}

function checklistHTML() {
  // вечером показываем вечерний, до этого утренний
  const hhmm = new Date(S.now).toTimeString().slice(0, 5);
  const kind = hhmm >= S.evening_at ? "evening" : "morning";
  const items = S.checklists[kind] || [];
  if (!items.length) return "";
  const done = items.filter((i) => i.done).length;
  const all = done === items.length;
  const folded = isFolded(kind, all);
  const title = kind === "morning" ? "☀️ Утренний чек-лист" : "🌙 Вечерний чек-лист";
  const head = `<h2 id="checklist" class="fold" data-fold="${kind}" role="button" aria-expanded="${!folded}">
    <span class="chev">${folded ? "▸" : "▾"}</span> ${title}
    <span class="hint">${done}/${items.length}${all ? " · закрыт 💪" : ""}</span></h2>`;
  if (folded) return head;
  return head + `<div class="list">${items.map((i) => `<div class="task cl ${i.done ? "done" : ""}">
      <button class="check" data-cl="${kind}:${i.idx}" aria-label="Отметить">${i.done ? "✓" : ""}</button>
      <div class="body" data-cl="${kind}:${i.idx}"><div class="title">${esc(i.text)}</div></div></div>`).join("")}</div>`;
}

function renderToday() {
  const byTime = [...S.today].sort((a, b) => (a.overdue === b.overdue ? 0 : a.overdue ? -1 : 1));
  let html = checklistHTML();
  if (S.boss) html += `<h2>🐉 Босс недели</h2>${list([S.boss], "")}`;
  html += `<h2>☀️ Сегодня</h2>${list(byTime, "На сегодня тихо. Самое время для библиотеки 📜")}`;
  if (S.library.length) html += `<h2>📜 Библиотека ждёт <span class="hint">важное без срока</span></h2>${list(S.library.slice(0, 5), "")}`;
  if (S.tomorrow.length) html += `<h2>🗺 Завтра</h2>${list(S.tomorrow, "")}`;
  $("#v-today").innerHTML = html;
  if (location.hash === "#checklist" && !window._clScrolled) {
    window._clScrolled = true;
    $("#checklist")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function renderMap() {
  const names = { 1: "Огненные залы", 2: "Эльфийская библиотека", 3: "Гоблинские поручения", 4: "Туманное болото" };
  const hints = { 1: "срочно · важно", 2: "важно · не срочно", 3: "срочно · не важно", 4: "в топку?" };
  const cells = [1, 2, 3, 4].map((q) => {
    const items = S.open.filter((t) => t.quadrant === q);
    return `<div class="cell q${q}" data-q="${q}">
      <h3>${S.zones[q][0]} ${names[q]}</h3>
      <div class="meta" style="font-size:11px;color:var(--muted)">${hints[q]}</div>
      ${items.map((t) => taskHTML(t, { drag: true, noZone: true })).join("") || '<div class="empty">пусто</div>'}
    </div>`;
  });
  $("#v-map").innerHTML = `<h2>🗺 Карта подземелья</h2><div class="matrix">${cells.join("")}</div>
    <div class="axis"><span>← срочно</span><span>не срочно →</span></div>`;
  bindDrag();
}

function renderInbox() {
  const rows = S.inbox.map((t) => `
    <div class="task" data-id="${t.id}">
      <div class="body" data-open="${t.id}"><div class="title">${esc(t.title)}</div>
        ${t.when ? `<div class="meta">${esc(t.when)}</div>` : ""}</div>
      <div class="triage">${[1, 2, 3, 4].map((q) => `<button class="chip" data-q="${q}" data-set="${t.id}">${S.zones[q][0]}</button>`).join("")}</div>
    </div>`).join("");
  $("#v-inbox").innerHTML = `<h2>📥 Сундук у входа <span class="hint">раскидай по зонам</span></h2>
    ${rows ? `<div class="list">${rows}</div>` : '<div class="empty">Пусто. Чистота как в эльфийском храме ✨</div>'}`;
}

function renderRaid() {
  const f = S.focus;
  const opts = S.open
    .map((t) => `<option value="${t.id}">${t.icon} ${esc(t.title)}</option>`).join("");
  const total = S.focus_min * 60;
  $("#v-raid").innerHTML = `<div class="raid">
    <h2>⚔ Фокус-рейд</h2>
    <div class="ring"><svg viewBox="0 0 120 120"><circle class="track" cx="60" cy="60" r="52"/>
      <circle class="prog" id="ringProg" cx="60" cy="60" r="52" stroke-dasharray="326.7" stroke-dashoffset="0"/></svg>
      <div class="time" id="raidTime">${String(S.focus_min).padStart(2, "0")}:00</div></div>
    ${f ? `<p>${f.task_id ? "Цель: " + esc((S.open.find((t) => t.id === f.task_id) || {}).title || "") : "Свободный рейд"}</p>
          <button class="btn ghost" id="raidStop">🏳 Прервать</button>`
        : `<select id="raidTask"><option value="">Без привязки к задаче</option>${opts}</select>
           <button class="btn" id="raidStart">⚔ Начать рейд · ${S.focus_min} мин</button>
           <p class="empty">+20 XP за рейд. Телефон экраном вниз.</p>`}
  </div>`;
  clearInterval(raidTimer);
  if (f) {
    const end = new Date(f.ends_at).getTime();
    const tickFn = () => {
      const left = Math.max(0, Math.round((end - Date.now()) / 1000));
      $("#raidTime").textContent = `${String(Math.floor(left / 60)).padStart(2, "0")}:${String(left % 60).padStart(2, "0")}`;
      $("#ringProg").style.strokeDashoffset = 326.7 * (1 - left / total);
      if (!left) { clearInterval(raidTimer); toast("🏁 Рейд окончен! +20 XP"); setTimeout(load, 20000); }
    };
    tickFn();
    raidTimer = setInterval(tickFn, 1000);
  }
}

function renderHeroTab() {
  const h = S.hero;
  const got = S.achievements.filter((a) => a.unlocked_at).length;
  const streaks = S.streaks.filter((s) => s.current > 0);
  $("#v-hero").innerHTML = `
    <h2>🛡 Лист героя</h2>
    <div class="stats">
      <div class="stat"><b>${h.level}</b><span>уровень</span></div>
      <div class="stat"><b>${h.xp}</b><span>всего XP</span></div>
      <div class="stat"><b>${h.best_streak}</b><span>лучший стрик</span></div>
    </div>
    <h2>🔮 Стрики рун</h2>
    ${streaks.length ? `<div class="list">${streaks.map((s) => `<div class="row"><span>🕯 ${esc(s.title)}</span><span>${s.current} дн. · рекорд ${s.best}</span></div>`).join("")}</div>` : '<div class="empty">Пока нет. Добавь ритуал: «каждый день в 8 йога».</div>'}
    <h2>🏅 Ачивки ${got}/${S.achievements.length}</h2>
    <div class="achs">${S.achievements.map((a) => `<div class="ach ${a.unlocked_at ? "" : "locked"}">
      <div class="ic">${a.icon}</div><div class="nm">${esc(a.name)}</div><div class="ds">${esc(a.desc)}</div></div>`).join("")}</div>
    <h2>🍺 Таверна <span class="hint">награды за золото, добавить в боте: /reward Пицца 200</span></h2>
    ${S.rewards.length ? `<div class="list">${S.rewards.map((r) => `<div class="row"><span>${esc(r.title)}</span>
      <button class="chip" data-buy="${r.id}">${r.price} 🪙</button></div>`).join("")}</div>` : '<div class="empty">Таверна пуста.</div>'}`;
}

// ---------- шторка задачи ----------
function openSheet(id) {
  const t = S.open.find((x) => x.id === id) || S.inbox.find((x) => x.id === id) || S.today.find((x) => x.id === id);
  if (!t) return;
  const sheet = $("#sheet");
  sheet.innerHTML = `
    <input id="shTitle" value="${esc(t.title)}" aria-label="Название">
    <div class="zones">${[1, 2, 3, 4].map((q) => `<button class="chip ${t.quadrant === q ? "on" : ""}" data-sq="${q}">${S.zones[q][0]} ${S.zones[q][1].split(" ")[0]}</button>`).join("")}</div>
    <input id="shWhen" placeholder="${esc(t.when || "когда? «завтра в 15», «по будням в 9»")}" aria-label="Срок">
    <div class="acts">
      <button class="chip" data-act="done">✅ Сделал</button>
      <button class="chip" data-act="s15">⏰ 15 мин</button>
      <button class="chip" data-act="tomorrow">➡ Завтра</button>
      <button class="chip ${t.is_habit ? "on" : ""}" data-act="habit">🕯 Ритуал</button>
      <button class="chip ${t.is_boss ? "on" : ""}" data-act="boss">🐉 Босс</button>
      ${t.rrule ? '<button class="chip" data-act="skip">⏭ Пропуск</button>' : '<button class="chip" data-act="focus">⚔ Рейд</button>'}
    </div>
    <div class="acts" style="grid-template-columns:1fr 1fr">
      <button class="chip" data-act="delete">🗑 Удалить</button>
      <button class="chip on" data-act="save">💾 Сохранить</button>
    </div>
    <small>#${t.id}${t.repeat ? " · 🔁 " + esc(t.repeat) : ""}</small>`;
  sheet.dataset.id = id;
  sheet.hidden = $("#backdrop").hidden = false;
}
const closeSheet = () => { $("#sheet").hidden = $("#backdrop").hidden = true; };

async function sheetAction(act) {
  const id = +$("#sheet").dataset.id;
  const t = S.open.find((x) => x.id === id) || {};
  if (act === "done") return done(id).then(closeSheet);
  if (act === "s15") await post(`/api/tasks/${id}/snooze`, { minutes: 15 });
  if (act === "tomorrow") await post(`/api/tasks/${id}/snooze`, { tomorrow: true });
  if (act === "habit") await api(`/api/tasks/${id}`, { method: "PATCH", body: JSON.stringify({ toggle_habit: true }) });
  if (act === "boss") { await api(`/api/tasks/${id}`, { method: "PATCH", body: JSON.stringify({ boss: true }) }); toast("🐉 Босс назначен"); }
  if (act === "skip") await post(`/api/tasks/${id}/skip`);
  if (act === "focus") { await post(`/api/focus?task_id=${id}`); switchTab("raid"); }
  if (act === "delete") { const r = await api(`/api/tasks/${id}`, { method: "DELETE" }); (r.msgs || []).forEach((m) => toast(m)); }
  if (act === "save") {
    const title = $("#shTitle").value.trim(), when = $("#shWhen").value.trim();
    const body = {};
    if (title && title !== t.title) body.title = title;
    if (when) body.when = when;
    if (Object.keys(body).length) await api(`/api/tasks/${id}`, { method: "PATCH", body: JSON.stringify(body) });
  }
  closeSheet();
  load();
}

async function done(id) {
  const el = document.querySelector(`.task[data-id="${id}"]`);
  el?.classList.add("done");
  if (navigator.vibrate) navigator.vibrate(20);
  const r = await post(`/api/tasks/${id}/done`);
  toast(`⚔ +${r.xp} XP`);
  (r.msgs || []).forEach((m, i) => setTimeout(() => toast(m, 4500), 400 * (i + 1)));
  setTimeout(load, 350);
}

// ---------- drag & drop между зонами (десктоп) ----------
function bindDrag() {
  document.querySelectorAll(".task[draggable]").forEach((el) =>
    el.addEventListener("dragstart", (e) => e.dataTransfer.setData("text/plain", el.dataset.id)));
  document.querySelectorAll(".cell").forEach((cell) => {
    cell.addEventListener("dragover", (e) => { e.preventDefault(); cell.classList.add("over"); });
    cell.addEventListener("dragleave", () => cell.classList.remove("over"));
    cell.addEventListener("drop", async (e) => {
      e.preventDefault();
      cell.classList.remove("over");
      const id = +e.dataTransfer.getData("text/plain");
      await api(`/api/tasks/${id}`, { method: "PATCH", body: JSON.stringify({ quadrant: +cell.dataset.q }) });
      load();
    });
  });
}

// ---------- ввод ----------
$("#capture").addEventListener("submit", async (e) => {
  e.preventDefault();
  const inp = $("#captureInput");
  const text = inp.value.trim();
  if (!text) return;
  inp.value = "";
  const r = await post("/api/tasks", { text, source: "pwa" });
  toast("➕ " + r.say);
  load();
});

let rec = null, chunks = [];
$("#mic").addEventListener("click", async () => {
  const btn = $("#mic");
  if (rec && rec.state === "recording") { rec.stop(); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mime = ["audio/mp4", "audio/webm;codecs=opus", "audio/webm"].find((m) => window.MediaRecorder?.isTypeSupported?.(m)) || "";
    rec = new MediaRecorder(stream, mime ? { mimeType: mime } : {});
    chunks = [];
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    rec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      btn.classList.remove("rec");
      btn.classList.add("busy");
      btn.textContent = "⏳";
      const type = rec.mimeType || "audio/mp4";
      const fd = new FormData();
      fd.append("file", new Blob(chunks, { type }), type.includes("webm") ? "voice.webm" : "voice.m4a");
      try {
        const r = await post("/api/voice", fd);
        toast(r.text ? `🎙 «${r.text}»` : "🎙 Тишина, ничего не услышал", 4500);
        load();
      } finally {
        btn.classList.remove("busy");
        btn.textContent = "🎙";
      }
    };
    rec.start();
    btn.classList.add("rec");
    btn.textContent = "⏹";
  } catch (err) {
    toast("🎙 Нет доступа к микрофону. Нужен HTTPS (Tailscale) и разрешение.");
  }
});

// ---------- делегирование кликов ----------
document.addEventListener("click", async (e) => {
  const fold = e.target.closest("[data-fold]");
  if (fold) {
    const kind = fold.dataset.fold;
    const all = (S.checklists[kind] || []).every((i) => i.done);
    store.set(foldKey(kind), isFolded(kind, all) ? "0" : "1");
    return renderToday();
  }
  const cl = e.target.closest("[data-cl]");
  if (cl) {
    const [kind, idx] = cl.dataset.cl.split(":");
    const r = await post(`/api/checklist/${kind}/${idx}`);
    S.checklists[kind] = r.state;
    if (r.state.every((i) => i.done)) store.set(foldKey(kind), "1");  // закрыл всё — свернули
    (r.msgs || []).forEach((m, i) => setTimeout(() => toast(m, 4500), 300 * i));
    if (r.msgs?.length) return load();
    return renderToday();
  }
  const el = e.target.closest("[data-done],[data-open],[data-set],[data-tab],[data-sq],[data-act],[data-buy],#raidStart,#raidStop,#backdrop");
  if (!el) return;
  if (el.dataset.done) return done(+el.dataset.done);
  if (el.dataset.open) return openSheet(+el.dataset.open);
  if (el.dataset.set) {
    await api(`/api/tasks/${el.dataset.set}`, { method: "PATCH", body: JSON.stringify({ quadrant: +el.dataset.q }) });
    return load();
  }
  if (el.dataset.tab) return switchTab(el.dataset.tab);
  if (el.dataset.sq) {
    await api(`/api/tasks/${$("#sheet").dataset.id}`, { method: "PATCH", body: JSON.stringify({ quadrant: +el.dataset.sq }) });
    closeSheet();
    return load();
  }
  if (el.dataset.act) return sheetAction(el.dataset.act);
  if (el.dataset.buy) { const r = await post(`/api/buy/${el.dataset.buy}`); toast(r.msg, 4000); return load(); }
  if (el.id === "raidStart") { const v = $("#raidTask").value; await post("/api/focus" + (v ? `?task_id=${v}` : "")); return load(); }
  if (el.id === "raidStop") { await api("/api/focus", { method: "DELETE" }); toast("🏳 Рейд прерван"); return load(); }
  if (el.id === "backdrop") closeSheet();
});

function switchTab(name) {
  tab = name;
  store.set("tab", name);
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.dataset.view !== name));
  if (S) render();
}

document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });
document.addEventListener("visibilitychange", () => document.visibilityState === "visible" && load());
setInterval(() => document.visibilityState === "visible" && load(), 60000);
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

if (location.hash === "#checklist") tab = "today";
switchTab(tab);
load();
