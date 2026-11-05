/* MT5 AI Bot dashboard (vanilla JS). Consumes only /api/v1 (see docs/CONTRACT.md §2). */
'use strict';

const API = '/api/v1';
const TZ = 'Europe/Chisinau';
const TOKEN_KEY = 'mt5bot.adminToken';
const THEME_KEY = 'mt5bot.theme';
const DEFAULT_TFS = ['M1', 'M5', 'M15', 'H1'];
const TF_SECONDS = { M1: 60, M5: 300, M15: 900, M30: 1800, H1: 3600, H4: 14400, D1: 86400 };
const ALL_TFS = Object.keys(TF_SECONDS);
const DAYS = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN'];
const DAY_RO = { MON: 'Lu', TUE: 'Ma', WED: 'Mi', THU: 'Jo', FRI: 'Vi', SAT: 'Sâ', SUN: 'Du' };

const S = {
  status: null, statusOkAt: 0, statusLatencyMs: null,
  settings: null,
  symbols: [],
  quotes: {},            // symbol -> {bid, ask, spread_points, ageBase, recvAt}
  positions: null,
  route: { page: 'overview', arg: null },
  sse: { es: null, connected: false, hadError: false },
};

/* ---------------- utils ---------------- */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ESC[c]);
const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const enc = encodeURIComponent;

function debounce(fn, ms) {
  let t = 0;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

const dtFmt = new Intl.DateTimeFormat('ro-RO', { timeZone: TZ, day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
const dtShortFmt = new Intl.DateTimeFormat('ro-RO', { timeZone: TZ, day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
const timeFmt = new Intl.DateTimeFormat('ro-RO', { timeZone: TZ, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
const hmFmt = new Intl.DateTimeFormat('ro-RO', { timeZone: TZ, hour: '2-digit', minute: '2-digit', hour12: false });
const dayFmt = new Intl.DateTimeFormat('ro-RO', { timeZone: TZ, day: '2-digit', month: 'short' });
const yearFmt = new Intl.DateTimeFormat('ro-RO', { timeZone: TZ, year: 'numeric' });
const monthFmt = new Intl.DateTimeFormat('ro-RO', { timeZone: TZ, month: 'short', year: 'numeric' });

function toDate(v) {
  if (v === null || v === undefined || v === '') return null;
  if (v instanceof Date) return v;
  if (typeof v === 'number') return new Date(v < 1e12 ? v * 1000 : v);
  let s = String(v);
  if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(s)) s = s.replace(' ', 'T') + 'Z'; // naive => UTC
  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d;
}
const fmtDT = (v) => { const d = toDate(v); return d ? dtFmt.format(d) : '—'; };
const fmtDTs = (v) => { const d = toDate(v); return d ? dtShortFmt.format(d) : '—'; };
const fmtTime = (v) => { const d = toDate(v); return d ? timeFmt.format(d) : '—'; };

function fmtDur(sec) {
  if (!isNum(sec)) return '—';
  sec = Math.max(0, Math.round(sec));
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  if (d > 0) return `${d}z ${h}h ${String(m).padStart(2, '0')}m`;
  if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`;
  if (m > 0) return `${m}m ${String(s).padStart(2, '0')}s`;
  return `${s}s`;
}
function ago(v) {
  const d = toDate(v);
  if (!d) return '—';
  return 'acum ' + fmtDur((Date.now() - d.getTime()) / 1000);
}
function fmtNum(v, dec = 2) {
  if (!isNum(v)) return '—';
  return v.toLocaleString('ro-RO', { minimumFractionDigits: dec, maximumFractionDigits: dec });
}
function fmtMoney(v, cur) {
  if (!isNum(v)) return '—';
  return fmtNum(v, 2) + (cur ? ' ' + cur : '');
}
function fmtSigned(v, dec = 2) {
  if (!isNum(v)) return '—';
  return (v > 0 ? '+' : '') + fmtNum(v, dec);
}
const pnlCls = (v) => (isNum(v) ? (v > 0 ? 'up' : v < 0 ? 'down' : '') : '');
function fmtPct(v, dec = 0) { return isNum(v) ? (v * 100).toFixed(dec) + '%' : '—'; }

function symInfo(sym) { return S.symbols.find((s) => s.symbol === sym) || null; }
function digitsFor(sym) { const i = symInfo(sym); return i && Number.isInteger(i.digits) ? i.digits : 5; }
function fmtPrice(v, sym) { return isNum(v) ? v.toFixed(digitsFor(sym)) : '—'; }

function dirBadge(d) {
  if (d === 'BUY') return '<span class="badge buy">▲ BUY</span>';
  if (d === 'SELL') return '<span class="badge sell">▼ SELL</span>';
  return `<span class="badge">${esc(d || '—')}</span>`;
}
function sideBadge(d) { return d === 'BUY' ? '<span class="badge buy">BUY</span>' : d === 'SELL' ? '<span class="badge sell">SELL</span>' : `<span class="badge">${esc(d ?? '—')}</span>`; }
function verdictBadge(ai) {
  if (!ai) return '<span class="badge">—</span>';
  if (ai.status === 'DONE' && ai.verdict) {
    const c = ai.verdict === 'AGREE' ? 'ok' : ai.verdict === 'CAUTION' ? 'warn' : 'bad';
    return `<span class="badge ${c}">${esc(ai.verdict)}</span>`;
  }
  const c = ai.status === 'ERROR' ? 'bad' : ai.status === 'PENDING' ? 'info' : '';
  return `<span class="badge ${c}">${esc(ai.status || '—')}</span>`;
}
const SIGNAL_STATUS_CLS = { NEW: 'info', NO_TRADE: '', INTENT_CREATED: 'accent', EXECUTED: 'ok', REJECTED: 'bad', EXPIRED: '' };
const INTENT_STATE_CLS = { REJECTED_RISK: 'bad', QUEUED: 'info', DELIVERED: 'accent', FILLED: 'ok', PAPER_FILLED: 'ok', FAILED: 'bad', UNKNOWN: 'warn' };
const statusBadge = (s, map) => `<span class="badge ${map[s] || ''}">${esc(s || '—')}</span>`;
function modeBadge(m) {
  const c = m === 'DEMO' ? 'warn' : m === 'PAPER' ? 'violet' : m === 'SIGNAL_ONLY' ? 'info' : m === 'LIVE' ? 'bad' : '';
  return `<span class="badge ${c}">${esc(m || '—')}</span>`;
}
function pill(level, text) {
  const c = level === 'ONLINE' ? 'ok' : level === 'DEGRADED' ? 'warn' : level === 'OFFLINE' ? 'bad' : '';
  return `<span class="pill ${c}"><span class="dot"></span>${esc(text || level)}</span>`;
}
function emptyState(title, msg) { return `<div class="empty"><strong>${esc(title)}</strong>${msg ? esc(msg) : ''}</div>`; }
function table(headers, rowsHtml, opts = {}) {
  const th = headers.map((h) => (typeof h === 'string' ? `<th>${esc(h)}</th>` : `<th class="${h.cls || ''}">${esc(h.t)}</th>`)).join('');
  return `<div class="table-wrap"><table class="tbl"><thead><tr>${th}</tr></thead><tbody>${rowsHtml}</tbody>${opts.foot || ''}</table></div>`;
}

/* ---------------- storage / theme ---------------- */
function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
function lsSet(k, v) { try { if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) { /* ignore */ } }
const getToken = () => lsGet(TOKEN_KEY) || '';

function currentTheme() {
  const t = document.documentElement.getAttribute('data-theme');
  if (t === 'light' || t === 'dark') return t;
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}
function toggleTheme() {
  const next = currentTheme() === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  lsSet(THEME_KEY, next);
}

/* ---------------- toasts ---------------- */
function toast(msg, type = 'info', title) {
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.innerHTML = (title ? `<b>${esc(title)}</b>` : '') + esc(msg);
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), type === 'err' ? 8000 : 4500);
}

/* ---------------- API ---------------- */
class ApiError extends Error { constructor(status, detail) { super(detail); this.status = status; this.detail = detail; } }

async function api(path, { method = 'GET', body } = {}) {
  const opts = { method, headers: { Accept: 'application/json' } };
  if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  if (method !== 'GET') { const t = getToken(); if (t) opts.headers['X-Admin-Token'] = t; }
  let res;
  try { res = await fetch(API + path, opts); } catch (e) { throw new ApiError(0, 'Serverul nu răspunde (' + e.message + ')'); }
  const text = await res.text();
  let data = null;
  if (text) { try { data = JSON.parse(text); } catch (e) { data = text; } }
  if (!res.ok) {
    let detail = data && typeof data === 'object' && 'detail' in data ? data.detail : (typeof data === 'string' && data ? data : `HTTP ${res.status}`);
    if (typeof detail !== 'string') detail = Array.isArray(detail) ? detail.map((d) => d.msg || JSON.stringify(d)).join('; ') : JSON.stringify(detail);
    const err = new ApiError(res.status, detail);
    // Auth problems: 401 always, 403 when it is about the admin token (LIVE refusal is also 403).
    if (method !== 'GET' && (res.status === 401 || (res.status === 403 && /token/i.test(detail)))) {
      openAdminModal(res.status === 401 ? 'Token invalid (401): ' + detail : detail);
    }
    throw err;
  }
  return data;
}

// Admin action wrapper: shows toast on error/success, returns data or null.
async function act(path, body, okMsg, btn) {
  if (btn) btn.classList.add('is-busy');
  try {
    const data = await api(path, { method: 'POST', body });
    if (okMsg) toast(okMsg, 'ok');
    return data ?? {};
  } catch (e) {
    toast(e.detail || e.message, 'err', 'Eroare' + (e.status ? ` (${e.status})` : ''));
    return null;
  } finally {
    if (btn) btn.classList.remove('is-busy');
  }
}

/* ---------------- admin modal ---------------- */
function openAdminModal(err) {
  const dlg = $('#admin-modal');
  $('#admin-token').value = getToken();
  const e = $('#admin-err');
  if (err) { e.textContent = err; e.classList.remove('hidden'); } else { e.classList.add('hidden'); }
  if (!dlg.open) { try { dlg.showModal(); } catch (x) { dlg.setAttribute('open', ''); } }
  setTimeout(() => $('#admin-token').focus(), 30);
}
function initAdminModal() {
  const dlg = $('#admin-modal');
  $('#admin-btn').addEventListener('click', () => openAdminModal());
  $('#admin-cancel').addEventListener('click', () => dlg.close());
  $('#admin-clear').addEventListener('click', () => { lsSet(TOKEN_KEY, null); $('#admin-token').value = ''; toast('Token șters din browser', 'info'); });
  $('#admin-form').addEventListener('submit', (ev) => {
    ev.preventDefault();
    const v = $('#admin-token').value.trim();
    lsSet(TOKEN_KEY, v || null);
    dlg.close();
    toast(v ? 'Token salvat în acest browser' : 'Token gol', v ? 'ok' : 'warn');
  });
}

/* ---------------- status (header + overview) ---------------- */
function bridgeLevel(st) {
  const b = st && st.bridge;
  if (!b || !b.connected) return 'OFFLINE';
  return isNum(b.age_s) && b.age_s > 2 ? 'DEGRADED' : 'ONLINE';
}
function mt5Level(st) {
  const b = st && st.bridge;
  if (!b || !b.connected || !b.terminal_connected) return 'OFFLINE';
  return b.trade_allowed ? 'ONLINE' : 'DEGRADED';
}
function serviceLevel() {
  if (!S.statusOkAt) return 'OFFLINE';
  const age = (Date.now() - S.statusOkAt) / 1000;
  return age < 6 ? 'ONLINE' : age < 20 ? 'DEGRADED' : 'OFFLINE';
}

function applyStatus(st) {
  if (!st || typeof st !== 'object') return;
  S.status = st;
  S.statusOkAt = Date.now();
  renderHeader();
  const p = S.route.page;
  if (p === 'overview') renderOverview();
}

function renderHeader() {
  const st = S.status;
  const feed = $('#hdr-feed'), mode = $('#hdr-mode');
  if (!st) { feed.className = 'badge'; feed.textContent = '—'; mode.className = 'badge hide-sm'; mode.textContent = '—'; return; }
  if (st.feed_label === 'SIMULATED') { feed.className = 'badge sim'; feed.textContent = 'SIMULATED'; feed.title = 'Date simulate (random walk) — NU sunt prețuri reale'; }
  else { feed.className = 'badge ok'; feed.textContent = st.feed_label || '—'; feed.title = 'Sursa datelor'; }
  const m = st.trading && st.trading.mode;
  mode.className = 'badge hide-sm ' + (m === 'DEMO' ? 'warn' : m === 'PAPER' ? 'violet' : 'info');
  mode.textContent = m || '—';
  $('#hdr-paused').classList.toggle('hidden', !(st.trading && st.trading.entries_paused));
  if (st.version) $('#app-version').textContent = 'v' + st.version;

  const banner = $('#global-banner');
  if (st.feed_label === 'SIMULATED') {
    banner.innerHTML = '<div class="banner"><span class="badge sim">SIMULATED</span> Feed simulat (DATA_SOURCE=sim): prețurile NU sunt reale. Conectează EA-ul MT5 pentru date reale.</div>';
  } else banner.innerHTML = '';
}

function accountCard(title, tag, acc, emptyTitle, emptyMsg) {
  const head = `<div class="card-head"><h2>${esc(title)}</h2>${tag}</div>`;
  if (!acc) return `<div class="card">${head}${emptyState(emptyTitle, emptyMsg)}</div>`;
  const cur = acc.currency || '';
  const meta = [];
  if (acc.login_masked) meta.push(esc(acc.login_masked));
  if (acc.server) meta.push(esc(acc.server));
  if (acc.company) meta.push(esc(acc.company));
  if (isNum(acc.leverage)) meta.push('1:' + acc.leverage);
  return `<div class="card">${head}
    <div class="acct-grid">
      <div class="kpi"><div class="label">Balanță</div><div class="value">${fmtMoney(acc.balance, cur)}</div></div>
      <div class="kpi"><div class="label">Equity</div><div class="value">${fmtMoney(acc.equity, cur)}</div></div>
      <div class="kpi"><div class="label">Marjă liberă</div><div class="value">${fmtMoney(acc.margin_free, cur)}</div><div class="meta">Marjă folosită ${fmtMoney(acc.margin, cur)}</div></div>
      <div class="kpi"><div class="label">P&amp;L flotant</div><div class="value ${pnlCls(acc.profit)}">${fmtSigned(acc.profit)}${cur ? ' ' + esc(cur) : ''}</div></div>
    </div>
    ${meta.length ? `<div class="faint" style="margin-top:12px;font-size:12px">${meta.join(' · ')}</div>` : ''}
  </div>`;
}

function sessionText(ses) {
  if (!ses) return { label: '—', next: '' };
  const nc = toDate(ses.next_change_utc);
  const rem = nc ? (nc.getTime() - Date.now()) / 1000 : null;
  const next = nc ? (ses.open_now ? 'Se închide în ' : 'Se deschide în ') + fmtDur(rem) + ` (${hmFmt.format(nc)})` : '';
  return { label: ses.open_now ? 'DESCHISĂ' : 'ÎNCHISĂ', next };
}

function renderOverview() {
  const st = S.status;
  $('#ov-clock').textContent = 'Chișinău ' + timeFmt.format(new Date());
  if (!st) {
    $('#ov-status').innerHTML = `<div class="card span-2">${emptyState('Fără date de stare', 'Serverul nu a răspuns încă la /status.')}</div>`;
    return;
  }
  const b = st.bridge || {};
  const tr = st.trading || {};
  const tiles = [
    ['Serviciu (API)', pill(serviceLevel()), `${esc(st.app_env || '')} · v${esc(st.version || '?')}`],
    ['Bridge EA', pill(bridgeLevel(st)), b.last_sync_utc ? `Sync ${ago(b.last_sync_utc)}${b.ea_version ? ' · EA ' + esc(b.ea_version) : ''}` : (st.data_source === 'sim' ? 'Nefolosit (feed simulat)' : 'Nicio sincronizare încă')],
    ['Terminal MT5', pill(mt5Level(st)), b.connected ? `Conectat: ${b.terminal_connected ? 'da' : 'nu'} · AutoTrading: ${b.trade_allowed ? 'da' : 'nu'}` : 'MT5 neconectat'],
    ['Sursă date', st.feed_label === 'SIMULATED' ? '<span class="badge sim lg">SIMULATED</span>' : `<span class="badge ok lg">${esc(st.feed_label || '—')}</span>`, `DATA_SOURCE=${esc(st.data_source || '—')}`],
    ['Mod', modeBadge(tr.mode).replace('class="badge', 'class="badge lg'), `Execuție: ${tr.execution_enabled ? '<b class="up">ACTIVĂ</b>' : '<b>oprită</b>'}${tr.entries_paused ? ' · <b class="down">intrări oprite</b>' : ''}`],
  ];
  $('#ov-status').innerHTML = tiles.map(([t, v, m]) => `<div class="card status-tile"><div class="title">${t}</div><div>${v}</div><div class="meta">${m}</div></div>`).join('');

  let mt5Empty = 'MT5 neconectat', mt5Msg = ' Pornește MT5 cu EA-ul AIBridgeEA atașat pe un grafic.';
  if (st.data_source === 'sim') { mt5Empty = 'neconfigurat'; mt5Msg = ' Feed simulat activ — nu există cont MT5.'; }
  else if (b.connected) { mt5Empty = 'neconfigurat'; mt5Msg = ' EA conectat, dar contul nu a fost raportat încă.'; }
  const acc = st.account;
  const mt5Tag = acc ? (acc.is_demo ? '<span class="badge ok">DEMO</span>' : '<span class="badge bad">REAL</span>') : '<span class="badge">—</span>';
  $('#ov-accounts').innerHTML = accountCard('Cont MT5', mt5Tag, acc, mt5Empty, mt5Msg);

  const ses = st.session || {};
  const sx = sessionText(ses);
  const days = Array.isArray(ses.days) ? ses.days.map((d) => DAY_RO[d] || d).join(' ') : '—';
  $('#ov-session').innerHTML = `<div class="card-head"><h2>Fereastra de sesiune</h2>${ses.enabled === false ? '<span class="badge">program dezactivat</span>' : ''}</div>
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">${pill(ses.open_now ? 'ONLINE' : 'OFFLINE', sx.label)}${ses.override ? `<span class="badge warn">manual: ${esc(ses.override)}</span>` : ''}</div>
    <dl class="kv">
      <dt>Interval</dt><dd class="num">${esc(ses.start || '—')} – ${esc(ses.end || '—')}</dd>
      <dt>Zile</dt><dd>${esc(days)}</dd>
      <dt>Fus orar</dt><dd>${esc(ses.tz || '—')}</dd>
      <dt>Următoarea schimbare</dt><dd class="num" id="ov-next-change">${esc(sx.next || '—')}</dd>
    </dl>`;

  const c = st.counts || {};
  $('#ov-counts').innerHTML = `<div class="card-head"><h2>Astăzi</h2></div>
    <div class="acct-grid">
      <div class="kpi"><div class="label">Tranzacții</div><div class="value">${c.trades_today ?? '—'}</div></div>
      <div class="kpi"><div class="label">Poziții deschise</div><div class="value">${c.open_positions ?? '—'}</div></div>
      <div class="kpi"><div class="label">Închise azi</div><div class="value">${c.closed_today ?? '—'}</div></div>
    </div>
    <div class="kpi" style="margin-top:12px"><div class="label">P&amp;L realizat azi</div><div class="value ${pnlCls(c.realized_pnl_today)}">${fmtSigned(c.realized_pnl_today)}</div></div>`;

  const ai = st.ai || {};
  const aiLvl = !ai.enabled ? null : !ai.key_set ? 'DEGRADED' : ai.last_error ? 'DEGRADED' : 'ONLINE';
  $('#ov-ai').innerHTML = `<div class="card-head"><h2>AI (Claude)</h2>${aiLvl ? pill(aiLvl, ai.key_set ? (ai.last_error ? 'EROARE' : 'ACTIV') : 'FĂRĂ CHEIE') : '<span class="pill">DEZACTIVAT</span>'}</div>
    <dl class="kv">
      <dt>Model</dt><dd class="mono">${esc(ai.model || '—')}</dd>
      <dt>Cost API azi</dt><dd class="num">${isNum((st.claude_cost || {}).today_usd) ? '$' + fmtNum(st.claude_cost.today_usd) : '—'}</dd>
      <dt>Cheie API</dt><dd>${ai.key_set ? '<span class="up">setată</span>' : '<span class="warn-text">lipsește (ANTHROPIC_API_KEY)</span>'}</dd>
      <dt>Ultimul OK</dt><dd>${ai.last_ok_utc ? fmtDT(ai.last_ok_utc) : '—'}</dd>
      <dt>Ultima eroare</dt><dd class="${ai.last_error ? 'down' : ''}">${esc(ai.last_error || '—')}</dd>
    </dl>`;
  renderNewsCard();
}

/* live pipeline status (GET /live): what the bot is doing right now, step by step; refreshed every 3 s */
const LIVE_CLS = { ok: 'ok', wait: 'warn', bad: 'bad', idle: '' };
const LIVE_TXT = { ok: 'OK', wait: 'AȘTEAPTĂ', bad: 'PROBLEMĂ', idle: '—' };
async function loadLive() {
  let d;
  try { d = await api('/live'); } catch (e) {
    $('#ov-live').innerHTML = `<div class="card-head"><h2>Status live</h2><span class="badge bad">PROBLEMĂ</span></div><p class="down">${esc(e.message)}</p>`;
    return;
  }
  const t = d.today || {};
  const blocks = (d.symbols || []).map((s) => `
    <div style="margin:6px 0 12px">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
        <span class="badge ${LIVE_CLS[s.level] || ''} lg">${esc(s.symbol)}</span><b>${esc(s.headline)}</b></div>
      <table class="tbl"><tbody>${s.steps.map((st) => `<tr>
        <td style="width:110px;white-space:nowrap">${esc(st.step)}</td>
        <td style="width:100px"><span class="badge ${LIVE_CLS[st.state] || ''}">${LIVE_TXT[st.state] || esc(st.state)}</span></td>
        <td style="white-space:normal">${esc(st.text)}</td></tr>`).join('')}</tbody></table>
    </div>`).join('');
  $('#ov-live').innerHTML = `<div class="card-head"><h2>Status live</h2>
      <span class="sub">mod ${esc(d.mode)} · ${d.armed ? '<b class="up">bot pornit</b>' : '<b class="down">bot oprit</b>'}
      · închise azi ${t.trades_closed ?? 0}</span></div>
    ${blocks || emptyState('Niciun simbol', 'Nu există simboluri de tranzacționat.')}`;
}
setInterval(() => { if (S.route.page === 'overview') loadLive(); }, 3000);
setInterval(() => { if (S.route.page === 'positions') loadBasket(); }, 10000);

/* economic calendar (status.news) */
const IMP_RO = { 1: 'scăzut', 2: 'moderat', 3: 'mare' };
function newsCountdown(n) {
  const nh = n && n.next_high;
  if (!nh || !isNum(nh.t)) return '—';
  const rem = nh.t - Date.now() / 1000;
  return rem >= 0 ? 'peste ' + fmtDur(rem) : 'acum ' + fmtDur(-rem);
}
function renderNewsCard() {
  const box = $('#ov-news');
  if (!box) return;
  const n = (S.status || {}).news;
  if (!n) {
    box.innerHTML = `<div class="card-head"><h2>Știri economice</h2><span class="pill">N/A</span></div>${emptyState('Fără date', ' Serverul nu raportează încă starea calendarului.')}`;
    return;
  }
  const nh = n.next_high;
  const blocked = Array.isArray(n.blocked) ? n.blocked : [];
  box.innerHTML = `<div class="card-head"><h2>Știri economice</h2>${n.ok ? pill('ONLINE', 'CALENDAR OK') : pill('DEGRADED', 'calendar indisponibil')}</div>
    <dl class="kv">
      <dt>Următoarea știre majoră</dt><dd>${nh ? `<b>${esc(nh.ccy)}</b> ${esc(nh.name || '')}` : '—'}</dd>
      <dt>Ora</dt><dd class="num">${nh ? fmtTime(nh.time_utc) : '—'} · <span id="ov-news-cd">${esc(newsCountdown(n))}</span></dd>
      <dt>Știri în 24h</dt><dd class="num">${n.events_next_24h ?? '—'} (impact ≥ ${esc(IMP_RO[n.min_importance] || n.min_importance)})</dd>
      <dt>Blocare</dt><dd>${n.blackout_minutes > 0 ? '±' + n.blackout_minutes + ' min' : 'dezactivată'}${n.ok ? '' : ' · <span class="warn-text">neaplicată (calendar indisponibil)</span>'}</dd>
      <dt>Simboluri blocate</dt><dd class="${blocked.length ? 'down' : ''}">${blocked.length ? blocked.map((x) => esc(x.symbol)).join(', ') : 'niciunul'}</dd>
      <dt>Actualizat</dt><dd>${isNum(n.age_s) ? 'acum ' + fmtDur(n.age_s) : '—'}</dd>
    </dl>`;
}
/* ---------------- positions page ---------------- */
async function loadPositionsQuiet() {
  try { S.positions = await api('/positions'); } catch (e) { /* keep last */ }
}
async function enterPositions() {
  await ensureSymbols();
  await Promise.all([loadPositionsQuiet(), loadTrades(), loadBasket()]);
  renderPositions();
}
const reloadPositionsPage = debounce(() => { if (S.route.page === 'positions') enterPositions(); }, 500);

function renderPositions() {
  const p = S.positions;
  const open = p && Array.isArray(p.open) ? p.open : [];
  $('#pos-open-sub').textContent = open.length ? `${open.length} poziții · P&L ${fmtSigned(open.reduce((a, x) => a + (isNum(x.profit) ? x.profit : 0), 0))}` : '';
  $('#pos-open').innerHTML = !p ? emptyState('Nu s-au putut încărca pozițiile', '') : !open.length ? emptyState('Nicio poziție deschisă', '') : table(
    ['Tichet', 'Sursă', 'Simbol', 'Direcție', { t: 'Volum', cls: 'num' }, { t: 'Preț intrare', cls: 'num' }, { t: 'Preț curent', cls: 'num' }, { t: 'SL', cls: 'num' }, { t: 'TP', cls: 'num' }, { t: 'Profit', cls: 'num' }, { t: 'Swap', cls: 'num' }, 'Deschisă', 'Comentariu', ''],
    open.map((x) => `<tr>
      <td class="num mono">${esc(x.ticket)}</td>
      <td>${x.source === 'PAPER' ? '<span class="badge violet">PAPER</span>' : '<span class="badge info">MT5</span>'}${x.bot_owned ? '' : ' <span class="badge" title="Poziție manuală — botul nu o atinge">manuală</span>'}</td>
      <td><b>${esc(x.symbol)}</b></td><td>${sideBadge(x.side)}</td>
      <td class="num">${isNum(x.volume) ? x.volume.toFixed(2) : '—'}</td>
      <td class="num">${fmtPrice(x.price_open, x.symbol)}</td><td class="num">${fmtPrice(x.price_current, x.symbol)}</td>
      <td class="num">${fmtPrice(x.sl, x.symbol)}</td><td class="num">${fmtPrice(x.tp, x.symbol)}</td>
      <td class="num ${pnlCls(x.profit)}">${fmtSigned(x.profit)}</td><td class="num">${fmtNum(x.swap)}</td>
      <td class="num">${fmtDTs(x.opened_at_utc)}</td><td class="mono">${esc(x.comment || '')}</td>
      <td>${x.bot_owned ? `<button class="btn danger sm" data-close-ticket="${esc(x.ticket)}" data-close-sym="${esc(x.symbol)}">Închide</button>` : ''}</td>
    </tr>`).join(''));

  const pend = p && Array.isArray(p.pending_commands) ? p.pending_commands : [];
  $('#pos-pending').innerHTML = pend.length ? intentsTable(pend, 'pend') : emptyState('Nicio comandă în așteptare', '');
}
/* basket loss limit (GET/PUT /basket): the group closes at -limit USD; editable from the Positions page */
async function loadBasket() {
  try {
    const b = await api('/basket');
    $('#basket-limit').value = b.max_loss_per_001_usd;
    $('#basket-current').textContent = `Valoare curentă: −${b.max_loss_usd} USD` +
      (b.lot_scale > 1 ? ` (−${b.max_loss_per_001_usd} × ${b.lot_scale})` : '');
    $('#start-lot').value = b.start_lot || 0;
    $('#start-lot-current').textContent = b.start_lot ? `Lot curent: ${b.start_lot}` : 'Lot curent: după sold';
    $('#start-lot-note').textContent = b.start_lot ? `Sumele în USD ale coșului (limita) sunt ×${b.lot_scale} față de 0.01 lot; ` +
      `lotul maxim al lui Claude: ${b.claude_max_lot}.` : '0 = lotul crește cu soldul.';
    $('#btn-bot-stop').disabled = !b.running;
    $('#btn-bot-start').disabled = b.running;
    const st = $('#bot-state');
    st.textContent = b.running ? 'BOT PORNIT' : 'BOT OPRIT';
    st.className = 'badge lg ' + (b.running ? 'ok' : 'bad');
    const cl = b.claude_last;
    $('#claude-last').innerHTML = cl ? `<span class="badge ${cl.status === 'DONE' ? 'ok' : 'bad'}">Claude ${esc(cl.time_utc || '')}</span> ` +
      `<b>${esc((cl.done || []).join('; ') || 'aștept')}</b> · ${esc(cl.market_view || '')}<br><span class="faint">Plan: ${esc(cl.plan || '')}</span>`
      : '<span class="faint">Claude decide la următoarea bară M1…</span>';
    const sc = b.scalp || {};
    $('#strategy-note').textContent = (b.running ? '' : 'Botul e oprit: nu deschide și nu închide nimic (pozițiile rămân cu SL/TP la broker). ') +
      `Claude alege direcția la fiecare bară M1; fiecare intrare are TP fix +${sc.tp_price} și SL ${sc.sl_price} (preț).`;
    $('#basket-sub').textContent = b.enabled ? `acum −${b.max_loss_usd} USD (${b.source})` : 'modul coș nu este activ';
    $('#basket-note').textContent = 'Limita închide toate pozițiile botului odată, ca plasă de siguranță peste SL-ul fiecărei poziții.';
  } catch (e) { $('#basket-sub').textContent = 'eroare: ' + (e.detail || e.message); }
}

function intentsTable(list, prefix) {
  const rows = list.map((it, i) => {
    const rc = it.risk_check || {};
    const checks = Array.isArray(rc.checks) ? rc.checks : [];
    const failed = checks.filter((c) => !c.ok).length;
    const res = it.result;
    const rid = `${prefix}-${i}`;
    return `<tr class="clickable" data-toggle="${rid}">
      <td class="num">${fmtDTs(it.created_at_utc)}</td>
      <td><span class="badge">${esc(it.kind)}</span></td>
      <td><b>${esc(it.symbol || '—')}</b></td><td>${sideBadge(it.side)}</td>
      <td class="num">${isNum(it.volume) ? it.volume.toFixed(2) : '—'}</td>
      <td class="num">${fmtPrice(it.sl, it.symbol)}</td><td class="num">${fmtPrice(it.tp, it.symbol)}</td>
      <td>${modeBadge(it.mode)}</td>
      <td>${statusBadge(it.state, INTENT_STATE_CLS)}</td>
      <td>${checks.length ? (rc.passed ? `<span class="badge ok">✓ ${checks.length}/${checks.length}</span>` : `<span class="badge bad">✗ ${failed} eșuate</span>`) : '—'}</td>
      <td class="mono">${res ? esc(`${res.retcode ?? ''} ${res.retcode_desc ?? ''}`.trim()) : '—'}</td>
      <td class="faint">▾</td>
    </tr>
    <tr class="sub-row hidden" id="${rid}"><td colspan="12">
      <div class="grid g-2" style="gap:12px">
        <div><div class="eyebrow" style="margin-bottom:4px">Verificări risc</div>
          ${checks.length ? `<div class="checks">${checks.map((c) => `<div class="check ${c.ok ? 'ok' : 'fail'}"><span class="ic">${c.ok ? '✓' : '✗'}</span><span class="nm">${esc(c.name)}</span><span class="dt">${esc(c.detail || '')}</span></div>`).join('')}</div>` : '<div class="faint">Fără verificări</div>'}
        </div>
        <div><div class="eyebrow" style="margin-bottom:4px">Detalii</div>
          <dl class="kv">
            <dt>ID</dt><dd class="mono">${esc(it.id)}</dd>
            <dt>Tichet</dt><dd class="mono">${esc(it.ticket ?? '—')}</dd>
            <dt>Actualizat</dt><dd>${fmtDT(it.updated_at_utc)}</dd>
            ${res ? `<dt>Rezultat</dt><dd class="mono">preț ${esc(res.price ?? '—')} · vol ${esc(res.volume ?? '—')} · poz ${esc(res.position ?? '—')}${res.comment ? ' · ' + esc(res.comment) : ''}</dd>` : ''}
          </dl>
        </div>
      </div>
    </td></tr>`;
  }).join('');
  return table(['Creat', 'Tip', 'Simbol', 'Direcție', { t: 'Volum', cls: 'num' }, { t: 'SL', cls: 'num' }, { t: 'TP', cls: 'num' }, 'Mod', 'Stare', 'Risc', 'Rezultat', ''], rows);
}
async function loadTrades() {
  const box = $('#pos-trades');
  try {
    const list = await api('/trades?limit=100');
    if (!Array.isArray(list) || !list.length) { box.innerHTML = emptyState('Nicio tranzacție închisă', ''); return; }
    const sum = (k) => list.reduce((a, t) => a + (isNum(t[k]) ? t[k] : 0), 0);
    const pr = sum('profit'), cm = sum('commission'), sw = sum('swap'), net = pr + cm + sw;
    const wins = list.filter((t) => isNum(t.profit) && t.profit > 0).length;
    const rows = list.map((t) => `<tr>
      <td class="num">${fmtDTs(t.closed_at_utc)}</td><td class="num mono">${esc(t.ticket ?? '—')}</td>
      <td><b>${esc(t.symbol)}</b></td><td>${sideBadge(t.side)}</td>
      <td class="num">${isNum(t.volume) ? t.volume.toFixed(2) : '—'}</td>
      <td class="num">${fmtPrice(t.price_open, t.symbol)}</td><td class="num">${fmtPrice(t.price_close, t.symbol)}</td>
      <td class="num ${pnlCls(t.profit)}">${fmtSigned(t.profit)}</td><td class="num">${fmtNum(t.commission)}</td><td class="num">${fmtNum(t.swap)}</td>
      <td><span class="badge ${t.close_reason === 'TP' ? 'ok' : t.close_reason === 'SL' ? 'bad' : ''}">${esc(t.close_reason || '—')}</span></td>
      <td>${modeBadge(t.mode)}</td>
    </tr>`).join('');
    const foot = `<tfoot><tr><td colspan="7">Total ${list.length} tranzacții · ${wins} câștigătoare · net ${fmtSigned(net)}</td>
      <td class="num ${pnlCls(pr)}">${fmtSigned(pr)}</td><td class="num">${fmtNum(cm)}</td><td class="num">${fmtNum(sw)}</td><td colspan="2"></td></tr></tfoot>`;
    box.innerHTML = table(['Închisă', 'Tichet', 'Simbol', 'Direcție', { t: 'Volum', cls: 'num' }, { t: 'Intrare', cls: 'num' }, { t: 'Ieșire', cls: 'num' }, { t: 'Profit', cls: 'num' }, { t: 'Comision', cls: 'num' }, { t: 'Swap', cls: 'num' }, 'Motiv', 'Mod'], rows, { foot });
  } catch (e) { box.innerHTML = emptyState('Eroare la încărcare', ' ' + (e.detail || e.message)); }
}

async function refreshSymbols() {
  try {
    const list = await api('/symbols');
    if (Array.isArray(list)) {
      S.symbols = list;
      S.symbolsLoadedAt = Date.now();
      const now = Date.now();
      list.forEach((s) => {
        const q = S.quotes[s.symbol];
        // Prefer fresher SSE tick data if received in the last second.
        if (!q || now - q.recvAt > 1000) S.quotes[s.symbol] = { bid: s.bid, ask: s.ask, spread_points: s.spread_points, ageBase: isNum(s.tick_age_s) ? s.tick_age_s : null, recvAt: now };
      });
    }
  } catch (e) { /* keep last */ }
}
async function ensureSymbols() {
  if (!S.symbols.length || !S.symbolsLoadedAt || Date.now() - S.symbolsLoadedAt > 30000) await refreshSymbols();
  if (!S.settings) { try { S.settings = await api('/settings'); } catch (e) { /* optional */ } }
}

/* ---------------- status fetch ---------------- */
async function refreshStatus() {
  const t0 = performance.now();
  try {
    const st = await api('/status');
    S.statusLatencyMs = Math.round(performance.now() - t0);
    applyStatus(st);
  } catch (e) {
    if (S.route.page === 'overview' && !S.status) renderOverview();
  }
}

/* ---------------- SSE ---------------- */
function setSse(state) {
  S.sse.connected = state === 'ok';
  const ind = $('#sse-ind');
  const dot = $('.dot', ind);
  dot.className = 'dot ' + (state === 'ok' ? 'ok' : state === 'warn' ? 'warn' : 'bad');
  $('.lbl', ind).textContent = state === 'ok' ? 'Live' : state === 'warn' ? 'Reconectare…' : 'Offline';
}
function parse(ev) { try { return JSON.parse(ev.data); } catch (e) { return null; } }

function connectSSE() {
  if (S.sse.es) { try { S.sse.es.close(); } catch (e) { /* ignore */ } }
  let es;
  try { es = new EventSource(API + '/stream'); } catch (e) { setSse('bad'); setTimeout(connectSSE, 5000); return; }
  S.sse.es = es;
  es.onopen = () => {
    setSse('ok');
    if (S.sse.hadError) { S.sse.hadError = false; resync(); }
  };
  es.onerror = () => {
    S.sse.hadError = true;
    if (es.readyState === 2) { setSse('bad'); setTimeout(connectSSE, 4000); } else setSse('warn');
  };
  es.addEventListener('status', (ev) => { const d = parse(ev); if (d) applyStatus(d); });
  es.addEventListener('tick', (ev) => {
    const t = parse(ev);
    if (!t || !t.symbol) return;
    S.quotes[t.symbol] = { bid: t.bid, ask: t.ask, spread_points: t.spread_points, ageBase: isNum(t.time_msc) ? Math.max(0, Math.min(5, (Date.now() - t.time_msc) / 1000)) : 0, recvAt: Date.now() };
  });
  es.addEventListener('intent', (ev) => {
    const it = parse(ev);
    if (!it) return;
    if (S.route.page === 'positions') reloadPositionsPage();
    if (it.state === 'UNKNOWN' || it.state === 'FAILED') toast(`${it.kind} ${it.symbol || ''}: ${it.state}`, 'warn', 'Intenție');
  });
  es.addEventListener('positions', (ev) => {
    const d = parse(ev);
    if (!d) return;
    S.positions = d;
    if (S.route.page === 'positions') renderPositions();
  });
  es.addEventListener('trade', (ev) => {
    const t = parse(ev);
    if (t) toast(`${t.symbol || ''} ${t.side || ''} închis (${t.close_reason || '—'}) · ${fmtSigned(t.profit)}`, isNum(t.profit) && t.profit >= 0 ? 'ok' : 'err', 'Tranzacție închisă');
    if (S.route.page === 'positions') reloadPositionsPage();
  });
}

async function resync() {
  await Promise.all([refreshStatus(), refreshSymbols(), loadPositionsQuiet()]);
  const p = S.route.page;
  if (p === 'overview') loadLive();
  if (p === 'positions') enterPositions();
}

/* ---------------- router ---------------- */
const PAGES = ['overview', 'positions'];
function route() {
  const raw = decodeURIComponent((location.hash || '#overview').slice(1));
  let [page, ...rest] = raw.split('/');
  if (!PAGES.includes(page)) page = 'overview';
  const arg = rest.join('/') || null;
  S.route = { page, arg };
  $$('.page').forEach((s) => { s.hidden = s.id !== 'page-' + page; });
  $$('#nav a').forEach((a) => a.classList.toggle('active', a.dataset.page === page));
  $('#nav').classList.remove('open');
  $('#menu-btn').setAttribute('aria-expanded', 'false');
  window.scrollTo(0, 0);
  switch (page) {
    case 'overview': renderOverview(); loadLive(); break;
    case 'positions': enterPositions(); break;
  }
}

/* ---------------- init ---------------- */
function init() {
  initAdminModal();
  $('#theme-btn').addEventListener('click', toggleTheme);
  $('#menu-btn').addEventListener('click', () => {
    const n = $('#nav'); n.classList.toggle('open');
    $('#menu-btn').setAttribute('aria-expanded', n.classList.contains('open') ? 'true' : 'false');
  });
  // Row navigation & toggles (delegated).
  document.addEventListener('click', async (ev) => {
    if (ev.target.closest('a, button, input, select')) {
      const cb = ev.target.closest('[data-close-ticket]');
      if (cb) {
        const ticket = Number(cb.dataset.closeTicket);
        if (!confirm(`Închizi poziția #${cb.dataset.closeTicket} (${cb.dataset.closeSym})?`)) return;
        const r = await act('/control/close-position', { ticket, confirm: true }, `Cerere de închidere trimisă pentru #${ticket}`, cb);
        if (r) { loadPositionsQuiet().then(() => { if (S.route.page === 'positions') renderPositions(); }); }
      }
      return;
    }
    const tr = ev.target.closest('tr[data-href]');
    if (tr) { location.hash = tr.dataset.href; return; }
    const tg = ev.target.closest('tr[data-toggle]');
    if (tg) { const r = document.getElementById(tg.dataset.toggle); if (r) r.classList.toggle('hidden'); }
  });
  $('#btn-pos-refresh').addEventListener('click', enterPositions);
  $('#btn-bot-stop').addEventListener('click', async (ev) => {
    if (!confirm('Oprești botul? Nu va mai deschide și nu va mai închide nimic; pozițiile deschise rămân cu SL/TP la broker.')) return;
    if (await act('/control/pause-entries', undefined, 'Botul este OPRIT', ev.currentTarget)) { refreshStatus(); loadBasket(); }
  });
  $('#btn-bot-start').addEventListener('click', async (ev) => {
    if (await act('/control/resume-entries', undefined, 'Botul este PORNIT', ev.currentTarget)) { refreshStatus(); loadBasket(); }
  });
  $('#btn-start-lot-save').addEventListener('click', async (ev) => {
    const v = Number($('#start-lot').value);
    if (!Number.isFinite(v) || v < 0 || v > 1) { toast('Introdu un lot între 0.01 și 1 (0 = după sold)', 'warn'); return; }
    const btn = ev.currentTarget;
    btn.classList.add('is-busy');
    try {
      const b = await api('/basket', { method: 'PUT', body: { start_lot: v } });
      toast(b.start_lot ? `Intrările noi se deschid cu ${b.start_lot} lot; coșul se închide la −${b.max_loss_usd} USD` : 'Lotul urmează soldul', 'ok', 'Salvat');
      loadBasket();
    } catch (e) {
      toast(e.detail || e.message, 'err', 'Eroare' + (e.status ? ` (${e.status})` : ''));
    } finally { btn.classList.remove('is-busy'); }
  });
  $('#btn-basket-save').addEventListener('click', async (ev) => {
    const v = Number($('#basket-limit').value);
    if (!Number.isFinite(v) || v < 1 || v > 1000) { toast('Introdu o valoare între 1 și 1000 USD', 'warn'); return; }
    const btn = ev.currentTarget;
    btn.classList.add('is-busy');
    try {
      const b = await api('/basket', { method: 'PUT', body: { max_loss_usd: v } });
      toast(`Coșul se închide acum la −${b.max_loss_usd} USD`, 'ok', 'Salvat');
      loadBasket();
    } catch (e) {
      toast(e.detail || e.message, 'err', 'Eroare' + (e.status ? ` (${e.status})` : ''));
    } finally { btn.classList.remove('is-busy'); }
  });
  const clearHistory = async (what, label, btn) => {
    if (!confirm(`Ștergi tot istoricul de ${label}? Se păstrează doar ce e încă activ (ordine în curs, poziții deschise). ` +
                 'Înainte de ștergere se salvează o copie în data/backup.')) return;
    const r = await act('/history/clear', { what, confirm: 'DELETE' }, null, btn);
    if (r) { toast(`Șterse: ${r.deleted} · copie: ${r.backup}`, 'ok', 'Istoric curățat'); enterPositions(); }
  };
  $('#btn-clear-trades').addEventListener('click', (ev) => clearHistory('trades', 'tranzacții închise', ev.currentTarget));
  window.addEventListener('hashchange', route);
  // 1 s ticker: clocks, countdowns, tick age, fallback polling.
  setInterval(() => {
    const p = S.route.page;
    if (p === 'overview') {
      $('#ov-clock').textContent = 'Chișinău ' + timeFmt.format(new Date());
      const el = $('#ov-next-change');
      if (el && S.status) el.textContent = sessionText(S.status.session).next || '—';
      const cd = $('#ov-news-cd');
      if (cd && S.status) cd.textContent = newsCountdown(S.status.news);
    }
  }, 1000);
  setInterval(() => { if (!S.sse.connected) refreshStatus(); }, 5000);

  refreshStatus().then(() => refreshSymbols());
  route();
  setSse('warn');
  connectSSE();
}

document.addEventListener('DOMContentLoaded', init);
