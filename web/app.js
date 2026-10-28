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
  signals: [],           // signals page list (filtered)
  route: { page: 'overview', arg: null },
  sse: { es: null, connected: false, hadError: false },
  logsTimer: null,
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
  applyChartThemes();
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
  if (p === 'control') renderControlState();
  if (p === 'integrations') renderIntegrations();
  if (p === 'system') renderHealth();
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
  const mt5Tag = acc ? (acc.is_demo ? '<span class="badge ok">DEMO</span>' : '<span class="badge bad">REAL — execuția blocată</span>') : '<span class="badge">—</span>';
  $('#ov-accounts').innerHTML =
    accountCard('Cont MT5', mt5Tag, acc, mt5Empty, mt5Msg) +
    accountCard('Cont PAPER (simulare)', '<span class="badge violet">PAPER</span>', st.paper_account || null, 'neconfigurat', ' Contul paper nu este disponibil.');

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
      <div class="kpi"><div class="label">Semnale</div><div class="value">${c.signals_today ?? '—'}</div></div>
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
      <dt>Veto</dt><dd>${ai.veto_enabled ? 'activ' : 'inactiv'}</dd>
      <dt>Cheie API</dt><dd>${ai.key_set ? '<span class="up">setată</span>' : '<span class="warn-text">lipsește (ANTHROPIC_API_KEY)</span>'}</dd>
      <dt>Ultimul OK</dt><dd>${ai.last_ok_utc ? fmtDT(ai.last_ok_utc) : '—'}</dd>
      <dt>Ultima eroare</dt><dd class="${ai.last_error ? 'down' : ''}">${esc(ai.last_error || '—')}</dd>
    </dl>`;
  renderNewsCard();
}

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

async function loadOverviewSignals() {
  const box = $('#ov-signals');
  try {
    const list = await api('/signals?limit=10');
    box.innerHTML = signalsTable(Array.isArray(list) ? list : [], 'Niciun semnal încă', 'Semnalele apar la închiderea barelor, în fereastra de sesiune.');
  } catch (e) { box.innerHTML = emptyState('Nu s-au putut încărca semnalele', ' ' + (e.detail || e.message)); }
}

function signalsTable(list, emptyT, emptyM) {
  if (!list.length) return emptyState(emptyT, emptyM ? ' ' + emptyM : '');
  const rows = list.map((s) => {
    const p = s.probabilities || {};
    const pd = s.direction === 'BUY' ? p.up : s.direction === 'SELL' ? p.down : null;
    return `<tr class="clickable" data-href="#signal/${esc(s.id)}">
      <td class="num">${fmtDTs(s.created_at_utc)}</td>
      <td><b>${esc(s.symbol)}</b> <span class="faint">${esc(s.timeframe || '')}</span></td>
      <td>${dirBadge(s.direction)}</td>
      <td class="num">${fmtPct(pd)}</td>
      <td class="num">${fmtPrice(s.entry, s.symbol)}</td>
      <td class="num">${fmtPrice(s.stop_loss, s.symbol)}</td>
      <td class="num">${fmtPrice(s.take_profit, s.symbol)}</td>
      <td class="num">${isNum(s.rr) ? s.rr.toFixed(2) : '—'}</td>
      <td>${verdictBadge(s.ai)}</td>
      <td>${statusBadge(s.status, SIGNAL_STATUS_CLS)}</td>
      <td>${modeBadge(s.mode)}</td>
    </tr>`;
  }).join('');
  return table(['Ora', 'Simbol', 'Direcție', { t: 'Prob.*', cls: 'num' }, { t: 'Intrare', cls: 'num' }, { t: 'SL', cls: 'num' }, { t: 'TP', cls: 'num' }, { t: 'R:R', cls: 'num' }, 'AI', 'Stare', 'Mod'], rows)
    + '<div class="faint" style="font-size:11.5px;padding:8px 12px 0">* probabilitate euristică, NEcalibrată</div>';
}

/* ---------------- chart helpers ---------------- */
function cssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
function chartThemeOptions() {
  return {
    layout: { background: { type: 'solid', color: cssVar('--surface') }, textColor: cssVar('--text-dim'), fontFamily: 'Inter, system-ui, sans-serif', fontSize: 11 },
    grid: { vertLines: { color: cssVar('--chart-grid') }, horzLines: { color: cssVar('--chart-grid') } },
    rightPriceScale: { borderColor: cssVar('--border') },
    timeScale: { borderColor: cssVar('--border') },
    crosshair: { vertLine: { labelBackgroundColor: cssVar('--surface-3') }, horzLine: { labelBackgroundColor: cssVar('--surface-3') } },
  };
}
function chartTickFormatter(t, type) {
  const d = new Date(t * 1000);
  if (type === 0) return yearFmt.format(d);
  if (type === 1) return monthFmt.format(d);
  if (type === 2) return dayFmt.format(d);
  return hmFmt.format(d);
}
function makeChart(el) {
  if (!window.LightweightCharts) return null;
  const c = LightweightCharts.createChart(el, Object.assign({
    autoSize: true,
    localization: { locale: 'ro-RO', timeFormatter: (t) => (typeof t === 'number' ? dtShortFmt.format(new Date(t * 1000)) : String(t)) },
    crosshair: { mode: 0 },
  }, chartThemeOptions()));
  c.applyOptions({ timeScale: { timeVisible: true, secondsVisible: false, tickMarkFormatter: chartTickFormatter, rightOffset: 6 } });
  return c;
}
function addSeriesSet(chart, sym) {
  const candle = chart.addCandlestickSeries({
    upColor: cssVar('--up'), downColor: cssVar('--down'), borderVisible: false,
    wickUpColor: cssVar('--up'), wickDownColor: cssVar('--down'), priceFormat: priceFormatFor(sym),
  });
  const emaF = chart.addLineSeries({ color: cssVar('--accent'), lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, priceFormat: priceFormatFor(sym) });
  const emaS = chart.addLineSeries({ color: cssVar('--violet'), lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, priceFormat: priceFormatFor(sym) });
  return { candle, emaF, emaS };
}
function priceFormatFor(sym) {
  const d = digitsFor(sym);
  return { type: 'price', precision: d, minMove: Math.pow(10, -d) };
}
function normBar(b) {
  if (!b) return null;
  const time = b.time ?? b.t, open = b.open ?? b.o, high = b.high ?? b.h, low = b.low ?? b.l, close = b.close ?? b.c;
  if (![time, open, high, low, close].every(isNum)) return null;
  return { time, open, high, low, close };
}
function cleanBars(bars) {
  const out = [];
  (bars || []).map(normBar).filter(Boolean).sort((a, b) => a.time - b.time).forEach((b) => {
    if (out.length && out[out.length - 1].time === b.time) out[out.length - 1] = b; else out.push(b);
  });
  return out;
}
function cleanLine(pts) {
  const out = [];
  (pts || []).filter((p) => p && isNum(p.time) && isNum(p.value)).sort((a, b) => a.time - b.time).forEach((p) => {
    if (out.length && out[out.length - 1].time === p.time) out[out.length - 1] = p; else out.push({ time: p.time, value: p.value });
  });
  return out;
}
function levelLine(series, price, color, title, style = 2) {
  if (!isNum(price)) return null;
  return series.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title });
}

/* ---------------- live chart page ---------------- */
const CH = {
  chart: null, s: null, symbol: null, tf: null, bars: [], lastBar: null, lines: [], signals: [],
  loadSeq: 0, pendingTick: false, tickTimer: 0, analysis: null, symbolsTimer: 0, tfs: DEFAULT_TFS.slice(),
};

function chartTfs() {
  const set = new Set(DEFAULT_TFS);
  const st = S.settings && S.settings.strategy;
  if (st) { if (st.signal_timeframe) set.add(st.signal_timeframe); if (st.htf_timeframe) set.add(st.htf_timeframe); }
  return ALL_TFS.filter((t) => set.has(t));
}

async function enterChart() {
  await ensureSymbols();
  const sel = $('#ch-symbol');
  const syms = S.symbols.map((s) => s.symbol);
  if (!CH.symbol || !syms.includes(CH.symbol)) CH.symbol = lsGet('mt5bot.chartSymbol') && syms.includes(lsGet('mt5bot.chartSymbol')) ? lsGet('mt5bot.chartSymbol') : syms[0] || null;
  sel.innerHTML = syms.length ? syms.map((s) => `<option ${s === CH.symbol ? 'selected' : ''}>${esc(s)}</option>`).join('') : '<option value="">(niciun simbol)</option>';
  CH.tfs = chartTfs();
  if (!CH.tf || !CH.tfs.includes(CH.tf)) {
    const saved = lsGet('mt5bot.chartTf');
    CH.tf = saved && CH.tfs.includes(saved) ? saved : (S.settings && S.settings.strategy && S.settings.strategy.signal_timeframe) || 'M5';
  }
  $('#ch-tf').innerHTML = CH.tfs.map((t) => `<button data-tf="${t}" class="${t === CH.tf ? 'active' : ''}">${t}</button>`).join('');

  if (!CH.chart) {
    CH.chart = makeChart($('#ch-box'));
    if (!CH.chart) { showChartOverlay('Biblioteca de grafic nu s-a încărcat', 'Verifică conexiunea la unpkg.com (Lightweight Charts 4.2.0).'); return; }
  }
  if (!CH.symbol) { showChartOverlay('Niciun simbol activ', 'Setează ACTIVE_SYMBOLS în .env sau din Control & Risc → Strategie.'); renderAnalysis(null); return; }
  renderQuote();
  await Promise.all([loadChartData(), loadChartAnalysis(), loadChartSignals(), loadPositionsQuiet()]);
  clearInterval(CH.symbolsTimer);
  CH.symbolsTimer = setInterval(() => { if (S.route.page === 'chart') refreshSymbols().then(renderQuote); }, 5000);
}
function leaveChart() { clearInterval(CH.symbolsTimer); }

function showChartOverlay(t, m) { const o = $('#ch-overlay'); o.innerHTML = emptyState(t, ' ' + (m || '')); o.classList.remove('hidden'); }
function hideChartOverlay() { $('#ch-overlay').classList.add('hidden'); }

function rebuildSeries() {
  if (CH.s) { CH.chart.removeSeries(CH.s.candle); CH.chart.removeSeries(CH.s.emaF); CH.chart.removeSeries(CH.s.emaS); }
  CH.lines = [];
  CH.s = addSeriesSet(CH.chart, CH.symbol);
  CH.s.symbol = CH.symbol;
}

async function loadChartData({ keepRange = false } = {}) {
  if (!CH.chart || !CH.symbol) return;
  const seq = ++CH.loadSeq;
  const sym = CH.symbol, tf = CH.tf;
  if (!keepRange) $('#ch-meta').textContent = 'Se încarcă…';
  let d;
  try { d = await api(`/candles?symbol=${enc(sym)}&tf=${enc(tf)}&limit=300`); }
  catch (e) { if (seq === CH.loadSeq) { showChartOverlay('Nu s-au putut încărca lumânările', e.detail || e.message); $('#ch-meta').textContent = ''; } return; }
  if (seq !== CH.loadSeq) return;
  const range = keepRange ? CH.chart.timeScale().getVisibleLogicalRange() : null;
  if (!CH.s || CH.s.symbol !== sym || !keepRange) rebuildSeries();
  CH.bars = cleanBars(d && d.bars);
  CH.lastBar = CH.bars.length ? Object.assign({}, CH.bars[CH.bars.length - 1]) : null;
  CH.s.candle.setData(CH.bars);
  CH.s.emaF.setData(cleanLine(d && d.ema_fast));
  CH.s.emaS.setData(cleanLine(d && d.ema_slow));
  if (!CH.bars.length) {
    showChartOverlay('Nu există bare pentru ' + sym + ' ' + tf, 'Așteaptă sincronizarea istoricului de la EA (sau feed-ul simulat).');
  } else hideChartOverlay();
  if (range) CH.chart.timeScale().setVisibleLogicalRange(range);
  else if (CH.bars.length) CH.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, CH.bars.length - 120), to: CH.bars.length + 6 });
  $('#ch-meta').textContent = CH.bars.length ? `${CH.bars.length} bare · ultima ${fmtDTs(CH.lastBar.time)}` : '';
  applyChartMarkers();
  applyPositionLines();
}
const refreshChartAfterNewBar = debounce(() => { if (S.route.page === 'chart') loadChartData({ keepRange: true }); }, 1200);

function onChartTick(t) {
  if (!CH.s || !CH.lastBar || t.symbol !== CH.symbol || !isNum(t.bid)) return;
  const b = CH.lastBar;
  b.close = t.bid; b.high = Math.max(b.high, t.bid); b.low = Math.min(b.low, t.bid);
  if (!CH.pendingTick) {
    CH.pendingTick = true;
    CH.tickTimer = setTimeout(() => {
      CH.pendingTick = false;
      if (CH.s && CH.lastBar) { try { CH.s.candle.update(Object.assign({}, CH.lastBar)); } catch (e) { /* ignore out-of-order */ } }
    }, 250);
  }
}
function onChartBar(ev) {
  if (!CH.s || !ev || ev.symbol !== CH.symbol || ev.tf !== CH.tf) return;
  const b = normBar(ev.bar);
  if (!b) return;
  if (!CH.lastBar || b.time > CH.lastBar.time) {
    CH.lastBar = Object.assign({}, b);
    try { CH.s.candle.update(b); } catch (e) { /* ignore */ }
    hideChartOverlay();
    refreshChartAfterNewBar(); // refresh EMA + finalize previous bar from server
  } else if (b.time === CH.lastBar.time) {
    CH.lastBar = Object.assign({}, b);
    try { CH.s.candle.update(b); } catch (e) { /* ignore */ }
  }
}

async function loadChartSignals() {
  if (!CH.symbol) return;
  try {
    const list = await api(`/signals?limit=100&symbol=${enc(CH.symbol)}`);
    CH.signals = Array.isArray(list) ? list : [];
  } catch (e) { CH.signals = []; }
  applyChartMarkers();
}
function signalMarkers(signals, tf, bars) {
  if (!bars.length) return [];
  const sec = TF_SECONDS[tf] || 60;
  const first = bars[0].time, last = bars[bars.length - 1].time;
  const seen = new Set();
  return signals
    .filter((s) => (s.direction === 'BUY' || s.direction === 'SELL') && isNum(s.bar_time))
    .map((s) => {
      const time = Math.floor(s.bar_time / sec) * sec;
      return {
        time, position: s.direction === 'BUY' ? 'belowBar' : 'aboveBar',
        color: s.direction === 'BUY' ? cssVar('--up') : cssVar('--down'),
        shape: s.direction === 'BUY' ? 'arrowUp' : 'arrowDown', text: s.direction,
      };
    })
    .filter((m) => { const k = m.time + m.text; if (m.time < first || m.time > last || seen.has(k)) return false; seen.add(k); return true; })
    .sort((a, b) => a.time - b.time);
}
function applyChartMarkers() {
  if (!CH.s) return;
  CH.s.candle.setMarkers(signalMarkers(CH.signals.filter((s) => s.symbol === CH.symbol), CH.tf, CH.bars));
}
function applyPositionLines() {
  if (!CH.s) return;
  CH.lines.forEach((l) => { try { CH.s.candle.removePriceLine(l); } catch (e) { /* ignore */ } });
  CH.lines = [];
  const open = (S.positions && Array.isArray(S.positions.open)) ? S.positions.open : [];
  open.filter((p) => p.symbol === CH.symbol && p.bot_owned).forEach((p) => {
    const tag = `#${p.ticket} ${p.side}${p.source === 'PAPER' ? ' (P)' : ''}`;
    [levelLine(CH.s.candle, p.price_open, cssVar('--text-dim'), tag + ' intrare', 0),
      levelLine(CH.s.candle, p.sl, cssVar('--down'), tag + ' SL'),
      levelLine(CH.s.candle, p.tp, cssVar('--up'), tag + ' TP')].forEach((l) => l && CH.lines.push(l));
  });
}

function renderQuote() {
  const sym = CH.symbol;
  const info = symInfo(sym);
  const q = S.quotes[sym];
  const bid = q && isNum(q.bid) ? q.bid : info && info.bid;
  const ask = q && isNum(q.ask) ? q.ask : info && info.ask;
  const spread = q && isNum(q.spread_points) ? q.spread_points : info && info.spread_points;
  $('#q-bid').textContent = fmtPrice(bid, sym);
  $('#q-ask').textContent = fmtPrice(ask, sym);
  $('#q-spread').textContent = isNum(spread) ? spread + ' pt' + (info && isNum(info.max_spread_points) ? ` / max ${info.max_spread_points}` : '') : '—';
  renderTickAge();
  const qual = info ? info.quality : null;
  const qe = $('#q-quality');
  qe.className = 'badge ' + (qual === 'OK' ? 'ok' : qual === 'STALE' ? 'warn' : (qual === 'NO_DATA' || qual === 'CROSSED') ? 'bad' : '');
  qe.textContent = qual ? 'Calitate: ' + qual : 'Calitate: —';
}
function renderTickAge() {
  const q = S.quotes[CH.symbol];
  const el = $('#q-age');
  if (!q || !isNum(q.ageBase)) { el.textContent = '—'; el.className = ''; return; }
  const age = q.ageBase + (Date.now() - q.recvAt) / 1000;
  el.textContent = age < 60 ? age.toFixed(1) + 's' : fmtDur(age);
  const max = 10;
  el.className = age > max ? 'down' : '';
}

async function loadChartAnalysis() {
  if (!CH.symbol) return;
  try { CH.analysis = await api(`/analysis?symbol=${enc(CH.symbol)}`); }
  catch (e) { CH.analysis = null; if (e.status && e.status !== 404) toast(e.detail, 'err', 'Analiză'); }
  renderAnalysis(CH.analysis);
}
function probBars(p) {
  if (!p) return '<div class="faint">—</div>';
  const row = (lbl, v, col) => `<div class="prob"><span>${lbl}</span><span class="bar"><span style="width:${isNum(v) ? Math.round(v * 100) : 0}%;background:${col}"></span></span><span class="v">${fmtPct(v)}</span></div>`;
  return `<div class="probs">${row('Sus', p.up, 'var(--up)')}${row('Jos', p.down, 'var(--down)')}${row('Neutru', p.neutral, 'var(--text-faint)')}</div>`;
}
function levelsBlock(o, sym) {
  return `<div class="levels">
    <div><div class="l">Intrare</div><div class="v">${fmtPrice(o.entry, sym)}</div></div>
    <div><div class="l">SL</div><div class="v down">${fmtPrice(o.stop_loss, sym)}</div></div>
    <div><div class="l">TP</div><div class="v up">${fmtPrice(o.take_profit, sym)}</div></div>
    <div><div class="l">R:R</div><div class="v">${isNum(o.rr) ? o.rr.toFixed(2) : '—'}</div></div>
  </div>`;
}
function indicatorsKv(ind, sym) {
  if (!ind || typeof ind !== 'object' || !Object.keys(ind).length) return '<div class="faint">—</div>';
  const fmt = (k, v) => {
    if (!isNum(v)) return esc(v ?? '—');
    if (k === 'ema_fast' || k === 'ema_slow') return fmtPrice(v, sym);
    if (k === 'rsi') return v.toFixed(1);
    if (k === 'atr') return v.toFixed(digitsFor(sym));
    return Math.abs(v) < 0.01 ? v.toExponential(2) : v.toFixed(4);
  };
  const names = { ema_fast: 'EMA rapidă', ema_slow: 'EMA lentă', rsi: 'RSI', atr: 'ATR', macd_hist: 'MACD hist', htf_trend: 'Trend HTF' };
  return `<dl class="kv">${Object.entries(ind).map(([k, v]) => `<dt>${esc(names[k] || k)}</dt><dd class="num">${fmt(k, v)}</dd>`).join('')}</dl>`;
}
function renderAnalysis(a) {
  const box = $('#ch-analysis');
  if (!a || typeof a !== 'object') {
    box.innerHTML = emptyState('Nicio analiză încă', ' Analiza rulează la închiderea fiecărei bare, în sesiune. Poți apăsa „Analizează acum”.');
    return;
  }
  const sym = a.symbol;
  box.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:flex-end;gap:10px">
      <div><div class="eyebrow">${esc(sym)} · ${esc(a.timeframe || '')}</div><div class="dir-big ${esc(a.direction)}">${esc(a.direction || '—')}</div></div>
      <div style="text-align:right"><div class="eyebrow">Scor</div><div class="num" style="font-size:20px;font-weight:700">${isNum(a.score) ? fmtSigned(a.score, 2) : '—'}</div></div>
    </div>
    <div class="faint" style="font-size:12px;margin-top:2px">${fmtDT(a.time_utc)} · regim <b>${esc(a.regime || '—')}</b> · spread ${a.spread_points ?? '—'} pt</div>
    <hr class="section-sep">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap"><h3>Probabilități</h3><span class="disclaimer-tag">euristic, NEcalibrat</span></div>
    ${probBars(a.probabilities)}
    <div class="faint" style="font-size:11.5px;margin-top:6px">${esc(a.probability_method || '')}</div>
    <hr class="section-sep">
    ${levelsBlock(a, sym)}
    <hr class="section-sep">
    <h3>Motive</h3>
    ${Array.isArray(a.reasons) && a.reasons.length ? `<ul class="reasons">${a.reasons.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>` : '<div class="faint">—</div>'}
    <hr class="section-sep">
    <h3 style="margin-bottom:8px">Indicatori</h3>
    ${indicatorsKv(a.indicators, sym)}`;
}

/* ---------------- signals page ---------------- */
async function enterSignals() {
  await ensureSymbols();
  const sel = $('#sg-symbol');
  const cur = sel.value;
  sel.innerHTML = '<option value="">Toate</option>' + S.symbols.map((s) => `<option ${s.symbol === cur ? 'selected' : ''}>${esc(s.symbol)}</option>`).join('');
  await loadSignals();
}
async function loadSignals() {
  const sym = $('#sg-symbol').value, dir = $('#sg-direction').value;
  const box = $('#sg-table');
  try {
    const list = await api(`/signals?limit=100&symbol=${enc(sym)}&direction=${enc(dir)}`);
    S.signals = Array.isArray(list) ? list : [];
    box.innerHTML = signalsTable(S.signals, 'Niciun semnal', sym || dir ? 'Niciun rezultat pentru filtrele alese.' : 'Semnalele apar la închiderea barelor, în fereastra de sesiune.');
    $('#sg-count').textContent = `${S.signals.length} semnale`;
  } catch (e) { box.innerHTML = emptyState('Eroare la încărcare', ' ' + (e.detail || e.message)); }
}
const reloadSignalsDebounced = debounce(() => { if (S.route.page === 'signals') loadSignals(); }, 600);

function exportCsv() {
  if (!S.signals.length) { toast('Nu există semnale de exportat', 'warn'); return; }
  const cols = ['id', 'created_at_utc', 'symbol', 'timeframe', 'direction', 'bar_time', 'entry', 'stop_loss', 'take_profit', 'rr', 'spread_points', 'p_up', 'p_down', 'p_neutral', 'ai_status', 'ai_verdict', 'status', 'status_reason', 'mode', 'feed_label', 'strategy_version', 'intent_id', 'reasons'];
  const q = (v) => { const s = v === null || v === undefined ? '' : String(v); return /[",\n;]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
  const lines = [cols.join(',')];
  S.signals.forEach((s) => {
    const p = s.probabilities || {}, ai = s.ai || {};
    const row = { ...s, p_up: p.up, p_down: p.down, p_neutral: p.neutral, ai_status: ai.status, ai_verdict: ai.verdict, reasons: Array.isArray(s.reasons) ? s.reasons.join(' | ') : '' };
    lines.push(cols.map((c) => q(row[c])).join(','));
  });
  const blob = new Blob(['﻿' + lines.join('\n')], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `semnale_${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

/* ---------------- signal detail ---------------- */
const SD = { chart: null, s: null, id: null };
async function enterSignalDetail(id) {
  SD.id = id;
  const body = $('#sd-body');
  body.innerHTML = '<div class="loading">Se încarcă…</div>';
  let s;
  try { s = await api(`/signals/${enc(id)}`); } catch (e) {
    body.innerHTML = emptyState(e.status === 404 ? 'Semnal inexistent' : 'Eroare la încărcare', ' ' + (e.detail || e.message));
    $('#sd-title').textContent = 'Semnal'; $('#sd-sub').textContent = '';
    return;
  }
  if (SD.id !== id) return;
  await ensureSymbols();
  renderSignalDetail(s);
}
function renderSignalDetail(s) {
  const sym = s.symbol;
  $('#sd-title').innerHTML = `${esc(sym)} ${dirBadge(s.direction)} <span class="faint" style="font-size:14px;font-weight:500">${esc(s.timeframe || '')}</span>`;
  $('#sd-sub').textContent = `Creat ${fmtDT(s.created_at_utc)} · ID ${s.id}`;
  const ai = s.ai || {};
  const events = Array.isArray(s.events) ? s.events : [];
  $('#sd-body').innerHTML = `
    <div class="grid g-3">
      <div class="card span-2 flush">
        <div class="card-head"><h2>Grafic în jurul semnalului</h2><span class="sub">${esc(sym)} ${esc(s.timeframe || '')}</span></div>
        <div class="chart-box mini" id="sd-chart"><div class="chart-overlay hidden" id="sd-overlay"></div></div>
      </div>
      <div class="card">
        <div class="card-head"><h2>Nivele</h2>${statusBadge(s.status, SIGNAL_STATUS_CLS)}</div>
        ${levelsBlock(s, sym)}
        <hr class="section-sep">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap"><h3>Probabilități</h3><span class="disclaimer-tag">euristic, NEcalibrat</span></div>
        ${probBars(s.probabilities)}
        <hr class="section-sep">
        <dl class="kv">
          <dt>Motiv stare</dt><dd>${esc(s.status_reason || '—')}</dd>
          <dt>Mod</dt><dd>${modeBadge(s.mode)}</dd>
          <dt>Feed</dt><dd>${s.feed_label === 'SIMULATED' ? '<span class="badge sim">SIMULATED</span>' : esc(s.feed_label || '—')}</dd>
          <dt>Orizont</dt><dd>${s.horizon_minutes ?? '—'} min</dd>
          <dt>Expiră</dt><dd>${fmtDT(s.expires_at_utc)}</dd>
          <dt>Spread</dt><dd>${s.spread_points ?? '—'} pt</dd>
          <dt>Bara</dt><dd class="num">${isNum(s.bar_time) ? fmtDT(s.bar_time) : '—'}</dd>
          <dt>Strategie</dt><dd class="mono">${esc(s.strategy_version || '—')}</dd>
          <dt>Intenție</dt><dd class="mono">${s.intent_id ? esc(s.intent_id) : '—'}</dd>
        </dl>
      </div>
    </div>
    <div class="grid g-3">
      <div class="card">
        <div class="card-head"><h2>Verdict AI</h2>${verdictBadge(ai)}</div>
        <div class="ai-box ${esc(ai.verdict || '')}">
          <div class="faint" style="font-size:12px">${esc(ai.model || '—')} · stare ${esc(ai.status || '—')}</div>
          <p>${esc(ai.explanation || (ai.status === 'PENDING' ? 'Se așteaptă răspunsul Claude…' : ai.status === 'DISABLED' ? 'AI dezactivat.' : 'Fără explicație.'))}</p>
        </div>
        <h3 style="margin-top:14px">Riscuri semnalate</h3>
        ${Array.isArray(ai.risks) && ai.risks.length ? `<ul class="reasons">${ai.risks.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>` : '<div class="faint">—</div>'}
        <p class="faint" style="font-size:12px;margin:12px 0 0">Claude doar explică și poate face VETO; nu creează ordine și nu schimbă nivelele.</p>
      </div>
      <div class="card">
        <div class="card-head"><h2>Motive &amp; invalidare</h2></div>
        ${Array.isArray(s.reasons) && s.reasons.length ? `<ul class="reasons">${s.reasons.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>` : '<div class="faint">—</div>'}
        <h3 style="margin-top:14px">Invalidare</h3>
        <p class="dim" style="margin:4px 0 0">${esc(s.invalidation || '—')}</p>
        <hr class="section-sep">
        <h3 style="margin-bottom:8px">Indicatori</h3>
        ${indicatorsKv(s.indicators, sym)}
      </div>
      <div class="card">
        <div class="card-head"><h2>Cronologie evenimente</h2></div>
        ${events.length ? `<ul class="timeline">${events.map((e) => `<li><div class="tt num">${fmtDT(e.time_utc)}</div><div class="te">${esc(e.event_type)}</div><div class="td">${esc(typeof e.detail === 'object' ? JSON.stringify(e.detail) : (e.detail ?? ''))}</div></li>`).join('')}</ul>` : emptyState('Niciun eveniment', '')}
      </div>
    </div>`;
  renderSignalChart(s);
}
async function renderSignalChart(s) {
  if (SD.chart) { try { SD.chart.remove(); } catch (e) { /* ignore */ } SD.chart = null; }
  const el = $('#sd-chart');
  if (!el) return;
  const overlay = (t, m) => { const o = $('#sd-overlay'); if (o) { o.innerHTML = emptyState(t, ' ' + (m || '')); o.classList.remove('hidden'); } };
  SD.chart = makeChart(el);
  if (!SD.chart) { overlay('Biblioteca de grafic nu s-a încărcat', ''); return; }
  const tf = s.timeframe || 'M5';
  let d;
  try { d = await api(`/candles?symbol=${enc(s.symbol)}&tf=${enc(tf)}&limit=300`); } catch (e) { overlay('Nu s-au putut încărca lumânările', e.detail || e.message); return; }
  if (!SD.chart || SD.id !== s.id) return;
  const ser = addSeriesSet(SD.chart, s.symbol);
  SD.s = ser;
  const bars = cleanBars(d && d.bars);
  ser.candle.setData(bars);
  ser.emaF.setData(cleanLine(d && d.ema_fast));
  ser.emaS.setData(cleanLine(d && d.ema_slow));
  levelLine(ser.candle, s.entry, cssVar('--text-dim'), 'Intrare', 0);
  levelLine(ser.candle, s.stop_loss, cssVar('--down'), 'SL');
  levelLine(ser.candle, s.take_profit, cssVar('--up'), 'TP');
  if (!bars.length) { overlay('Nu există bare disponibile', ''); return; }
  ser.candle.setMarkers(signalMarkers([s], tf, bars));
  const sec = TF_SECONDS[tf] || 300;
  const t0 = isNum(s.bar_time) ? Math.floor(s.bar_time / sec) * sec : null;
  let idx = t0 !== null ? bars.findIndex((b) => b.time >= t0) : -1;
  if (t0 !== null && (idx < 0 || bars[0].time > t0)) {
    overlay('Bara semnalului nu mai este în istoricul încărcat', 'Se afișează ultimele bare disponibile.');
    idx = bars.length - 1;
  }
  if (idx < 0) idx = bars.length - 1;
  SD.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, idx - 50), to: Math.min(bars.length + 5, idx + 40) });
}

/* ---------------- positions page ---------------- */
async function loadPositionsQuiet() {
  try { S.positions = await api('/positions'); } catch (e) { /* keep last */ }
  applyPositionLines();
}
async function enterPositions() {
  await ensureSymbols();
  await Promise.all([loadPositionsQuiet(), loadIntents(), loadTrades()]);
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
async function loadIntents() {
  const box = $('#pos-intents');
  try {
    const list = await api('/intents?limit=100');
    box.innerHTML = Array.isArray(list) && list.length ? intentsTable(list, 'int') : emptyState('Nicio intenție de execuție', ' Intențiile apar când un semnal trece spre execuție (PAPER/DEMO).');
  } catch (e) { box.innerHTML = emptyState('Eroare la încărcare', ' ' + (e.detail || e.message)); }
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
            <dt>Semnal</dt><dd>${it.signal_id ? `<a href="#signal/${esc(it.signal_id)}">${esc(String(it.signal_id).slice(0, 8))}…</a>` : '—'}</dd>
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
      <td>${t.signal_id ? `<a href="#signal/${esc(t.signal_id)}">${esc(String(t.signal_id).slice(0, 8))}</a>` : '—'}</td>
    </tr>`).join('');
    const foot = `<tfoot><tr><td colspan="7">Total ${list.length} tranzacții · ${wins} câștigătoare · net ${fmtSigned(net)}</td>
      <td class="num ${pnlCls(pr)}">${fmtSigned(pr)}</td><td class="num">${fmtNum(cm)}</td><td class="num">${fmtNum(sw)}</td><td colspan="3"></td></tr></tfoot>`;
    box.innerHTML = table(['Închisă', 'Tichet', 'Simbol', 'Direcție', { t: 'Volum', cls: 'num' }, { t: 'Intrare', cls: 'num' }, { t: 'Ieșire', cls: 'num' }, { t: 'Profit', cls: 'num' }, { t: 'Comision', cls: 'num' }, { t: 'Swap', cls: 'num' }, 'Motiv', 'Mod', 'Semnal'], rows, { foot });
  } catch (e) { box.innerHTML = emptyState('Eroare la încărcare', ' ' + (e.detail || e.message)); }
}

/* ---------------- control page ---------------- */
const RISK_LABELS = {
  must_have_sl_tp: 'SL/TP obligatoriu', max_risk_per_trade_pct: 'Risc max / tranzacție (%)', max_daily_loss_pct: 'Pierdere max zilnică (%)',
  max_equity_drawdown_pct: 'Drawdown max equity (%)', max_open_positions: 'Poziții deschise max', max_trades_per_day: 'Tranzacții max / zi',
  max_consecutive_losses: 'Pierderi consecutive max', max_spread_points: 'Spread max (puncte)', max_slippage_points: 'Slippage max (puncte)', max_volume_lots: 'Volum max / ordin (loturi)', max_same_currency_direction: 'Max poziții pe monedă+direcție (0 = fără filtru)',
  min_free_margin_pct: 'Marjă liberă min (%)', max_tick_age_seconds: 'Vechime max tick (s)', max_signals_per_symbol_per_session: 'Semnale max / simbol / sesiune',
  news_blackout_minutes: 'Blocare știri ± minute (0 = dezactivat)', news_min_importance: 'Importanță minimă știre (1–3)',
  max_total_open_risk_pct: 'Risc total deschis max (% equity)', loss_streak_pause_minutes: 'Pauză după pierderi consecutive (min, 0 = restul zilei)',
  live_trading_allowed: 'LIVE permis', paper_start_balance: 'Balanță start PAPER',
};
const RISK_READONLY = new Set(['must_have_sl_tp', 'live_trading_allowed']);
const STRAT_LABELS = {
  signal_timeframe: 'Interval semnal', htf_timeframe: 'Interval HTF', symbols: 'Simboluri (separate prin virgulă)', atr_sl_mult: 'Multiplicator ATR pentru SL',
  atr_tp_mult: 'Multiplicator ATR pentru TP', min_sl_points: 'SL minim (puncte)', min_score: 'Scor minim (componente)', signal_expiry_minutes: 'Expirare semnal (min)', break_even_at_r: 'Break-even la R',
  trailing_enabled: 'Trailing stop', max_hold_minutes: 'Durată max poziție (min)', close_at_session_end: 'Închide la final de sesiune',
};
let riskOrig = null, stratOrig = null, schedOrig = null;

async function enterControl() {
  await ensureSymbols();
  const ts = $('#ctl-test-symbol');
  ts.innerHTML = S.symbols.length ? S.symbols.map((s) => `<option>${esc(s.symbol)}</option>`).join('') : '<option value="">(niciun simbol)</option>';
  renderControlState();
  await Promise.all([loadRiskForm(), loadSettingsForms()]);
}
function renderControlState() {
  const st = S.status;
  if (!st) return;
  const tr = st.trading || {};
  $$('#ctl-mode button').forEach((b) => b.classList.toggle('active', b.dataset.mode === tr.mode));
  $('#ctl-mode-sub').innerHTML = `curent: ${modeBadge(tr.mode)}`;
  const ex = $('#ctl-exec');
  if (document.activeElement !== ex) ex.checked = !!tr.execution_enabled;
  $('#ctl-exec-lbl').textContent = tr.execution_enabled ? 'ACTIVĂ' : 'Oprită';
  const pb = $('#ctl-pause');
  if (tr.entries_paused) { pb.className = 'btn lg success'; pb.textContent = 'Reia intrările noi'; }
  else { pb.className = 'btn lg danger'; pb.textContent = 'Oprește intrările noi (kill switch)'; }
  const ses = st.session || {};
  const sx = sessionText(ses);
  $('#ctl-session-d').textContent = `Acum: ${sx.label}${ses.override ? ' (manual: ' + ses.override + ')' : ' (automat)'} · ${ses.start || '—'}–${ses.end || '—'} ${ses.tz || ''}`;
  $$('#ctl-session button').forEach((b) => b.classList.toggle('active', (ses.override || 'auto') === b.dataset.act));
}

function fieldFor(key, val, labels, readonly) {
  const lbl = esc(labels[key] || key);
  const dis = readonly ? 'disabled' : '';
  if (typeof val === 'boolean') return `<label class="check-field"><input type="checkbox" name="${esc(key)}" ${val ? 'checked' : ''} ${dis}> ${lbl}${readonly ? ' <span class="faint">(fix în v1)</span>' : ''}</label>`;
  if (/timeframe$/.test(key)) return `<label class="field">${lbl}<select name="${esc(key)}" ${dis}>${ALL_TFS.map((t) => `<option ${t === val ? 'selected' : ''}>${t}</option>`).join('')}</select></label>`;
  if (Array.isArray(val)) return `<label class="field">${lbl}<input type="text" name="${esc(key)}" value="${esc(val.join(','))}" ${dis}></label>`;
  if (isNum(val)) return `<label class="field">${lbl}<input type="number" step="any" name="${esc(key)}" value="${esc(val)}" ${dis}></label>`;
  if (val !== null && typeof val === 'object') return '';
  return `<label class="field">${lbl}<input type="text" name="${esc(key)}" value="${esc(val ?? '')}" ${dis}></label>`;
}
function readForm(form, orig, readonlySet = new Set()) {
  const out = {};
  Object.keys(orig).forEach((k) => {
    if (readonlySet.has(k)) return;
    const el = form.elements[k];
    if (!el) return;
    const o = orig[k];
    let v;
    if (typeof o === 'boolean') v = el.checked;
    else if (Array.isArray(o)) v = el.value.split(',').map((x) => x.trim().toUpperCase()).filter(Boolean);
    else if (isNum(o)) { if (el.value === '') return; v = Number(el.value); if (!Number.isFinite(v)) return; }
    else v = el.value;
    if (JSON.stringify(v) !== JSON.stringify(o)) out[k] = v;
  });
  return out;
}
async function loadRiskForm() {
  const form = $('#risk-form');
  try {
    riskOrig = await api('/risk');
    renderRiskForm();
  } catch (e) { form.innerHTML = emptyState('Nu s-au putut încărca setările de risc', ' ' + (e.detail || e.message)); }
}
function renderRiskForm() {
  const form = $('#risk-form');
  if (!riskOrig || typeof riskOrig !== 'object') { form.innerHTML = emptyState('Setări de risc indisponibile', ''); return; }
  form.innerHTML = `<div class="form-grid">${Object.entries(riskOrig).map(([k, v]) => fieldFor(k, v, RISK_LABELS, RISK_READONLY.has(k))).join('')}</div>
    <div class="form-actions"><button type="button" class="btn ghost" data-reset="risk">Resetează</button><button type="submit" class="btn primary">Salvează riscul</button></div>`;
}
async function loadSettingsForms() {
  try { S.settings = await api('/settings'); } catch (e) {
    $('#strategy-form').innerHTML = emptyState('Nu s-au putut încărca setările', ' ' + (e.detail || e.message));
    $('#schedule-form').innerHTML = '';
    return;
  }
  stratOrig = (S.settings && S.settings.strategy) || null;
  schedOrig = (S.settings && S.settings.schedule) || null;
  renderStrategyForm(); renderScheduleForm();
}
function renderStrategyForm() {
  const form = $('#strategy-form');
  if (!stratOrig) { form.innerHTML = emptyState('Setări de strategie indisponibile', ''); return; }
  form.innerHTML = `<div class="form-grid">${Object.entries(stratOrig).map(([k, v]) => fieldFor(k, v, STRAT_LABELS, false)).join('')}</div>
    <div class="form-actions"><button type="button" class="btn ghost" data-reset="strategy">Resetează</button><button type="submit" class="btn primary">Salvează strategia</button></div>`;
}
function renderScheduleForm() {
  const form = $('#schedule-form');
  const s = schedOrig;
  if (!s) { form.innerHTML = emptyState('Program indisponibil', ''); return; }
  const days = Array.isArray(s.days) ? s.days : [];
  form.innerHTML = `
    <label class="check-field" style="padding-top:0;margin-bottom:12px"><input type="checkbox" name="enabled" ${s.enabled ? 'checked' : ''}> Fereastra de sesiune activă</label>
    <div class="form-grid">
      <label class="field">Început<input type="time" name="start" value="${esc(s.start || '')}"></label>
      <label class="field">Sfârșit<input type="time" name="end" value="${esc(s.end || '')}"></label>
      <label class="field">Fus orar (IANA)<input type="text" name="tz" value="${esc(s.tz || '')}"></label>
    </div>
    <div class="field" style="margin-top:12px;font-size:12px;color:var(--text-dim);font-weight:550">Zile
      <div class="days" style="margin-top:6px">${DAYS.map((d) => `<label><input type="checkbox" name="day" value="${d}" ${days.includes(d) ? 'checked' : ''}>${DAY_RO[d]}</label>`).join('')}</div>
    </div>
    <div class="form-actions"><button type="button" class="btn ghost" data-reset="schedule">Resetează</button><button type="submit" class="btn primary">Salvează programul</button></div>`;
}
async function putSettings(path, body, okMsg) {
  if (!Object.keys(body).length) { toast('Nicio modificare de salvat', 'info'); return null; }
  try {
    const r = await api(path, { method: 'PUT', body });
    toast(okMsg, 'ok');
    return r ?? {};
  } catch (e) { toast(e.detail || e.message, 'err', 'Eroare' + (e.status ? ` (${e.status})` : '')); return null; }
}
function initControl() {
  $('#ctl-mode').addEventListener('click', async (ev) => {
    const b = ev.target.closest('button[data-mode]');
    if (!b || b.disabled) return;
    const mode = b.dataset.mode;
    if (S.status && S.status.trading && S.status.trading.mode === mode) return;
    if (mode === 'DEMO' && !confirm('Treci în mod DEMO? Botul va putea trimite ordine reale pe contul DEMO MT5 (dacă execuția este activă).')) return;
    const r = await act('/control/mode', { mode }, `Mod schimbat în ${mode}`, b);
    if (r) refreshStatus();
  });
  $('#ctl-exec').addEventListener('change', async (ev) => {
    const enabled = ev.target.checked;
    if (enabled && !confirm('Activezi execuția? În PAPER/DEMO botul va deschide poziții când semnalele trec de toate verificările.')) { ev.target.checked = false; return; }
    const r = await act('/control/execution', { enabled }, enabled ? 'Execuție activată' : 'Execuție oprită');
    if (!r) ev.target.checked = !enabled;
    refreshStatus();
  });
  $('#ctl-pause').addEventListener('click', async (ev) => {
    const paused = S.status && S.status.trading && S.status.trading.entries_paused;
    const r = await act(paused ? '/control/resume-entries' : '/control/pause-entries', undefined, paused ? 'Intrările noi au fost reluate' : 'Intrările noi au fost OPRITE', ev.currentTarget);
    if (r) refreshStatus();
  });
  $('#ctl-close-all').addEventListener('click', async (ev) => {
    if (!confirm('Închizi TOATE pozițiile deschise de bot? Pozițiile manuale nu sunt atinse.')) return;
    const typed = prompt('Pentru confirmare scrie exact: CLOSE ALL');
    if (typed === null) return;
    if (typed.trim() !== 'CLOSE ALL') { toast('Text de confirmare incorect — nu s-a închis nimic', 'warn'); return; }
    const r = await act('/control/close-all', { confirm: 'CLOSE ALL' }, 'Cerere de închidere trimisă pentru toate pozițiile botului', ev.currentTarget);
    if (r) { loadPositionsQuiet(); reloadPositionsPage(); }
  });
  $('#ctl-session').addEventListener('click', async (ev) => {
    const b = ev.target.closest('button[data-act]');
    if (!b) return;
    const labels = { start: 'Sesiune pornită manual', stop: 'Sesiune oprită manual', auto: 'Sesiune în mod automat (program)' };
    const r = await act('/control/session', { action: b.dataset.act }, labels[b.dataset.act], b);
    if (r) refreshStatus();
  });
  $$('[data-test-side]').forEach((b) => b.addEventListener('click', async () => {
    const symbol = $('#ctl-test-symbol').value;
    const side = b.dataset.testSide;
    if (!symbol) { toast('Alege un simbol', 'warn'); return; }
    if (!confirm(`Trimiți un ordin de TEST ${side} pe ${symbol} (volum minim)?`)) return;
    const r = await act('/control/test-order', { symbol, side }, null, b);
    if (r) {
      const passed = r.risk_check && r.risk_check.passed;
      const failed = r.risk_check && Array.isArray(r.risk_check.checks) ? r.risk_check.checks.filter((c) => !c.ok).map((c) => c.name + (c.detail ? ': ' + c.detail : '')) : [];
      toast(passed ? `Intenție ${r.state || ''} creată (${String(r.id || '').slice(0, 8)})` : `Respins de Risk Engine: ${failed.join('; ') || r.state || ''}`, passed ? 'ok' : 'warn', 'Ordin de test');
    }
  }));
  $('#risk-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    if (!riskOrig) return;
    const r = await putSettings('/risk', readForm(ev.target, riskOrig, RISK_READONLY), 'Setările de risc au fost salvate');
    if (r) { if (r && typeof r === 'object' && Object.keys(r).length) { riskOrig = r; renderRiskForm(); } else loadRiskForm(); }
  });
  $('#strategy-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    if (!stratOrig) return;
    const r = await putSettings('/strategy', readForm(ev.target, stratOrig), 'Strategia a fost salvată');
    if (r) { loadSettingsForms(); S.symbolsLoadedAt = 0; }
  });
  $('#schedule-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    if (!schedOrig) return;
    const f = ev.target;
    const cur = { enabled: f.elements.enabled.checked, start: f.elements.start.value, end: f.elements.end.value, tz: f.elements.tz.value.trim(), days: $$('input[name="day"]:checked', f).map((x) => x.value) };
    const body = {};
    Object.keys(cur).forEach((k) => { if (JSON.stringify(cur[k]) !== JSON.stringify(schedOrig[k])) body[k] = cur[k]; });
    const r = await putSettings('/schedule', body, 'Programul a fost salvat');
    if (r) { loadSettingsForms(); refreshStatus(); }
  });
  document.addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-reset]');
    if (!b) return;
    if (b.dataset.reset === 'risk') renderRiskForm();
    if (b.dataset.reset === 'strategy') renderStrategyForm();
    if (b.dataset.reset === 'schedule') renderScheduleForm();
  });
}

/* ---------------- integrations ---------------- */
async function enterIntegrations() {
  if (!S.settings) { try { S.settings = await api('/settings'); } catch (e) { /* render from status */ } }
  renderIntegrations();
}
function renderIntegrations() {
  const st = S.status || {};
  const tg = st.telegram || {};
  const tgs = (S.settings && S.settings.telegram) || {};
  const tgLvl = !tg.enabled ? null : !tg.configured ? 'DEGRADED' : tg.last_error ? 'DEGRADED' : 'ONLINE';
  $('#int-telegram').innerHTML = `
    <div class="card-head"><h2>Telegram</h2>${tgLvl ? pill(tgLvl, tg.configured ? (tg.last_error ? 'EROARE' : 'ACTIV') : 'NECONFIGURAT') : '<span class="pill">DEZACTIVAT</span>'}</div>
    <dl class="kv">
      <dt>Activat</dt><dd>${tg.enabled ? 'da' : 'nu'} <span class="faint">(TELEGRAM_ENABLED)</span></dd>
      <dt>Configurat</dt><dd>${tg.configured ? '<span class="up">da</span>' : '<span class="warn-text">nu — lipsesc token/chat_id</span>'}</dd>
      <dt>Thread ID</dt><dd>${tgs.thread_id_set ? 'setat' : '—'}</dd>
      <dt>Ultimul trimis OK</dt><dd>${tg.last_ok_utc ? fmtDT(tg.last_ok_utc) : '—'}</dd>
      <dt>Ultima eroare</dt><dd class="${tg.last_error ? 'down' : ''}">${esc(tg.last_error || '—')}</dd>
      <dt>În coadă</dt><dd class="num">${tg.pending ?? '—'}</dd>
    </dl>
    <hr class="section-sep">
    <button class="btn primary" id="btn-tg-test">Trimite mesaj de test</button>
    <p class="faint" style="font-size:12px;margin:12px 0 0">Token-ul botului și chat_id se setează doar în fișierul <span class="mono">.env</span> (<span class="mono">TELEGRAM_BOT_TOKEN</span>, <span class="mono">TELEGRAM_CHAT_ID</span>, opțional <span class="mono">TELEGRAM_MESSAGE_THREAD_ID</span>) și nu sunt afișate niciodată în interfață. După modificare, repornește serverul.</p>`;
  const ai = st.ai || {};
  const ais = (S.settings && S.settings.ai) || {};
  const aiLvl = !ai.enabled ? null : !ai.key_set ? 'DEGRADED' : ai.last_error ? 'DEGRADED' : 'ONLINE';
  $('#int-claude').innerHTML = `
    <div class="card-head"><h2>Claude (Anthropic)</h2>${aiLvl ? pill(aiLvl, ai.key_set ? (ai.last_error ? 'EROARE' : 'ACTIV') : 'FĂRĂ CHEIE') : '<span class="pill">DEZACTIVAT</span>'}</div>
    <dl class="kv">
      <dt>Activat</dt><dd>${ai.enabled ? 'da' : 'nu'} <span class="faint">(CLAUDE_ENABLED)</span></dd>
      <dt>Model</dt><dd class="mono">${esc(ai.model || '—')}</dd>
      <dt>Veto</dt><dd>${ai.veto_enabled ? 'activ — un VETO transformă semnalul în NO_TRADE' : 'inactiv — verdictul e doar informativ'}</dd>
      <dt>Obligatoriu</dt><dd>${ais.required === undefined ? '—' : ais.required ? 'da — fără răspuns AI nu se tranzacționează' : 'nu — la eroare se continuă fără AI'}</dd>
      <dt>Cheie API</dt><dd>${ai.key_set ? '<span class="up">setată</span>' : '<span class="warn-text">lipsește</span>'}</dd>
      <dt>Ultimul OK</dt><dd>${ai.last_ok_utc ? fmtDT(ai.last_ok_utc) : '—'}</dd>
      <dt>Ultima eroare</dt><dd class="${ai.last_error ? 'down' : ''}">${esc(ai.last_error || '—')}</dd>
    </dl>
    <hr class="section-sep">
    <p class="faint" style="font-size:12px;margin:0">Cheia se setează doar în <span class="mono">.env</span> ca <span class="mono">ANTHROPIC_API_KEY</span> și nu este afișată niciodată. Claude nu creează ordine și nu modifică direcția sau nivelele: doar explică semnalul (AGREE / CAUTION / VETO) și poate reduce riscul prin VETO.</p>`;
}

/* ---------------- system ---------------- */
function healthTile(title, level, text, meta) {
  return `<div class="card status-tile"><div class="title">${esc(title)}</div><div>${pill(level, text)}</div><div class="meta">${meta}</div></div>`;
}
function renderHealth() {
  const st = S.status;
  const box = $('#sys-health');
  if (!st) { box.innerHTML = healthTile('API', 'OFFLINE', 'OFFLINE', 'Fără răspuns de la /status'); return; }
  const b = st.bridge || {}, eng = st.engine || {}, tg = st.telegram || {}, ai = st.ai || {};
  const loopAge = toDate(eng.last_loop_utc) ? (Date.now() - toDate(eng.last_loop_utc).getTime()) / 1000 : null;
  const engLvl = loopAge === null ? 'OFFLINE' : loopAge < 15 ? 'ONLINE' : loopAge < 60 ? 'DEGRADED' : 'OFFLINE';
  const tgLvl = !tg.enabled ? 'N/A' : !tg.configured || tg.last_error ? 'DEGRADED' : 'ONLINE';
  const aiLvl = !ai.enabled ? 'N/A' : !ai.key_set || ai.last_error ? 'DEGRADED' : 'ONLINE';
  const nw = st.news;
  const nwLvl = !nw ? 'N/A' : nw.blackout_minutes <= 0 ? 'N/A' : nw.ok ? 'ONLINE' : 'DEGRADED';
  const nwMeta = !nw ? 'Server fără calendar' : !nw.ok ? '<span class="warn-text">calendar indisponibil — blocarea pe știri nu se aplică</span>'
    : (nw.next_high ? `${esc(nw.next_high.ccy)} ${esc(nw.next_high.name || '')} ${fmtTime(nw.next_high.time_utc)}` : 'Nicio știre majoră') + ` · actualizat ${isNum(nw.age_s) ? 'acum ' + fmtDur(nw.age_s) : '—'}`;
  box.innerHTML = [
    healthTile('API', serviceLevel(), null, `Latență ${S.statusLatencyMs !== null ? S.statusLatencyMs + ' ms' : '—'} · SSE ${S.sse.connected ? 'conectat' : 'deconectat'}`),
    healthTile('Bridge MT5', bridgeLevel(st), null, b.last_sync_utc ? `Ultimul sync ${ago(b.last_sync_utc)}${isNum(b.age_s) ? ` (${b.age_s.toFixed(1)}s)` : ''}` : (st.data_source === 'sim' ? 'Feed simulat — bridge nefolosit' : 'Nicio sincronizare')),
    healthTile('Engine', engLvl, null, `Buclă ${eng.last_loop_utc ? ago(eng.last_loop_utc) : '—'} · analiză ${eng.last_analysis_utc ? ago(eng.last_analysis_utc) : '—'}`),
    healthTile('Telegram', tgLvl, tgLvl === 'N/A' ? 'DEZACTIVAT' : null, tg.last_error ? '<span class="down">' + esc(tg.last_error) + '</span>' : `OK ${tg.last_ok_utc ? ago(tg.last_ok_utc) : '—'} · coadă ${tg.pending ?? 0}`),
    healthTile('Claude', aiLvl, aiLvl === 'N/A' ? 'DEZACTIVAT' : null, ai.last_error ? '<span class="down">' + esc(ai.last_error) + '</span>' : `OK ${ai.last_ok_utc ? ago(ai.last_ok_utc) : '—'}`),
    healthTile('Știri economice', nwLvl, nwLvl === 'N/A' ? 'DEZACTIVAT' : nwLvl === 'DEGRADED' ? 'INDISPONIBIL' : null, nwMeta),
  ].join('');
}
async function loadLogs() {
  const lvl = $('#log-level').value;
  const box = $('#log-table');
  try {
    const list = await api(`/logs?limit=200&level=${enc(lvl)}`);
    renderLogs(Array.isArray(list) ? list : []);
  } catch (e) { box.innerHTML = emptyState('Eroare la încărcarea logurilor', ' ' + (e.detail || e.message)); }
}
function renderLogs(list) {
  $('#log-count').textContent = `${list.length} intrări`;
  $('#log-table').innerHTML = list.length ? table(['Ora', 'Nivel', 'Componentă', 'Mesaj'], list.map((l) => `<tr>
    <td class="num">${fmtDT(l.time_utc)}</td><td class="log-level-${esc(l.level)}">${esc(l.level)}</td>
    <td class="mono">${esc(l.component || '')}</td><td class="wrap">${esc(l.message || '')}</td></tr>`).join('')) : emptyState('Niciun log', '');
}
function enterSystem() {
  renderHealth();
  loadLogs();
  clearInterval(S.logsTimer);
  S.logsTimer = setInterval(() => { if (S.route.page === 'system' && $('#log-auto').checked) loadLogs(); }, 5000);
}
function leaveSystem() { clearInterval(S.logsTimer); }

/* ---------------- symbols ---------------- */
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
    if (S.route.page === 'chart') { onChartTick(t); if (t.symbol === CH.symbol) throttledQuote(); }
  });
  es.addEventListener('bar', (ev) => { const d = parse(ev); if (d && S.route.page === 'chart') onChartBar(d); });
  es.addEventListener('analysis', (ev) => {
    const a = parse(ev);
    if (!a) return;
    const info = symInfo(a.symbol); if (info) info.analysis = a;
    if (a.symbol === CH.symbol) { CH.analysis = a; if (S.route.page === 'chart') renderAnalysis(a); }
  });
  es.addEventListener('signal', (ev) => {
    const s = parse(ev);
    if (!s || !s.id) return;
    if (s.direction === 'BUY' || s.direction === 'SELL') toast(`${s.symbol} ${s.direction} @ ${fmtPrice(s.entry, s.symbol)} · ${s.status || ''}`, s.direction === 'BUY' ? 'ok' : 'err', 'Semnal');
    if (s.symbol === CH.symbol) { const i = CH.signals.findIndex((x) => x.id === s.id); if (i >= 0) CH.signals[i] = s; else CH.signals.unshift(s); applyChartMarkers(); }
    const p = S.route.page;
    if (p === 'overview') loadOverviewSignals();
    if (p === 'signals') reloadSignalsDebounced();
    if (p === 'signal' && S.route.arg === s.id) enterSignalDetail(s.id);
  });
  es.addEventListener('intent', (ev) => {
    const it = parse(ev);
    if (!it) return;
    if (S.route.page === 'positions') reloadPositionsPage();
    if (S.route.page === 'signal' && it.signal_id && it.signal_id === S.route.arg) enterSignalDetail(it.signal_id);
    if (it.state === 'UNKNOWN' || it.state === 'FAILED') toast(`${it.kind} ${it.symbol || ''}: ${it.state}`, 'warn', 'Intenție');
  });
  es.addEventListener('positions', (ev) => {
    const d = parse(ev);
    if (!d) return;
    S.positions = d;
    applyPositionLines();
    if (S.route.page === 'positions') renderPositions();
  });
  es.addEventListener('trade', (ev) => {
    const t = parse(ev);
    if (t) toast(`${t.symbol || ''} ${t.side || ''} închis (${t.close_reason || '—'}) · ${fmtSigned(t.profit)}`, isNum(t.profit) && t.profit >= 0 ? 'ok' : 'err', 'Tranzacție închisă');
    if (S.route.page === 'positions') reloadPositionsPage();
  });
  es.addEventListener('log', (ev) => {
    const l = parse(ev);
    if (!l || S.route.page !== 'system' || !$('#log-auto').checked) return;
    const lvl = $('#log-level').value;
    if (lvl && l.level !== lvl) return;
    debouncedLogs();
  });
}
const debouncedLogs = debounce(loadLogs, 800);
const throttledQuote = (() => { let t = 0; return () => { if (t) return; t = setTimeout(() => { t = 0; renderQuote(); }, 250); }; })();

async function resync() {
  await Promise.all([refreshStatus(), refreshSymbols(), loadPositionsQuiet()]);
  const p = S.route.page;
  if (p === 'chart') { loadChartData({ keepRange: true }); loadChartSignals(); loadChartAnalysis(); }
  if (p === 'overview') loadOverviewSignals();
  if (p === 'signals') loadSignals();
  if (p === 'positions') enterPositions();
}

/* ---------------- router ---------------- */
const PAGES = ['overview', 'chart', 'signals', 'signal', 'positions', 'control', 'integrations', 'system'];
function route() {
  const raw = decodeURIComponent((location.hash || '#overview').slice(1));
  let [page, ...rest] = raw.split('/');
  if (!PAGES.includes(page)) page = 'overview';
  const arg = rest.join('/') || null;
  const prev = S.route.page;
  if (prev === 'chart' && page !== 'chart') leaveChart();
  if (prev === 'system' && page !== 'system') leaveSystem();
  if (prev === 'signal' && page !== 'signal' && SD.chart) { try { SD.chart.remove(); } catch (e) { /* ignore */ } SD.chart = null; }
  S.route = { page, arg };
  $$('.page').forEach((s) => { s.hidden = s.id !== 'page-' + page; });
  const navPage = page === 'signal' ? 'signals' : page;
  $$('#nav a').forEach((a) => a.classList.toggle('active', a.dataset.page === navPage));
  $('#nav').classList.remove('open');
  $('#menu-btn').setAttribute('aria-expanded', 'false');
  window.scrollTo(0, 0);
  switch (page) {
    case 'overview': renderOverview(); loadOverviewSignals(); break;
    case 'chart': enterChart(); break;
    case 'signals': enterSignals(); break;
    case 'signal': if (arg) enterSignalDetail(arg); break;
    case 'positions': enterPositions(); break;
    case 'control': enterControl(); break;
    case 'integrations': enterIntegrations(); break;
    case 'system': enterSystem(); break;
  }
}

function applyChartThemes() {
  const opts = chartThemeOptions();
  [CH.chart, SD.chart].forEach((c) => { if (c) c.applyOptions(opts); });
  [CH.s, SD.s].forEach((s) => {
    if (!s) return;
    s.candle.applyOptions({ upColor: cssVar('--up'), downColor: cssVar('--down'), wickUpColor: cssVar('--up'), wickDownColor: cssVar('--down') });
    s.emaF.applyOptions({ color: cssVar('--accent') });
    s.emaS.applyOptions({ color: cssVar('--violet') });
  });
  applyChartMarkers();
  applyPositionLines();
}

/* ---------------- init ---------------- */
function init() {
  initAdminModal();
  initControl();
  $('#theme-btn').addEventListener('click', toggleTheme);
  if (window.matchMedia) {
    const mq = window.matchMedia('(prefers-color-scheme: light)');
    const h = () => { if (!document.documentElement.getAttribute('data-theme')) applyChartThemes(); };
    if (mq.addEventListener) mq.addEventListener('change', h);
  }
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
  $('#ch-symbol').addEventListener('change', (ev) => {
    CH.symbol = ev.target.value; lsSet('mt5bot.chartSymbol', CH.symbol);
    CH.analysis = null; renderQuote();
    Promise.all([loadChartData(), loadChartAnalysis(), loadChartSignals()]);
  });
  $('#ch-tf').addEventListener('click', (ev) => {
    const b = ev.target.closest('button[data-tf]');
    if (!b || b.dataset.tf === CH.tf) return;
    CH.tf = b.dataset.tf; lsSet('mt5bot.chartTf', CH.tf);
    $$('#ch-tf button').forEach((x) => x.classList.toggle('active', x === b));
    loadChartData();
  });
  $('#btn-analyze').addEventListener('click', async (ev) => {
    if (!CH.symbol) { toast('Niciun simbol selectat', 'warn'); return; }
    const a = await act('/control/analyze-now', { symbol: CH.symbol }, null, ev.currentTarget);
    if (a && a.symbol) { CH.analysis = a; renderAnalysis(a); toast(`${a.symbol}: ${a.direction}`, 'info', 'Analiză finalizată'); }
  });
  $('#sg-symbol').addEventListener('change', loadSignals);
  $('#sg-direction').addEventListener('change', loadSignals);
  $('#btn-csv').addEventListener('click', exportCsv);
  $('#btn-pos-refresh').addEventListener('click', enterPositions);
  $('#log-level').addEventListener('change', loadLogs);
  $('#log-refresh').addEventListener('click', loadLogs);
  document.addEventListener('click', async (ev) => {
    const b = ev.target.closest('#btn-tg-test');
    if (!b) return;
    const r = await act('/telegram/test', undefined, null, b);
    if (r) { if (r.ok) toast('Mesaj de test trimis pe Telegram', 'ok'); else toast(r.error || 'Trimitere eșuată', 'err', 'Telegram'); }
    refreshStatus();
  });

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
    if (p === 'chart') renderTickAge();
  }, 1000);
  setInterval(() => { if (!S.sse.connected) refreshStatus(); }, 5000);

  refreshStatus().then(() => refreshSymbols());
  route();
  setSse('warn');
  connectSSE();
}

document.addEventListener('DOMContentLoaded', init);
