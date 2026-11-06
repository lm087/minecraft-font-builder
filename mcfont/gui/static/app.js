// The Minecraft Font Builder app. It talks to the local server of mcfont gui.
"use strict";

const TOKEN = new URLSearchParams(location.hash.slice(1)).get("t") || "";
const STYLE_ORDER = ["regular", "bold", "italic", "bold_italic"];
const FONT_ORDER = ["minecraft:default", "minecraft:alt", "minecraft:illageralt", "minecraft:uniform"];
const LANGUAGES = [
  { lang: "en", name: "English (US)", icon: "🇺🇸" },
  { lang: "zh-TW", name: "中文（臺灣）", icon: "🇹🇼" },
];
const PLANE_NAMES = { 0: "planeName0", 1: "planeName1", 2: "planeName2", 3: "planeName3", 14: "planeName14", 15: "planeName15", 16: "planeName16" };
const PLANE_HINTS = { 0: "planeHint0", 1: "planeHint1", 2: "planeHint2", 3: "planeHint3", 14: "planeHint14", 15: "planeHintPrivate", 16: "planeHintPrivate" };

const S = {
  lang: "en",
  start: null,        // /api/start
  settings: null,     // the settings, same shape as the YAML file
  useLocal: false,
  versions: null,     // /api/versions
  versionsError: "",
  game: null,         // /api/fonts
  gameError: "",
  loadingGame: false,
  counts: null,       // /api/count
  countError: "",
  counting: false,
  showFragments: false,
  blockFont: "",
  blockFilter: "",
  blockView: "all",
  job: { state: "idle", log: [], total: 0, done: 0, elapsed: 0, error: "", files: [] },
  builtWith: "",      // the settings of the last build, to tell if its result still applies
  modes: {},          // choices the settings can't show, such as "custom" with no text yet
  previewText: null,
  builtAt: 0,         // when the last build finished, so the preview loads the new files
  previewSize: 16,
  previewBg: "white",
};
let gameSeq = 0;
let countSeq = 0;
let countTimer = null;
let namesTimer = null;
let footer = null;  // the footer's scroll behavior
const faces = new Map();

// helpers
const $ = (id) => document.getElementById(id);
const fmt = (n) => String(n);  // no thousands separators
const clone = (o) => JSON.parse(JSON.stringify(o));

function el(tag, props = {}, children = []) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === undefined || v === null) continue;
    if (k === "text") e.textContent = v;
    else if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (typeof v === "boolean") e[k] = v;
    else e.setAttribute(k, v);
  }
  for (const c of [].concat(children)) if (c !== null && c !== undefined && c !== false) e.append(c);
  return e;
}

function t(key, ...args) {
  let s = (STRINGS[S.lang] && STRINGS[S.lang][key]) ?? STRINGS.en[key] ?? key;
  args.forEach((a, i) => { s = s.split(`{${i}}`).join(String(a)); });
  return s;
}

const hasText = (key) => key in STRINGS.en;

async function api(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Token": TOKEN },
    body: JSON.stringify(body || {}),
  });
  let j;
  try { j = await r.json(); } catch (e) { j = { error: r.statusText }; }
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

// a status word: OK, OVER, ERROR, TODO, or "..." while waiting
function tag(kind) {
  const text = { ok: t("tagOk"), over: t("tagOver"), error: t("tagError"), todo: t("tagTodo"), busy: "..." }[kind];
  const cls = kind === "over" || kind === "error" ? "tag is-over" : kind === "busy" ? "tag is-busy" : "tag";
  return el("span", { class: cls, text });
}

function fillSelect(select, items, value) {
  select.replaceChildren(...items.map(([v, label]) => el("option", { value: String(v), text: label })));
  select.value = String(value);
}

// a checkbox or radio button with its text, and an optional explanation under it
function option(type, checked, text, hint, onChange, props = {}) {
  const { textClass, ...attrs } = props;
  const input = el("input", { type, checked, onchange: (e) => onChange(e.target.checked), ...attrs });
  const label = el("label", {}, [input, el("span", { class: textClass || null, text })]);
  return hint ? [label, el("p", { class: "check-hint", text: hint })] : [label];
}

function duration(sec) {
  const s = Math.max(0, Math.round(sec));
  return s < 60 ? t("seconds", s) : t("minutes", Math.floor(s / 60), s % 60);
}

function fullPath(p) {
  if (!p) return "";
  if (p.startsWith("/") || p.startsWith("~") || /^[A-Za-z]:[\\/]/.test(p)) return p;
  const base = S.start ? S.start.workdir : "";
  const sep = base.includes("\\") && !base.includes("/") ? "\\" : "/";
  return base ? base.replace(/[\\/]$/, "") + sep + p : p;
}

function reveal(id) {
  const target = $(id);
  if (target.tagName === "DETAILS") target.open = true;
  const top = target.getBoundingClientRect().top + window.scrollY - ($("top").offsetHeight + 16);
  window.scrollTo({ top, behavior: "smooth" });
}

const rules = () => S.settings.characters;
const fontInfo = (id) => (S.game ? S.game.fonts.find((f) => f.id === id) : null);
const countOf = (id) => (S.counts ? S.counts.fonts.find((f) => f.font_id === id) : null);
const displayFamily = (id, fallback) => (fontInfo(id) ? fontInfo(id).family : fallback || id);

function fontOrder(a, b) {
  const ia = FONT_ORDER.indexOf(a.id), ib = FONT_ORDER.indexOf(b.id);
  if (ia !== ib) return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  return a.include - b.include || a.id.localeCompare(b.id);
}

// the font ids that will be built: "all" expanded, fonts this version lacks left out
function selectedFonts() {
  const ids = S.game ? S.game.fonts.map((f) => f.id) : [];
  const all = S.game ? S.game.fonts.filter((f) => !f.include).map((f) => f.id) : [];
  const out = [];
  for (const f of S.settings.fonts) {
    for (const id of f === "all" ? all : [f.includes(":") ? f : "minecraft:" + f]) {
      if (!out.includes(id) && (!S.game || ids.includes(id))) out.push(id);
    }
  }
  return out;
}

function missingFonts() {
  if (!S.game) return [];
  const ids = S.game.fonts.map((f) => f.id);
  return S.settings.fonts.filter((f) => f !== "all").map((f) => (f.includes(":") ? f : "minecraft:" + f)).filter((f) => !ids.includes(f));
}

function worstStyle(c) {
  let best = null;
  if (c) for (const [style, v] of Object.entries(c.styles)) if (!best || v.glyphs > best.glyphs) best = { style, ...v };
  return best;
}

function overFonts() {
  return S.counts ? S.counts.fonts.filter((f) => Object.values(f.styles).some((s) => s.over > 0)) : [];
}

// header, footer, menu, toast and theme
function initScrollHeader() {
  const header = $("top");
  header.setAttribute("data-scroll-header", "");
  const scrollPosition = () => Math.max(0, Math.min(window.scrollY, Math.max(0, document.documentElement.scrollHeight - window.innerHeight)));
  let previousY = scrollPosition();
  let collapse = 0;
  let frame = 0;
  const update = () => {
    frame = 0;
    const y = scrollPosition();
    collapse = y === 0 ? 0 : Math.max(0, Math.min(1, collapse + (y - previousY) / 100));
    header.style.setProperty("--header-collapse", String(collapse));
    window.dispatchEvent(new Event("headerpositionchange"));
    previousY = y;
  };
  window.addEventListener("scroll", () => { if (!frame) frame = requestAnimationFrame(update); }, { passive: true });
  window.addEventListener("resize", update);
  window.addEventListener("pageshow", () => { previousY = scrollPosition(); update(); });
}

function initScrollFooter() {
  const bar = $("bottom");
  const scrollPosition = () => Math.max(0, Math.min(window.scrollY, Math.max(0, document.documentElement.scrollHeight - window.innerHeight)));
  let previousY = scrollPosition();
  let offset = 0;
  let frame = 0;
  let settleTimer = 0;
  let keyboardInput = true;
  let hovering = false;
  let animationFrame = 0;
  let displayedOffset = 0;
  let targetOffset = 0;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const hasKeyboardFocus = () => keyboardInput && bar.contains(document.activeElement);

  const paint = (value) => {
    displayedOffset = value;
    bar.style.setProperty("--footer-scroll-offset", `${value}px`);
    window.dispatchEvent(new Event("footerpositionchange"));
  };
  const render = (animate = false) => {
    const next = hovering || hasKeyboardFocus() ? 0 : offset;
    if (animationFrame && next === targetOffset) return;
    cancelAnimationFrame(animationFrame);
    animationFrame = 0;
    targetOffset = next;
    if (!animate || reducedMotion.matches || displayedOffset === next) {
      paint(next);
      return;
    }
    const from = displayedOffset;
    const start = performance.now();
    const step = (now) => {
      const progress = Math.min(1, (now - start) / 220);
      paint(from + (next - from) * (1 - Math.pow(1 - progress, 3)));
      animationFrame = progress < 1 ? requestAnimationFrame(step) : 0;
    };
    animationFrame = requestAnimationFrame(step);
  };
  const update = () => {
    frame = 0;
    const y = scrollPosition();
    if (animationFrame && !hovering && !hasKeyboardFocus()) offset = displayedOffset;
    offset = y === 0 || hasKeyboardFocus() ? 0 : Math.max(0, Math.min(bar.offsetHeight, offset + y - previousY));
    previousY = y;
    render();
  };

  window.addEventListener("scroll", () => {
    if (!frame) frame = requestAnimationFrame(update);
    clearTimeout(settleTimer);
    settleTimer = setTimeout(() => {
      if (frame) {
        cancelAnimationFrame(frame);
        update();
      }
      offset = hovering || hasKeyboardFocus() || offset <= bar.offsetHeight / 2 ? 0 : bar.offsetHeight;
      render(true);
    }, 150);
  }, { passive: true });
  document.addEventListener("pointerdown", (event) => {
    keyboardInput = false;
    if (event.pointerType !== "mouse") hovering = false;
    render();
  }, { passive: true, capture: true });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Tab") {
      keyboardInput = true;
      render();
    }
  }, true);
  document.addEventListener("pointermove", (event) => {
    if (event.pointerType !== "mouse") return;
    const next = event.clientY >= window.innerHeight - bar.offsetHeight / 2;
    if (next === hovering) return;
    hovering = next;
    offset = 0;
    render(true);
  }, { passive: true });
  document.addEventListener("pointerout", (event) => {
    if (event.relatedTarget !== null || !hovering) return;
    hovering = false;
    offset = 0;
    render(true);
  });
  bar.addEventListener("focusin", () => {
    if (!keyboardInput) return;
    offset = 0;
    previousY = scrollPosition();
    render();
  });
  window.addEventListener("resize", update);
  window.addEventListener("pageshow", () => {
    hovering = false;
    offset = 0;
    previousY = scrollPosition();
    render();
  });
  new ResizeObserver(() => {
    document.documentElement.style.setProperty("--footer-ui-height", `${bar.offsetHeight}px`);
  }).observe(bar);
  // brings the footer back, e.g. when a build finishes while it is hidden
  return { show: () => { offset = 0; render(true); } };
}

function initDropdownMenu(toggle, menu, items) {
  menu.replaceChildren(...items.map((item) => {
    const button = el("button", { type: "button", class: "dropdownMenuItem", text: item.icon ? `${item.icon} ${item.text}` : item.text });
    if (item.attrs) for (const [k, v] of Object.entries(item.attrs)) button.setAttribute(k, v);
    button.addEventListener("click", (event) => {
      event.preventDefault();
      closeMenu();
      item.onClick(event);
    });
    return button;
  }));
  document.body.appendChild(menu);

  const positionMenu = () => {
    const btnRect = toggle.getBoundingClientRect();
    const buttonBorder = parseFloat(getComputedStyle(toggle).borderBottomWidth) || 0;
    const menuTop = btnRect.bottom - buttonBorder;
    const bar = document.querySelector(".bottom");
    const footerBorder = bar ? parseFloat(getComputedStyle(bar).borderTopWidth) || 0 : 0;
    const menuBottom = Math.min(bar ? bar.getBoundingClientRect().top + footerBorder : window.innerHeight, window.innerHeight);
    menu.style.top = `${menuTop}px`;
    menu.style.right = `${window.innerWidth - btnRect.right}px`;
    menu.style.maxHeight = `${Math.max(menuBottom - menuTop, 0)}px`;
  };
  const closeMenu = () => {
    menu.classList.remove("open");
    toggle.setAttribute("aria-expanded", "false");
  };
  toggle.addEventListener("click", (event) => {
    event.stopPropagation();
    const isOpen = menu.classList.contains("open");
    if (!isOpen) positionMenu();
    menu.classList.toggle("open");
    toggle.setAttribute("aria-expanded", String(!isOpen));
  });
  document.addEventListener("click", (event) => {
    if (!toggle.contains(event.target) && !menu.contains(event.target)) closeMenu();
  });
  for (const name of ["resize", "footerpositionchange", "headerpositionchange"]) {
    window.addEventListener(name, () => { if (menu.classList.contains("open")) positionMenu(); });
  }
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && menu.classList.contains("open")) {
      closeMenu();
      toggle.focus();
    }
  });
}

const toasts = { queue: [], showing: false, container: null };

function toast(text, duration = 3000) {
  if (!text || !text.trim()) return;
  toasts.queue.push({ text, duration });
  if (!toasts.showing) nextToast();
}

function nextToast() {
  if (!toasts.queue.length) {
    toasts.showing = false;
    return;
  }
  toasts.showing = true;
  const { text, duration } = toasts.queue.shift();
  if (!toasts.container) {
    toasts.container = el("div", { class: "toast-container", "aria-live": "polite", "aria-atomic": "false" });
    document.body.appendChild(toasts.container);
  }
  const item = el("div", { class: "toast", role: "status", text });
  item.style.bottom = "0rem";
  toasts.container.appendChild(item);
  void item.offsetHeight;
  item.classList.add("toast-visible");
  setTimeout(() => {
    item.classList.remove("toast-visible");
    setTimeout(() => {
      item.remove();
      nextToast();
    }, 400);
  }, duration);
}

function initTheme() {
  const STORAGE_KEY = "theme-preference";
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const valid = (v) => v === "light" || v === "dark";
  let preference = null;
  try { preference = localStorage.getItem(STORAGE_KEY); } catch (e) { /* storage blocked */ }
  if (!valid(preference)) preference = null;
  const effective = (p) => (valid(p) ? p : media.matches ? "dark" : "light");
  const store = (p) => {
    try { if (valid(p)) localStorage.setItem(STORAGE_KEY, p); else localStorage.removeItem(STORAGE_KEY); } catch (e) { /* storage blocked */ }
  };
  const apply = (p) => document.documentElement.setAttribute("data-theme", effective(p));
  apply(preference);
  $("themeToggle").addEventListener("click", () => {
    preference = effective(preference) === "dark" ? "light" : "dark";
    store(preference);
    apply(preference);
  });
  media.addEventListener("change", () => {
    preference = null;
    store(null);
    apply(null);
  });
}

// language
function loadLang() {
  try {
    const saved = localStorage.getItem("mcfont-lang");
    if (saved && STRINGS[saved]) return saved;
  } catch (e) { /* storage blocked */ }
  return (navigator.languages || [navigator.language || ""]).some((l) => /^zh/i.test(l)) ? "zh-TW" : "en";
}

function setLang(lang) {
  S.lang = lang;
  try { localStorage.setItem("mcfont-lang", lang); } catch (e) { /* storage blocked */ }
  renderAll();
}

function applyTexts() {
  document.documentElement.lang = S.lang;
  for (const e of document.querySelectorAll("[data-t]")) e.textContent = t(e.dataset.t);
  for (const e of document.querySelectorAll("[data-ph]")) e.placeholder = t(e.dataset.ph);
  for (const e of document.querySelectorAll("[data-aria]")) e.setAttribute("aria-label", t(e.dataset.aria));
  for (const e of document.querySelectorAll("[data-title]")) e.title = t(e.dataset.title);
  for (const e of document.querySelectorAll("#langMenu [data-lang]")) {
    if (e.dataset.lang === S.lang) e.setAttribute("aria-current", "true"); else e.removeAttribute("aria-current");
  }
}

// 1. version
function renderVersion() {
  const s = S.settings;
  $("use-local").checked = S.useLocal;
  $("game-path").hidden = !S.useLocal;
  $("version").disabled = S.useLocal;
  $("snapshots").disabled = S.useLocal;
  if (document.activeElement !== $("game-path")) $("game-path").value = s.game_path || "";
  renderVersions();
  renderGameStatus();
}

function renderVersions() {
  const items = [];
  const seen = new Set();
  const add = (v, label) => { if (!seen.has(v)) { seen.add(v); items.push([v, label]); } };
  if (S.versions) {
    add("latest", t("latestRelease", S.versions.latest.release));
    add("latest-snapshot", t("latestSnapshot", S.versions.latest.snapshot));
    for (const v of S.versions.versions) add(v.id, v.type === "snapshot" ? `${v.id} (${t("snapshot")})` : v.id);
  } else {
    add("latest", t("latestRelease", S.game && S.settings.version === "latest" ? S.game.game.version : "..."));
  }
  add(S.settings.version, S.settings.version);
  fillSelect($("version"), items, S.settings.version);
  const err = $("versions-error");
  err.hidden = !(S.versionsError && !S.useLocal);
  err.textContent = S.versionsError ? t("versionListFailed", S.versionsError) : "";
}

function renderGameStatus() {
  const box = $("game-status");
  let parts = [];
  if (S.useLocal && !S.settings.game_path) parts = [tag("todo"), t("needLocalPath")];
  else if (S.loadingGame) parts = [tag("busy"), t("statusLoading")];
  else if (S.gameError) parts = [tag("error"), t("statusError", S.gameError), el("button", { type: "button", class: "link-btn", text: t("retry"), onclick: loadGame })];
  else if (S.game) {
    const g = S.game.game;
    const from = g.origin === "mojang" ? t("fromMojang") : t("fromComputer", g.origin.replace(/^local:/, ""));
    parts = [tag("ok"), t("statusReady", g.version) + (S.lang.startsWith("zh") ? "" : " ") + from];
  }
  box.hidden = !parts.length;
  box.replaceChildren(...parts);
  const warnings = S.game && !S.loadingGame ? S.game.warnings : [];
  $("game-warnings").hidden = !warnings.length;
  $("game-warnings").textContent = warnings.join(" ");
}

async function loadVersions() {
  if (S.useLocal) return;
  try {
    S.versions = await api("/api/versions", { settings: S.settings, snapshots: $("snapshots").checked });
    S.versionsError = "";
  } catch (e) {
    S.versions = null;
    S.versionsError = e.message;
  }
  renderVersions();
}

async function loadGame() {
  const seq = ++gameSeq;
  if (S.useLocal && !S.settings.game_path) {
    S.game = null;
    S.counts = null;
    S.loadingGame = false;
    S.gameError = "";
    renderAfterGame();
    return;
  }
  S.loadingGame = true;
  S.gameError = "";
  renderGameStatus();
  renderFonts();
  renderFooter();
  try {
    const r = await api("/api/fonts", { settings: S.settings });
    if (seq !== gameSeq) return;
    S.game = r;
  } catch (e) {
    if (seq !== gameSeq) return;
    S.game = null;
    S.counts = null;
    S.gameError = e.message;
  }
  S.loadingGame = false;
  renderAfterGame();
  if (S.game) scheduleCount(0);
}

function renderAfterGame() {
  renderVersion();
  renderFonts();
  renderCharacters();
  renderNames();
  renderFooter();
}

// 2. fonts
function renderFonts() {
  const box = $("fonts");
  $("show-fragments").checked = S.showFragments;
  const missing = missingFonts();
  $("fonts-missing").hidden = !missing.length;
  $("fonts-missing").textContent = missing.length ? t("missingFonts", S.game.game.version, missing.join(", ")) : "";
  if (!S.game) {
    box.replaceChildren(el("p", { text: S.loadingGame ? t("statusLoading") : t("fontsNeedGame") }));
    return;
  }
  const chosen = new Set(selectedFonts());
  const list = S.game.fonts.filter((f) => S.showFragments || !f.include || chosen.has(f.id)).sort(fontOrder);
  box.replaceChildren(...list.map((f) => fontCard(f, chosen.has(f.id))));
}

function fontCard(f, on) {
  const c = on ? countOf(f.id) : null;
  const worst = worstStyle(c);
  const over = !!(worst && worst.over > 0);
  const limit = S.counts ? S.counts.limit : 65535;
  const input = el("input", {
    type: "checkbox", checked: on, disabled: !!f.error && !on,
    onchange: (e) => {
      const set = new Set(selectedFonts());
      e.target.checked ? set.add(f.id) : set.delete(f.id);
      S.settings.fonts = S.game.fonts.map((x) => x.id).filter((id) => set.has(id));
      renderFonts();
      renderNames();
      scheduleCount();
    },
  });
  const meta = [el("code", { text: f.id })];
  if (f.error) meta.push(" · " + t("fontBroken", f.error));
  else if (c) meta.push(" · " + t("fontCharsOf", fmt(c.selected), fmt(c.available)), " · " + t("fontGlyphs", fmt(worst.glyphs), fmt(limit)));
  else meta.push(" · " + t("fontChars", fmt(f.codepoints)));
  const status = on && !f.error ? tag(!c ? "busy" : over ? "over" : "ok") : null;
  const content = el("span", { class: "card-content" }, [
    el("span", { class: "card-head" }, [el("span", { class: "card-title", text: f.family }), status]),
    hasText("desc_" + f.id) ? el("span", { class: "card-text", text: t("desc_" + f.id) }) : null,
    el("span", { class: "card-text card-meta" }, meta),
    over ? el("span", { class: "card-text card-alert" }, [t("overBy", fmt(worst.over), fmt(limit)), " ",
      el("button", { type: "button", class: "link-btn", text: t("fixIt"), onclick: (e) => { e.preventDefault(); openCharacters(f.id); } })]) : null,
  ]);
  return el("label", { class: "card font-card" + (f.error ? " is-broken" : "") }, [input, content]);
}

function refreshNames() {
  clearTimeout(namesTimer);
  namesTimer = setTimeout(async () => {
    try {
      S.game = await api("/api/fonts", { settings: S.settings });
    } catch (e) { return; }
    renderFonts();
    renderNames();
    renderCharacters();
    renderFooter();
  }, 250);
}

// 3. files
function renderFiles() {
  const s = S.settings;
  if (document.activeElement !== $("output")) $("output").value = s.output;
  $("output-full").textContent = t("outputFull", fullPath(s.output));
  const look = { regular: null, bold: "is-bold", italic: "is-italic", bold_italic: "is-bold is-italic" };
  const styles = S.start.styles.flatMap((st) => option("checkbox", s.styles.includes(st), t("style_" + st), null, (on) => {
    s.styles = S.start.styles.filter((x) => (x === st ? on : s.styles.includes(x)));
    scheduleCount();
    renderFooter();
  }, { textClass: look[st] }));
  $("styles").replaceChildren(el("legend", { text: t("styles") }), ...styles, el("p", { class: "hint", text: t("stylesHint") }));
  const formats = S.start.formats.flatMap((fm) => option("checkbox", s.formats.includes(fm), t("fmt_" + fm), t("fmtHint_" + fm), (on) => {
    s.formats = S.start.formats.filter((x) => (x === fm ? on : s.formats.includes(x)));
    renderFooter();
  }));
  $("formats").replaceChildren(el("legend", { text: t("formats") }), ...formats);
}

// characters
function scheduleCount(delay = 350) {
  clearTimeout(countTimer);
  S.counting = true;
  renderFooter();
  countTimer = setTimeout(loadCount, delay);
}

async function loadCount() {
  const seq = ++countSeq;
  if (!S.game || !selectedFonts().length || !S.settings.styles.length) {
    S.counts = null;
    S.counting = false;
    renderFonts();
    renderCharacters();
    renderFooter();
    return;
  }
  S.counting = true;
  try {
    const r = await api("/api/count", { settings: S.settings });
    if (seq !== countSeq) return;
    S.counts = r;
    S.countError = "";
  } catch (e) {
    if (seq !== countSeq) return;
    S.counts = null;
    S.countError = e.message;
  }
  S.counting = false;
  renderFonts();
  renderCharacters();
  renderFooter();
}

function blockSetting(name) {
  const n = name.toLowerCase();
  if ((rules().keep_blocks || []).some((b) => b.toLowerCase() === n)) return "keep";
  if ((rules().exclude_blocks || []).some((b) => b.toLowerCase() === n)) return "exclude";
  return "default";
}

function setBlockSetting(name, value) {
  const n = name.toLowerCase();
  const r = rules();
  r.keep_blocks = (r.keep_blocks || []).filter((b) => b.toLowerCase() !== n);
  r.exclude_blocks = (r.exclude_blocks || []).filter((b) => b.toLowerCase() !== n);
  if (value === "keep") r.keep_blocks.push(name);
  if (value === "exclude") r.exclude_blocks.push(name);
  scheduleCount();
}

function countFont() {
  if (!S.counts || !S.counts.fonts.length) return null;
  let f = S.counts.fonts.find((x) => x.font_id === S.blockFont);
  if (!f) {
    f = overFonts()[0] || [...S.counts.fonts].sort((a, b) => b.available - a.available)[0];
    S.blockFont = f.font_id;
  }
  return f;
}

function openCharacters(fontId) {
  if (fontId) S.blockFont = fontId;
  renderCharacters();
  reveal("d-chars");
}

function renderCharacters() {
  const limit = S.start ? S.start.limit : 65535;
  $("chars-lead").textContent = t("charsLead", fmt(limit));
  const note = $("chars-note");
  const over = overFonts().length > 0;
  note.hidden = !S.counts;
  note.className = "tag" + (over ? " is-over" : "");
  note.textContent = over ? t("tagOver") : t("tagOk");
  $("exclude-unassigned").checked = !!rules().exclude_unassigned;
  const r = rules();
  const other = [];
  if (r.only_blocks) other.push(`only_blocks: ${r.only_blocks.join(", ")}`);
  if (r.only_ranges) other.push(`only_ranges: ${r.only_ranges.join(", ")}`);
  if (r.exclude_ranges && r.exclude_ranges.length) other.push(`exclude_ranges: ${r.exclude_ranges.join(", ")}`);
  if (r.exclude_providers && r.exclude_providers.length) other.push(`exclude_providers: ${r.exclude_providers.join(", ")}`);
  if (Object.keys(S.settings.characters_per_font || {}).length) other.push(`characters_per_font: ${Object.keys(S.settings.characters_per_font).join(", ")}`);
  $("other-rules").textContent = other.length ? t("otherRules", other.join(" · ")) : "";
  $("other-rules").hidden = !other.length;
  renderSuggestions();
  renderPlanes();
  const fonts = S.counts ? S.counts.fonts : [];
  const current = countFont();
  fillSelect($("block-font"), fonts.map((f) => [f.font_id, displayFamily(f.font_id, f.family)]), current ? current.font_id : "");
  $("block-font").hidden = fonts.length < 2;
  fillSelect($("block-view"), [["all", t("viewAll")], ["included", t("viewIncluded")], ["excluded", t("viewExcluded")],
    ["changed", t("viewChanged")]], S.blockView);
  renderBlocks();
}

function renderSuggestions() {
  const box = $("suggestions");
  box.replaceChildren();
  if (S.countError) {
    box.append(el("div", { class: "callout is-alert" }, el("p", { text: t("error", S.countError) })));
  }
  const limit = S.counts ? S.counts.limit : 65535;
  for (const f of overFonts()) {
    const worst = worstStyle(f);
    const items = f.suggestions.map((s) => {
      let label;
      if (s.exclude_planes) label = t("suggestPlanes", s.exclude_planes.join(", "), fmt(s.frees));
      else {
        const names = s.exclude_blocks.slice(0, 3).join(", ") + (s.exclude_blocks.length > 3 ? ", ..." : "");
        label = t("suggestBlocks", s.exclude_blocks.length, names, fmt(s.frees));
      }
      return el("li", {}, [label, " ", el("button", { type: "button", class: "link-btn", text: t("apply"), onclick: () => applySuggestion(s) })]);
    });
    box.append(el("div", { class: "callout is-alert" }, [
      el("p", { text: t("tooManyNotice", displayFamily(f.font_id, f.family), fmt(worst.glyphs), fmt(worst.glyphs - limit)) }),
      items.length ? el("ul", {}, items) : null,
    ]));
  }
  box.hidden = !box.children.length;
}

function applySuggestion(s) {
  const r = rules();
  if (s.exclude_planes) r.exclude_planes = [...new Set([...r.exclude_planes, ...s.exclude_planes])].sort((a, b) => a - b);
  else for (const b of s.exclude_blocks) if (blockSetting(b) !== "exclude") {
    r.keep_blocks = (r.keep_blocks || []).filter((x) => x.toLowerCase() !== b.toLowerCase());
    r.exclude_blocks.push(b);
  }
  renderCharacters();
  scheduleCount();
}

function renderPlanes() {
  const f = countFont();
  const planes = f ? f.planes : [];
  const excluded = new Set(rules().exclude_planes);
  $("planes").replaceChildren(...planes.flatMap((p) => option("checkbox", !excluded.has(p.plane),
    `${t("planeLabel", p.plane)} · ${t(PLANE_NAMES[p.plane] || "planeHintPrivate")}`,
    `${t(PLANE_HINTS[p.plane] || "planeHintPrivate")} · ${t("planeCount", fmt(p.selected), fmt(p.codepoints))}`,
    (on) => {
      const set = new Set(rules().exclude_planes);
      on ? set.delete(p.plane) : set.add(p.plane);
      rules().exclude_planes = [...set].sort((a, b) => a - b);
      renderPlanes();
      scheduleCount();
    })));
  $("planes-box").hidden = !planes.length;
}

function renderBlocks() {
  const box = $("blocks");
  const f = countFont();
  if (!f) {
    box.replaceChildren(el("div", { class: "table-empty", text: S.counting ? t("stCounting") : t("blocksNeedGame") }));
    return;
  }
  const q = S.blockFilter.trim().toLowerCase();
  const rows = [];
  for (const b of f.blocks) {
    const setting = blockSetting(b.block);
    if (S.blockView === "included" && !b.selected) continue;
    if (S.blockView === "excluded" && b.selected) continue;
    if (S.blockView === "changed" && setting === "default") continue;
    if (q && !b.block.toLowerCase().includes(q) && !b.first.toLowerCase().includes(q)) continue;
    const pick = el("select", { "aria-label": b.block, onchange: (e) => setBlockSetting(b.block, e.target.value) });
    fillSelect(pick, [["default", t("settingDefault")], ["keep", t("settingKeep")], ["exclude", t("settingExclude")]], setting);
    const range = `${b.first.replace("U+", "")}..${b.last.replace("U+", "")}`;
    const counts = `${fmt(b.selected)} / ${fmt(b.codepoints)}`;
    rows.push(el("tr", { class: b.selected ? "" : "is-out" }, [
      el("td", { title: b.block }, el("span", { class: "clamp", text: b.block })),
      el("td", { class: "mono", text: range, title: range }),
      el("td", { class: "num", text: fmt(b.glyphs) }),
      el("td", { class: "num", text: counts, title: counts }),
      el("td", {}, pick),
    ]));
  }
  const scroll = box.scrollTop;
  box.replaceChildren(rows.length ? el("table", {}, [
    el("thead", {}, el("tr", {}, [el("th", { text: t("colBlock") }), el("th", { text: t("colRangeHex") }),
      el("th", { class: "num", text: t("colGlyphs") }), el("th", { class: "num", text: t("colIncluded") }),
      el("th", { text: t("colSetting") })])),
    el("tbody", {}, rows),
  ]) : el("div", { class: "table-empty", text: t("noBlocks") }));
  box.scrollTop = scroll;
}

// appearance
function renderLook() {
  const s = S.settings;
  const radio = (name, value, current, title, hint, onPick) => option("radio", value === current, title, hint, () => onPick(value), { name });
  $("line-metrics").replaceChildren(el("legend", { text: t("lineMetrics") }),
    ...radio("line-metrics", "centered", s.line_metrics, t("lmCentered"), t("lmCenteredHint"), (v) => { s.line_metrics = v; }),
    ...radio("line-metrics", "game", s.line_metrics, t("lmGame"), t("lmGameHint"), (v) => { s.line_metrics = v; }),
    ...radio("line-metrics", "fit", s.line_metrics, t("lmFit"), t("lmFitHint"), (v) => { s.line_metrics = v; }));
  $("center-glyphs").checked = !!s.center_glyphs;
  const sizes = [[8, t("size8")], [10, t("size10")]];
  if (![8, 10].includes(s.em_pixels)) sizes.push([s.em_pixels, String(s.em_pixels)]);
  fillSelect($("em-pixels"), sizes, s.em_pixels);
  const shifts = [[0, t("shift0")], [1, t("shift1")], [2, t("shift2")]];
  if (s.italic_shift > 2) shifts.push([s.italic_shift, String(s.italic_shift)]);
  fillSelect($("italic-shift"), shifts, s.italic_shift);
  $("shadow").checked = !!s.shadow;
  const box = $("shadow-box");
  box.replaceChildren(...[...box.children].slice(0, 3));  // the legend, the checkbox and its hint
  if (s.shadow) {
    box.append(
      ...radio("shadow-format", "colrv1", s.shadow_format, t("shadowV1"), t("shadowV1Hint"), (v) => { s.shadow_format = v; scheduleCount(); }),
      ...radio("shadow-format", "colrv0", s.shadow_format, t("shadowV0"), t("shadowV0Hint"), (v) => { s.shadow_format = v; scheduleCount(); }),
    );
  }
  $("force-unicode").checked = !!s.force_unicode_font;
  $("japanese").checked = !!s.japanese_glyph_variants;
}

// names
function textMode(key) {
  const v = S.settings[key];
  if (key === "font_version") return S.modes[key] || (v ? "custom" : "auto");
  return S.modes[key] || (v === null || v === undefined ? "auto" : v === "" ? "none" : "custom");
}

function renderNames() {
  const s = S.settings;
  const box = $("names");
  const fonts = S.game ? S.game.fonts.filter((f) => !f.include || selectedFonts().includes(f.id)).sort(fontOrder) : [];
  const focused = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.font : null;
  if (!focused) {
    box.replaceChildren(...fonts.flatMap((f) => [
      el("label", { for: "name-" + f.id }, el("code", { text: f.id })),
      el("input", {
        type: "text", id: "name-" + f.id, value: s.family_names[f.id] || "", placeholder: f.family, spellcheck: "false", "data-font": f.id,
        onchange: (e) => {
          const v = e.target.value.trim();
          if (v) s.family_names[f.id] = v; else delete s.family_names[f.id];
          refreshNames();
        },
      }),
    ]));
    box.closest("fieldset").hidden = !fonts.length;
  }
  if (document.activeElement !== $("family-suffix")) $("family-suffix").value = s.family_suffix || "";
}

// metadata
const AUTO_TEXT = ["copyright", "license", "license_url"];   // automatic, none or own text
const PLAIN_TEXT = ["designer", "designer_url", "manufacturer", "manufacturer_url", "description"];

function renderMeta() {
  const s = S.settings;
  for (const key of [...AUTO_TEXT, "font_version"]) {
    const m = textMode(key);
    const input = $(key + "-text");
    const choices = key === "font_version" ? [["auto", t("versionAuto")], ["custom", t("custom")]]
      : [["auto", t("auto")], ["none", t("none")], ["custom", t("custom")]];
    fillSelect($(key + "-mode"), choices, m);
    input.hidden = m !== "custom";
    if (document.activeElement !== input) input.value = m === "custom" ? s[key] || "" : "";
  }
  for (const key of PLAIN_TEXT) if (document.activeElement !== $(key)) $(key).value = s[key] || "";
}

// downloads
function launcherMode() {
  const d = S.settings.launcher_dir;
  return S.modes.launcher || (d === "auto" || d === "off" ? d : "folder");
}

function renderDownloads() {
  const s = S.settings;
  const mode = launcherMode();
  fillSelect($("launcher-mode"), [["auto", t("launcherAuto")], ["off", t("launcherOff")], ["folder", t("launcherFolder")]], mode);
  $("launcher-path").hidden = mode !== "folder";
  if (mode === "folder" && document.activeElement !== $("launcher-path")) {
    $("launcher-path").value = ["auto", "off"].includes(s.launcher_dir) ? "" : s.launcher_dir;
  }
  const found = S.start && S.start.launcher_dir;
  $("launcher-hint").textContent = mode === "auto" ? (found ? t("launcherFound", found) : t("launcherNotFound"))
    : mode === "off" ? t("launcherOffHint") : t("launcherFolderHint");
  if (document.activeElement !== $("cache-dir")) $("cache-dir").value = s.cache_dir || "";
  $("offline").checked = !!s.offline;
  if (document.activeElement !== $("resource-packs")) $("resource-packs").value = (s.resource_packs || []).join("\n");
}

// advanced
function renderAdvanced() {
  const s = S.settings;
  $("embedded-bitmaps").value = (s.embedded_bitmaps || []).join(", ");
  fillSelect($("render-profile"), [["auto", t("rpAuto")], ["1.13-1.21.1", t("rp_1.13-1.21.1")], ["1.21.2+", t("rp_1.21.2+")]], s.render_profile);
  $("jobs").value = s.jobs || 0;
  $("build-date").value = s.build_date || "";
  $("units-per-pixel").value = s.units_per_pixel;
}

// footer
function fileCount() {
  return selectedFonts().length * S.settings.styles.length * S.settings.formats.length;
}

// what keeps the fonts from being built as [tag, text, where to look, font], or null
function blocker() {
  if (S.useLocal && !S.settings.game_path) return ["todo", t("stNeedPath"), "s-version"];
  if (S.loadingGame || (!S.game && !S.gameError)) return ["busy", t("stLoading")];
  if (!S.game) return ["error", t("stGameError"), "s-version"];
  if (!selectedFonts().length) return ["todo", t("stNoFonts"), "s-fonts"];
  if (!S.settings.styles.length) return ["todo", t("stNoStyles"), "s-files"];
  if (!S.settings.formats.length) return ["todo", t("stNoFormats"), "s-files"];
  if (S.counting || (!S.counts && !S.countError)) return ["busy", t("stCounting")];
  if (S.countError) return ["error", t("error", S.countError), "d-chars"];
  const over = overFonts();
  if (over.length) return ["over", t("stTooMany", displayFamily(over[0].font_id, over[0].family)), "d-chars", over[0].font_id];
  return null;
}

function renderFooter() {
  if (!S.settings) return;
  const job = S.job;
  const running = job.state === "running";
  const current = S.builtWith === JSON.stringify(S.settings);
  const block = blocker();
  let kind, text, action = null;
  if (running) {
    kind = "busy";
    text = job.total ? t("stRunning", Math.min(job.done + 1, job.total), job.total, duration(job.elapsed)) : t("stStarting");
  } else if (current && job.state === "done") {
    kind = "ok"; text = t("stDone", job.files.length, duration(job.elapsed));
  } else if (current && job.state === "too-many-glyphs") {
    kind = "over"; text = t("stTooManyBuild"); action = [t("showLog"), () => reveal("s-log")];
  } else if (current && job.state === "error") {
    kind = "error"; text = t("stFailed", job.error); action = [t("showLog"), () => reveal("s-log")];
  } else if (block) {
    [kind, text] = block;
    if (block[2]) action = [t("show"), () => (block[2] === "d-chars" ? openCharacters(block[3]) : reveal(block[2]))];
  } else {
    const n = fileCount();
    kind = "ok";
    text = n === 1 ? t("stReadyOne", S.settings.output) : t("stReady", fmt(n), S.settings.output);
  }
  $("footerNotes").replaceChildren(tag(kind), text,
    action ? el("button", { type: "button", class: "link-btn", text: action[0], onclick: action[1] }) : "");

  const btn = $("build");
  btn.disabled = running || !!block;
  btn.textContent = running ? t("building") : t("build");
  $("open-folder").hidden = job.state !== "done";
  $("progress").hidden = !running;
  $("progress").firstChild.style.width = (job.total ? Math.round((job.done / job.total) * 100) : 3) + "%";
}

// build
async function startBuild() {
  S.job = { state: "running", log: [], total: 0, done: 0, elapsed: 0, error: "", files: [] };
  S.builtWith = JSON.stringify(S.settings);
  showLog([]);
  renderFooter();
  try {
    await api("/api/build", { settings: S.settings });
  } catch (e) {
    S.job.state = "error";
    S.job.error = e.message;
    S.job.log = [e.message];
    showLog(S.job.log);
    renderFooter();
    return;
  }
  poll(0);
}

async function poll(since) {
  let j;
  try {
    j = await api("/api/build/status", { since });
  } catch (e) {
    setTimeout(() => poll(since), 1500);
    return;
  }
  const job = S.job;
  for (const line of j.log) {
    job.log.push(line);
    const m = line.match(/^\[(\d+)\/(\d+)\]/);
    if (m) { job.done = Number(m[1]); job.total = Number(m[2]); }
    const n = line.match(/^writing (\d+) style/);
    if (n) job.total = Number(n[1]);
  }
  job.state = j.state;
  job.elapsed = j.elapsed;
  job.error = j.error;
  if (j.state === "too-many-glyphs" && j.error) job.log.push(j.error);
  showLog(job.log);
  if (j.state === "running") {
    renderFooter();
    setTimeout(() => poll(j.log_total), 600);
    return;
  }
  job.files = j.files;
  renderFooter();
  if (footer) footer.show();
  if (j.state === "done") {
    S.builtAt = Date.now();
    renderPreview();
  }
}

// log
function showLog(lines) {
  const log = $("log");
  const following = log.hidden || log.scrollHeight - log.scrollTop - log.clientHeight < 8;
  log.textContent = lines.join("\n");
  for (const id of ["s-log", "log-rule", "log"]) $(id).hidden = !lines.length;
  if (following) log.scrollTop = log.scrollHeight;
}

// preview
function renderPreview() {
  const files = S.job.state === "done" ? S.job.files : [];
  $("preview-lead").textContent = files.length ? t("previewLead") : t("previewEmpty");
  $("preview-tools").hidden = !files.length;
  $("preview").hidden = !files.length;
  if (!files.length) return;
  fillSelect($("preview-size"), [8, 16, 24, 32].map((n) => [n, `${n} px`]), S.previewSize);
  fillSelect($("preview-bg"), [["white", t("bgWhite")], ["black", t("bgBlack")]], S.previewBg);
  if (document.activeElement !== $("preview-text")) $("preview-text").value = S.previewText ?? t("sample");
  const box = $("preview");
  box.classList.toggle("is-dark", S.previewBg === "black");
  const rank = (name) => (name.endsWith(".ttf") ? 0 : name.endsWith(".otf") ? 1 : 2);
  const pick = new Map();
  for (const f of files) {
    const key = `${f.family}|${f.style}`;
    if (!pick.has(key) || rank(f.file) < rank(pick.get(key).file)) pick.set(key, f);
  }
  const items = [...pick.values()].sort((a, b) => a.family.localeCompare(b.family) || STYLE_ORDER.indexOf(a.style) - STYLE_ORDER.indexOf(b.style));
  const text = S.previewText ?? t("sample");
  const missing = [...new Set([...text].map((c) => c.codePointAt(0).toString(16)))].sort().join(".");
  const inUse = new Set();
  const face = (family, url) => {
    inUse.add(family);
    if (!faces.has(family)) faces.set(family, new FontFace(family, `url(${url})`).load().then((ff) => document.fonts.add(ff) && ff).catch(() => null));
  };
  const samples = items.map((f) => {
    // a new build gets new names, so the browser never shows the fonts of an older one
    const family = `preview-${S.builtAt}-${f.file.replace(/[^A-Za-z0-9]/g, "-")}`;
    const url = `/files/${encodeURIComponent(f.file)}?t=${encodeURIComponent(TOKEN)}&v=${S.builtAt}`;
    face(family, url);
    // characters the font lacks are drawn as its own box, never with another font
    const boxes = `${family}-missing-${missing}`;
    face(boxes, `${url}&missing=${missing}`);
    const sample = el("div", { class: "sample", text });
    sample.style.fontFamily = `"${family}", "${boxes}"`;
    sample.style.fontSize = S.previewSize + "px";
    sample.style.lineHeight = "normal";
    return el("div", { class: "preview-item" }, [el("span", { class: "preview-label", text: `${f.family} ${t("style_" + f.style)}` }), sample]);
  });
  box.replaceChildren(...samples);
  for (const [family, loading] of faces) {  // let go of fonts no longer shown
    if (inUse.has(family)) continue;
    faces.delete(family);
    loading.then((ff) => ff && document.fonts.delete(ff));
  }
}

// settings
async function saveSettings() {
  try {
    const r = await api("/api/settings/write", { settings: S.settings });
    const name = "mcfont.yaml";
    const a = el("a", { href: URL.createObjectURL(new Blob([r.text], { type: "text/yaml" })), download: name });
    document.body.append(a);
    a.click();
    a.remove();
    toast(t("settingsSaved", name));
  } catch (e) { toast(t("error", e.message)); }
}

async function openSettingsFile(file) {
  try {
    const r = await api("/api/settings/read", { text: await file.text() });
    setSettings(r.settings);
    toast(t("settingsLoaded", file.name));
  } catch (e) { toast(t("error", e.message)); }
}

function setSettings(settings) {
  S.settings = clone(settings);
  S.modes = {};
  S.useLocal = !!S.settings.game_path;
  renderAll();
  loadVersions();
  loadGame();
}

// wiring
function renderAll() {
  applyTexts();
  renderVersion();
  renderFonts();
  renderFiles();
  renderCharacters();
  renderLook();
  renderNames();
  renderMeta();
  renderDownloads();
  renderAdvanced();
  renderFooter();
  renderPreview();
}

function bind() {
  const s = () => S.settings;
  for (const form of document.querySelectorAll("form")) form.addEventListener("submit", (e) => e.preventDefault());

  initTheme();
  footer = initScrollFooter();
  initScrollHeader();
  initDropdownMenu($("langToggle"), $("langMenu"), LANGUAGES.map(({ lang, name, icon }) => ({
    icon, text: name, attrs: { "data-lang": lang }, onClick: () => setLang(lang),
  })));

  // 1. version
  $("version").addEventListener("change", (e) => { s().version = e.target.value; loadGame(); });
  $("snapshots").addEventListener("change", loadVersions);
  $("use-local").addEventListener("change", (e) => {
    S.useLocal = e.target.checked;
    s().game_path = S.useLocal ? $("game-path").value.trim() || null : null;
    renderVersion();
    if (S.useLocal) $("game-path").focus();
    else loadVersions();
    loadGame();
  });
  $("game-path").addEventListener("change", (e) => { s().game_path = e.target.value.trim() || null; loadGame(); });

  // 2. fonts
  $("show-fragments").addEventListener("change", (e) => { S.showFragments = e.target.checked; renderFonts(); });

  // 3. files
  $("output").addEventListener("input", (e) => {
    s().output = e.target.value.trim() || "output";
    $("output-full").textContent = t("outputFull", fullPath(s().output));
    renderFooter();
  });
  $("output").addEventListener("change", (e) => { if (!e.target.value.trim()) e.target.value = s().output; });

  // characters
  $("exclude-unassigned").addEventListener("change", (e) => { rules().exclude_unassigned = e.target.checked; scheduleCount(); });
  $("block-font").addEventListener("change", (e) => { S.blockFont = e.target.value; renderCharacters(); });
  $("block-filter").addEventListener("input", (e) => { S.blockFilter = e.target.value; renderBlocks(); });
  $("block-view").addEventListener("change", (e) => { S.blockView = e.target.value; renderBlocks(); });
  $("reset-chars").addEventListener("click", () => {
    s().characters = clone(S.start.defaults.characters);
    s().characters_per_font = {};
    renderCharacters();
    scheduleCount();
  });

  // appearance and game options
  $("em-pixels").addEventListener("change", (e) => { s().em_pixels = Number(e.target.value); });
  $("italic-shift").addEventListener("change", (e) => { s().italic_shift = Number(e.target.value); });
  $("center-glyphs").addEventListener("change", (e) => { s().center_glyphs = e.target.checked; });
  $("shadow").addEventListener("change", (e) => { s().shadow = e.target.checked; renderLook(); refreshNames(); scheduleCount(); });
  $("force-unicode").addEventListener("change", (e) => { s().force_unicode_font = e.target.checked; refreshNames(); scheduleCount(); });
  $("japanese").addEventListener("change", (e) => { s().japanese_glyph_variants = e.target.checked; refreshNames(); scheduleCount(); });

  // names
  $("family-suffix").addEventListener("change", (e) => { s().family_suffix = e.target.value.trim(); refreshNames(); });
  // metadata
  for (const key of [...AUTO_TEXT, "font_version"]) {
    $(key + "-mode").addEventListener("change", (e) => {
      const next = e.target.value;
      S.modes[key] = next;
      s()[key] = next === "auto" ? null : next === "none" ? "" : $(key + "-text").value;
      renderMeta();
      if (next === "custom") $(key + "-text").focus();
    });
    $(key + "-text").addEventListener("input", (e) => {
      s()[key] = key === "font_version" ? e.target.value.trim() || null : e.target.value;
    });
  }
  for (const key of PLAIN_TEXT) $(key).addEventListener("input", (e) => { s()[key] = e.target.value.trim(); });

  // downloads
  $("launcher-mode").addEventListener("change", (e) => {
    const m = e.target.value;
    S.modes.launcher = m;
    const path = $("launcher-path").value.trim();
    s().launcher_dir = m === "folder" ? path || "auto" : m;
    renderDownloads();
    if (m === "folder") $("launcher-path").focus();
    if (m !== "folder" || path) loadGame();
  });
  $("launcher-path").addEventListener("change", (e) => { s().launcher_dir = e.target.value.trim() || "auto"; loadGame(); });
  $("cache-dir").addEventListener("change", (e) => { s().cache_dir = e.target.value.trim() || null; loadVersions(); loadGame(); });
  $("offline").addEventListener("change", (e) => { s().offline = e.target.checked; loadVersions(); loadGame(); });
  $("resource-packs").addEventListener("change", (e) => {
    s().resource_packs = e.target.value.split("\n").map((x) => x.trim()).filter(Boolean);
    loadGame();
  });

  // advanced
  $("embedded-bitmaps").addEventListener("change", (e) => {
    s().embedded_bitmaps = e.target.value.split(/[,\s]+/).map((x) => parseInt(x, 10)).filter((n) => n > 0);
  });
  $("render-profile").addEventListener("change", (e) => { s().render_profile = e.target.value; loadGame(); });
  $("jobs").addEventListener("change", (e) => { s().jobs = Math.max(0, parseInt(e.target.value, 10) || 0); });
  $("build-date").addEventListener("change", (e) => { s().build_date = e.target.value.trim() || null; });
  $("units-per-pixel").addEventListener("change", (e) => { s().units_per_pixel = Math.max(2, parseInt(e.target.value, 10) || 120); });

  // settings file
  $("open-settings").addEventListener("click", () => $("settings-file").click());
  $("settings-file").addEventListener("change", (e) => { if (e.target.files[0]) openSettingsFile(e.target.files[0]); e.target.value = ""; });
  $("save-settings").addEventListener("click", saveSettings);
  $("reset-settings").addEventListener("click", () => {
    if (!window.confirm(t("confirmReset"))) return;
    setSettings(S.start.defaults);
    toast(t("settingsReset"));
  });

  // footer and preview
  $("build").addEventListener("click", startBuild);
  $("open-folder").addEventListener("click", async () => {
    try { await api("/api/open-folder", { settings: s() }); } catch (e) { toast(t("error", e.message)); }
  });
  let previewTimer = null;
  $("preview-text").addEventListener("input", (e) => {
    S.previewText = e.target.value;
    clearTimeout(previewTimer);
    previewTimer = setTimeout(renderPreview, 250);
  });
  $("preview-size").addEventListener("change", (e) => { S.previewSize = Number(e.target.value); renderPreview(); });
  $("preview-bg").addEventListener("change", (e) => { S.previewBg = e.target.value; renderPreview(); });
}

async function init() {
  S.lang = loadLang();
  bind();
  applyTexts();
  try {
    S.start = await api("/api/start");
  } catch (e) {
    $("footerNotes").replaceChildren(tag("error"), t("error", e.message));
    $("build").disabled = true;
    return;
  }
  $("app-version").textContent = "v" + S.start.app_version;
  S.settings = clone(S.start.settings);
  S.useLocal = !!S.settings.game_path;
  renderAll();
  loadVersions();
  loadGame();
}

document.addEventListener("DOMContentLoaded", init);