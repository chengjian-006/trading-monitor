// ==UserScript==
// @name         问财观点上报 (chat智能调度)
// @namespace    guxiaocha-wencai-opinion
// @version      1.6
// @description  在 www.iwencai.com 登录态下, 口语问一句(chat 智能调度 aime stream-query SSE), Shadow DOM 浮层实时渲染答案(流式markdown/可折叠思考), 完成后出「研判速览」决策卡(主推+买入/止盈/止损价位磁贴+距现价%)并上报股小察落「问财观点」。零配置。
// @match        https://www.iwencai.com/*
// @match        http://www.iwencai.com/*
// @grant        GM_registerMenuCommand
// @grant        GM_xmlhttpRequest
// @connect      124.71.75.5
// @connect      localhost
// @run-at       document-idle
// @updateURL    http://124.71.75.5/api/wencai/userscript.user.js
// @downloadURL  http://124.71.75.5/api/wencai/userscript.user.js
// ==/UserScript==

(function () {
  'use strict';

  // ============ 可调(默认零配置即用) ============
  const SERVER_URL = 'http://124.71.75.5';   // 股小察后端(本地调试填 http://localhost:8000)
  const DEEP_RESEARCH = false;   // false=普通agent(每天约188次) / true=深度研究(每天约10次, 更深但卡额度)
  const DEFAULT_Q = '给我推荐一只股票,目前处于买入区间,持股一周以内,盈利7%以上';
  // ============================================

  const CSS = `
  :host { all: initial; }
  * { box-sizing: border-box; }
  .wrap { position: fixed; right: 20px; bottom: 20px; z-index: 2147483647;
    width: min(468px, 92vw); max-height: 84vh; display: flex; flex-direction: column;
    background: #fff; border-radius: 18px; overflow: hidden; box-shadow: 0 20px 54px rgba(40,60,110,.30);
    border: 1px solid #e6eaf4; font-family: -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
    font-variant-numeric: tabular-nums; color:#131b2b; animation: pop .22s cubic-bezier(.2,.8,.3,1); }
  @keyframes pop { from { opacity: 0; transform: translateY(12px) scale(.98); } to { opacity: 1; transform: none; } }
  .wrap.min { max-height: none; width: auto; }
  .wrap.min .q, .wrap.min .rz, .wrap.min .body, .wrap.min .ft { display: none; }
  .hd { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 13px 15px;
    background: linear-gradient(135deg,#3d4ee0,#2a37b8); color: #fff; cursor: grab; user-select: none; }
  .hd:active { cursor: grabbing; }
  .hd .l { display: flex; align-items: center; gap: 8px; font-weight: 700; font-size: 14px; white-space: nowrap; letter-spacing:.02em; }
  .pill { font-size: 11px; font-weight: 600; padding: 3px 10px; border-radius: 99px; background: rgba(255,255,255,.18); color:#fff; white-space: nowrap; }
  .pill.blue { background:#2563eb; } .pill.amber { background:#c2820a; } .pill.green { background:#12894e; } .pill.red { background:#d3453b; }
  .dot { display:inline-block; width:6px; height:6px; border-radius:50%; background:currentColor; margin-right:5px; animation: blink 1s infinite; }
  @keyframes blink { 0%,100%{opacity:1} 50%{opacity:.25} }
  .btns { display: flex; align-items: center; gap: 4px; }
  .ic { cursor: pointer; width: 24px; height: 24px; display:flex; align-items:center; justify-content:center;
    border-radius: 7px; color: rgba(255,255,255,.7); font-size: 16px; line-height: 1; transition:.15s; }
  .ic:hover { color:#fff; background: rgba(255,255,255,.16); }
  .q { padding: 10px 16px; font-size: 12.5px; color: #64748b; background: #f6f8fd; border-bottom: 1px solid #eef2f9; }
  .q b { color:#334155; }
  .rz { border-bottom: 1px solid #eef2f9; background:#fbfcfe; }
  .rz-h { padding: 8px 16px; font-size: 12px; color:#64748b; cursor: pointer; display:flex; align-items:center; gap:6px; user-select:none; }
  .rz-h:hover { color:#334155; } .rz-h .ar { transition: transform .18s; font-size:10px; } .rz.open .rz-h .ar { transform: rotate(90deg); }
  .rz-b { display:none; padding: 0 16px 10px; font-size: 12px; line-height: 1.7; color:#94a3b8; max-height: 180px; overflow-y:auto; white-space: pre-wrap; }
  .rz.open .rz-b { display:block; }
  .body { flex: 1; overflow-y: auto; padding: 14px 16px; font-size: 14px; line-height: 1.8; color: #1f2937; word-break: break-word; }
  .body::-webkit-scrollbar, .rz-b::-webkit-scrollbar { width: 8px; }
  .body::-webkit-scrollbar-thumb, .rz-b::-webkit-scrollbar-thumb { background:#d9e0f0; border-radius: 8px; }
  .body h1,.body h2,.body h3,.body h4 { margin: 14px 0 6px; line-height:1.4; color:#131b2b; font-weight:800; }
  .body h1 { font-size: 16px; } .body h2 { font-size: 15px; } .body h3,.body h4 { font-size: 14px; }
  .body p { margin: 6px 0; } .body p.num { margin: 10px 0 4px; color:#131b2b; font-weight:700; }
  .body ul { margin: 4px 0; padding-left: 20px; } .body li { margin: 3px 0; }
  .body strong { color:#131b2b; font-weight: 800; }
  .body code { background:#eff2f9; padding: 1px 5px; border-radius: 4px; font-size: 12.5px; }
  .body sup { color:#8791a6; font-size:11px; } .body .think { color:#94a3b8; }
  .cursor { display:inline-block; width:7px; height:15px; background:#3d4ee0; margin-left:2px; vertical-align:-2px; animation: blink .8s steps(1) infinite; border-radius:1px; }
  /* 决策卡 */
  .dcard { border:1px solid #e6eaf4; border-radius:14px; padding:14px; margin-bottom:12px; background:#fbfcff; }
  .d-top { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }
  .d-eye { font-size:10px; font-weight:800; letter-spacing:.12em; text-transform:uppercase; color:#8791a6; }
  .d-name { font-size:22px; font-weight:800; color:#131b2b; margin-top:5px; letter-spacing:-.02em; }
  .d-name .d-code { font-size:13px; color:#8791a6; font-weight:600; margin-left:8px; }
  .d-quote { flex-shrink:0; font-size:11.5px; font-weight:700; color:#fff; background:#3d4ee0; text-decoration:none; padding:7px 12px; border-radius:9px; white-space:nowrap; }
  .d-cur { margin-top:9px; font-size:12px; color:#8791a6; } .d-cur b { color:#131b2b; font-weight:800; font-size:15px; }
  .chg.up { color:#d3453b; } .chg.down { color:#12894e; }
  .tiles { display:grid; grid-template-columns:repeat(3,1fr); gap:8px; margin:12px 0 0; }
  .tile { background:#f4f6fc; border:1px solid #e6eaf4; border-radius:11px; padding:10px; }
  .t-k { font-size:9.5px; font-weight:800; letter-spacing:.04em; text-transform:uppercase; color:#8791a6; }
  .t-num { font-size:19px; font-weight:800; color:#131b2b; margin-top:6px; line-height:1; display:flex; align-items:baseline; gap:6px; flex-wrap:wrap; }
  .t-d { font-size:11px; font-weight:800; } .t-d.up { color:#d3453b; } .t-d.down { color:#12894e; }
  .t-bar { height:4px; border-radius:99px; margin-top:8px; background:#e6eaf4; overflow:hidden; } .t-bar i { display:block; height:100%; }
  .tile.buy .t-bar i { background:#12894e; } .tile.tp .t-bar i { background:#3d4ee0; } .tile.sl .t-bar i { background:#d3453b; }
  .t-cap { font-size:11px; color:#40495c; line-height:1.5; margin-top:8px; } .t-only { font-size:12.5px; color:#131b2b; font-weight:600; margin-top:6px; }
  .thesis { display:flex; flex-direction:column; gap:7px; margin-top:12px; }
  .th { display:flex; gap:9px; padding:9px 11px; border-radius:10px; background:#f4f6fc; font-size:12.5px; line-height:1.55; }
  .th.risk { background:#fdeceb; } .th-k { flex-shrink:0; font-weight:800; width:30px; color:#12894e; } .th.risk .th-k { color:#d3453b; }
  .full { border-top:1px solid #eef2f9; margin-top:4px; }
  .full-h { font-size:12px; color:#64748b; cursor:pointer; display:flex; align-items:center; gap:6px; user-select:none; padding:10px 0 4px; } .full-h:hover { color:#334155; }
  .full-h .ar { font-size:10px; transition:.18s; } .full.open .full-h .ar { transform:rotate(90deg); }
  .full-b { display:none; } .full.open .full-b { display:block; }
  .md-table { width:100%; border-collapse:collapse; margin:10px 0; font-size:12.5px; }
  .md-table th { text-align:left; font-size:9.5px; font-weight:800; letter-spacing:.08em; text-transform:uppercase; color:#8791a6; padding:0 10px 8px 0; border-bottom:2px solid #e6eaf4; }
  .md-table td { padding:9px 10px 9px 0; border-bottom:1px solid #eff2f9; vertical-align:top; line-height:1.55; }
  .md-table tr:last-child td { border-bottom:0; } .md-table td:first-child { font-weight:800; color:#3d4ee0; white-space:nowrap; }
  .ft { padding: 11px 16px; border-top: 1px solid #eef2f9; font-size: 12.5px; color: #64748b; background: #fafbfe; }
  .ft .lbl { color:#94a3b8; }
  .chip { display:inline-block; padding: 3px 10px; margin: 3px 6px 3px 0; border-radius: 8px; font-size: 12.5px;
    background:#eef2f9; color:#475569; border:1px solid #e2e8f0; }
  .chip.hot { background:#eef1fb; color:#3d4ee0; border-color:#d9e0f7; font-weight:700; }
  .src { margin-top: 6px; font-size: 11.5px; color:#94a3b8; }
  .btn { display:inline-block; margin-top:10px; padding: 8px 16px; background:#3d4ee0; color:#fff; border-radius: 9px;
    text-decoration:none; font-size: 12.5px; font-weight:700; transition:.15s; } .btn:hover { filter:brightness(1.06); }
  `;

  // ---------- 内核: markdown(含表格) / 结论抽取 / 价位提取 ----------
  function esc(s) { return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  function inlineMd(s) { return esc(s).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/`([^`]+)`/g, '<code>$1</code>'); }
  function stripEmbeds(text) {
    if (!text) return text || '';
    let t = String(text);
    t = t.replace(/```\s*visual[\s\S]*?```/gi, '').replace(/```\s*visual[\s\S]*$/i, '');
    t = t.replace(/```[\s\S]*?```/g, (m) => (/"uuid"\s*:/.test(m) ? '' : m));
    return t.replace(/\n{3,}/g, '\n\n').trim();
  }
  const isTableSep = (s) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/.test(s || '');
  const tableCells = (s) => String(s || '').replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map((x) => x.trim());
  function mdRender(src) {
    const lines = stripEmbeds(src).split('\n'); let html = '', inList = false, m;
    const closeList = () => { if (inList) { html += '</ul>'; inList = false; } };
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].replace(/\s+$/, '');
      if (line.indexOf('|') >= 0 && isTableSep(lines[i + 1])) {
        closeList();
        const head = tableCells(line); let rows = ''; i += 2;
        for (; i < lines.length; i++) {
          if (lines[i].indexOf('|') < 0 || !lines[i].trim()) { i--; break; }
          rows += '<tr>' + tableCells(lines[i]).map((c) => '<td>' + inlineMd(c) + '</td>').join('') + '</tr>';
        }
        html += '<table class="md-table"><thead><tr>' + head.map((h) => '<th>' + inlineMd(h) + '</th>').join('') + '</tr></thead><tbody>' + rows + '</tbody></table>';
        continue;
      }
      if (!line.trim()) { closeList(); continue; }
      if (/^```/.test(line)) { closeList(); continue; }
      if (/^(-{3,}|\*{3,}|_{3,})$/.test(line.replace(/\s/g, ''))) { closeList(); html += '<hr>'; continue; }
      if ((m = line.match(/^(#{1,4})\s+(.*)$/))) { closeList(); const lv = m[1].length; html += '<h' + lv + '>' + inlineMd(m[2]) + '</h' + lv + '>'; }
      else if ((m = line.match(/^\s*[-•·]\s+(.*)$/))) { if (!inList) { html += '<ul>'; inList = true; } html += '<li>' + inlineMd(m[1]) + '</li>'; }
      else if ((m = line.match(/^\s*(\d{1,2})[.、]\s+(.*)$/))) { closeList(); html += '<p class="num"><strong>' + m[1] + '. </strong>' + inlineMd(m[2]) + '</p>'; }
      else { closeList(); html += '<p>' + inlineMd(line) + '</p>'; }
    }
    closeList(); return html;
  }

  const FORMAT_SUFFIX = '\n\n请在回答的最末尾，另起一行，用严格固定格式补一条结论小结（某项没有就填「-」，不要额外解释、不要加粗）：\n【标的】x【买点】x【止盈】x【止损】x【周期】x【逻辑】x【风险】x';
  const MK = { 标的: 'stock', 买点: 'buy', 止盈: 'takeProfit', 止损: 'stopLoss', 周期: 'period', 逻辑: 'logic', 风险: 'risk' };
  function parseMarkers(text) {
    if (!text || text.indexOf('【标的】') < 0) return null;
    const out = {}; let hit = 0;
    for (const k of Object.keys(MK)) {
      const m = text.match(new RegExp('【' + k + '】\\s*([\\s\\S]*?)(?=【[标买止周逻风]|\\n\\n|$)'));
      if (m) { const v = m[1].replace(/[*`]/g, '').trim(); out[MK[k]] = (v === '-' || v === '—' || v === '无') ? '' : v.slice(0, 80); hit++; }
    }
    return hit >= 3 ? out : null;
  }
  function parseTableConc(text) {
    const out = {}; let hit = 0;
    for (const ln of String(text || '').split('\n')) {
      if (ln.indexOf('|') < 0) continue;
      const cs = tableCells(ln).map((x) => x.replace(/[*`]/g, '').trim());
      if (cs.length < 2) continue;
      const key = MK[cs[0]], val = cs[1];
      if (key && val && !/^[-—]+$/.test(val) && out[key] === undefined) { out[key] = val.slice(0, 120); hit++; }
    }
    return hit >= 3 ? out : null;
  }
  function stripMarkerLine(text) { return (text || '').replace(/\n*【标的】[\s\S]*$/, '').trim(); }
  function clipC(s) { s = (s || '').replace(/\*\*/g, '').replace(/^[\s\-•·]+/, '').replace(/\s+/g, ' ').trim(); return s.length > 80 ? s.slice(0, 80) + '…' : s; }
  function sentenceOf(text, kw) {
    const arr = text.match(new RegExp('[^。\\n;；！|]*(?:' + kw + ')[^。\\n;；！|]*', 'g'));
    if (!arr) return '';
    arr.sort((a, b) => (/[\d]/.test(b) ? 1 : 0) - (/[\d]/.test(a) ? 1 : 0));
    return arr[0];
  }
  function extractConclusion(answer, stockItems) {
    const stockStr = (stockItems && stockItems[0]) ? (stockItems[0].name + (stockItems[0].code ? ' (' + stockItems[0].code + ')' : '')) : '';
    const marked = parseMarkers(answer);
    if (marked) { if (!marked.stock) marked.stock = stockStr; return marked; }
    const tbl = parseTableConc(answer);
    if (tbl) { if (!tbl.stock) tbl.stock = stockStr; return tbl; }
    const t = stripEmbeds(answer || '');
    const period = (t.match(/(?:持股|持有|周期)[^。\n]{0,12}?(一周|半个?月|\d+\s*(?:天|日|周|个月))/) || [])[0] || '';
    return {
      stock: stockStr,
      buy: clipC(sentenceOf(t, '买点|买入价|买入区间|建仓|回踩[^。\\n]{0,8}买|低吸')),
      takeProfit: clipC(sentenceOf(t, '止盈|目标价|目标位|目标先?看')),
      stopLoss: clipC(sentenceOf(t, '止损|撤退价?|防守位?|跌破[^。\\n]{0,8}(?:清|走|撤|止)')),
      period: clipC(period),
      logic: clipC(sentenceOf(t, '一句话策略|核心逻辑|逻辑[:：]|之所以|受益于|催化')),
      risk: clipC(sentenceOf(t, '风险提示|需(?:要)?警惕|风险[:：]|若跌破|利空|不及预期')),
    };
  }
  function cleanConcVal(s, label) {
    let v = String(s || '').replace(/[*`]/g, '').trim();
    v = v.replace(/^\|+/, '').replace(/\|+$/, '').trim();
    if (label) v = v.replace(new RegExp('^' + label + '\\s*[|｜:：]?\\s*'), '');
    return v.replace(/\s*\|\s*/g, ' ').replace(/\s+/g, ' ').trim();
  }
  function extractPrice(text) {
    const t = String(text || '').replace(/[,，]/g, ''); let m;
    if ((m = t.match(/(\d{2,5}(?:\.\d+)?)\s*[—~\-–]\s*(\d{2,5}(?:\.\d+)?)\s*元/))) return { lo: +m[1], hi: +m[2] };
    if ((m = t.match(/(\d{2,5}(?:\.\d+)?)\s*元/))) return { lo: +m[1], hi: +m[1] };
    if ((m = t.match(/(\d{2,5}(?:\.\d+)?)\s*[—~–]\s*(\d{2,5}(?:\.\d+)?)/))) return { lo: +m[1], hi: +m[2] };
    const re = /(\d{2,5}(?:\.\d+)?)(?!\s*(?:%|％|天|日|周|个?月|年|倍|万|亿|手))/g;
    while ((m = re.exec(t))) { const v = +m[1]; if (v >= 2 && v <= 100000) return { lo: v, hi: v }; }
    return null;
  }
  const fmtN = (v) => Number.isInteger(v) ? String(v) : String(+v.toFixed(2));
  const signPct = (p) => (p >= 0 ? '+' : '−') + Math.abs(p).toFixed(1) + '%';

  // ---------- 浮层 ----------
  let P = null;
  function panel() {
    if (P && P.host.isConnected) return P;
    const host = document.createElement('div');
    const sh = host.attachShadow({ mode: 'open' });
    sh.innerHTML = `<style>${CSS}</style>
      <div class="wrap" id="wrap">
        <div class="hd" id="hd">
          <div class="l">💡 问财观点 <span class="pill" id="pill">准备中</span></div>
          <div class="btns"><div class="ic" id="min" title="最小化">–</div><div class="ic" id="x" title="关闭">×</div></div>
        </div>
        <div class="q" id="q"></div>
        <div class="rz" id="rz" style="display:none"><div class="rz-h" id="rzh"><span class="ar">▸</span>思考过程</div><div class="rz-b" id="rzb"></div></div>
        <div class="body" id="body">…</div>
        <div class="ft" id="ft" style="display:none"></div>
      </div>`;
    (document.documentElement || document.body).appendChild(host);
    const $ = (id) => sh.getElementById(id);
    const wrap = $('wrap');
    $('x').onclick = () => host.remove();
    $('min').onclick = (e) => { e.stopPropagation(); wrap.classList.toggle('min'); $('min').textContent = wrap.classList.contains('min') ? '▢' : '–'; };
    $('rzh').onclick = () => $('rz').classList.toggle('open');
    let drag = false, sx = 0, sy = 0, ox = 0, oy = 0;
    $('hd').addEventListener('mousedown', (e) => {
      if (e.target.closest('.btns')) return;
      drag = true; const r = wrap.getBoundingClientRect();
      wrap.style.left = r.left + 'px'; wrap.style.top = r.top + 'px'; wrap.style.right = 'auto'; wrap.style.bottom = 'auto';
      sx = e.clientX; sy = e.clientY; ox = r.left; oy = r.top; e.preventDefault();
    });
    window.addEventListener('mousemove', (e) => { if (!drag) return; wrap.style.left = Math.max(0, ox + e.clientX - sx) + 'px'; wrap.style.top = Math.max(0, oy + e.clientY - sy) + 'px'; });
    window.addEventListener('mouseup', () => { drag = false; });
    P = { host, sh, wrap, pill: $('pill'), q: $('q'), rz: $('rz'), rzb: $('rzb'), body: $('body'), ft: $('ft') };
    return P;
  }
  function setStage(text, cls, live) { const p = panel(); p.pill.className = 'pill ' + (cls || ''); p.pill.innerHTML = (live ? '<span class="dot"></span>' : '') + text; }
  function setQuestion(q) { panel().q.innerHTML = '问：<b>' + esc(q) + '</b>'; }
  function setReasoning(text) { const p = panel(); if (!text) { p.rz.style.display = 'none'; return; } p.rz.style.display = 'block'; p.rzb.textContent = text; }
  function setBodyText(t) { panel().body.innerHTML = '<span class="think">' + esc(t) + '</span>'; }
  function setBodyMd(md, cursor) {
    const p = panel(); const near = p.body.scrollHeight - p.body.scrollTop - p.body.clientHeight < 70;
    p.body.innerHTML = mdRender(md) + (cursor ? '<span class="cursor"></span>' : '');
    if (near) p.body.scrollTop = p.body.scrollHeight;
  }
  function setFoot(html) { const p = panel(); p.ft.style.display = 'block'; p.ft.innerHTML = html; }

  // 决策卡渲染(cur=现价, 可空; 空则不显示%)
  function tileHtml(cls, cn, en, valText, cur) {
    const pr = extractPrice(valText);
    const k = '<div class="t-k">' + cn + ' ' + en + '</div>';
    if (!pr) return '<div class="tile ' + cls + '">' + k + '<div class="t-only">' + esc(valText || '—') + '</div></div>';
    const num = pr.lo === pr.hi ? fmtN(pr.lo) : fmtN(pr.lo) + '–' + fmtN(pr.hi);
    let delta = '', bar = '';
    if (cur) {
      const loP = (pr.lo - cur) / cur * 100, hiP = (pr.hi - cur) / cur * 100, mid = (loP + hiP) / 2;
      const dir = mid >= 0 ? 'up' : 'down', arrow = mid >= 0 ? '↑' : '↓';
      delta = '<span class="t-d ' + dir + '">' + arrow + ' ' + (pr.lo === pr.hi ? signPct(loP) : signPct(loP) + '~' + signPct(hiP)) + '</span>';
      bar = '<div class="t-bar"><i style="width:' + Math.max(6, Math.min(100, Math.abs(mid) / 10 * 100)).toFixed(0) + '%"></i></div>';
    }
    return '<div class="tile ' + cls + '">' + k + '<div class="t-num">' + num + delta + '</div>' + bar + '<div class="t-cap">' + esc(valText) + '</div></div>';
  }
  function renderResult(state, cur) {
    const p = panel();
    const { question, displayAnswer, rawAnswer, items, sources } = state;
    const fresh = extractConclusion(rawAnswer, items);
    const v = (k, label) => cleanConcVal(fresh[k] || '', label);
    const c = { buy: v('buy', '买点'), takeProfit: v('takeProfit', '止盈'), stopLoss: v('stopLoss', '止损'), period: v('period', '周期'), logic: v('logic', '逻辑'), risk: v('risk', '风险') };
    const top = (items || [])[0] || {};
    const name = top.name || cleanConcVal(fresh.stock || '', '标的').replace(/\s*\(.*$/, '');
    const code = top.code || '';

    let html = '';
    if (name || c.buy || c.takeProfit || c.stopLoss || c.logic || c.risk) {
      html += '<div class="dcard"><div class="d-top"><div>'
        + '<div class="d-eye">主推标的 · Top Pick</div>'
        + '<div class="d-name">' + esc(name || '—') + (code ? '<span class="d-code">' + esc(code) + '</span>' : '') + '</div></div>'
        + (code ? '<a class="d-quote" href="https://stockpage.10jqka.com.cn/' + esc(code) + '/" target="_blank">看行情 →</a>' : '') + '</div>';
      if (cur != null) html += '<div class="d-cur">现价 <b>' + fmtN(cur) + '</b> <span style="color:#8791a6">· 距下列价位</span></div>';
      const tiles = tileHtml('buy', '买入', 'BUY', c.buy, cur) + tileHtml('tp', '止盈', 'TARGET', c.takeProfit, cur) + tileHtml('sl', '止损', 'STOP', c.stopLoss, cur);
      html += '<div class="tiles">' + tiles + '</div>';
      let th = '';
      if (c.logic) th += '<div class="th"><span class="th-k">逻辑</span><span>' + esc(c.logic) + '</span></div>';
      if (c.risk) th += '<div class="th risk"><span class="th-k">风险</span><span>' + esc(c.risk) + '</span></div>';
      if (th) html += '<div class="thesis">' + th + '</div>';
      html += '</div>';
    }
    html += '<div class="full open"><div class="full-h" id="fullh"><span class="ar">▸</span>完整分析</div><div class="full-b">' + mdRender(displayAnswer) + '</div></div>';
    p.body.innerHTML = html;
    const fh = p.body.querySelector('#fullh'); if (fh) fh.onclick = () => p.body.querySelector('.full').classList.toggle('open');
    p.body.scrollTop = 0;
  }

  function getCookie(name) { const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)'); return m ? m.pop() : ''; }
  function genSessionId() { let s = ''; for (let i = 0; i < 32; i++) s += Math.floor(Math.random() * 16).toString(16); return s; }

  function gmReq(method, path, payload) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method, url: SERVER_URL + path, headers: { 'Content-Type': 'application/json' },
        data: payload ? JSON.stringify(payload) : undefined, timeout: 20000,
        onload: (r) => { try { const j = JSON.parse(r.responseText); (r.status >= 200 && r.status < 300) ? resolve(j) : reject(new Error('HTTP ' + r.status + ': ' + (j.detail || r.responseText))); } catch (e) { reject(new Error('HTTP ' + r.status + ' 非JSON')); } },
        onerror: () => reject(new Error('网络错误(检查 @connect)')), ontimeout: () => reject(new Error('超时')),
      });
    });
  }
  const gmPost = (path, payload) => gmReq('POST', path, payload);
  const gmGet = (path) => gmReq('GET', path, null);

  async function readAimeSSE(resp, onUpdate) {
    if (!resp.ok) throw new Error('问财 HTTP ' + resp.status);
    const reader = resp.body.getReader(); const dec = new TextDecoder('utf-8');
    let buf = '', answer = '', reasoning = '', traceId = '', agentMode = '', phase = 'connecting'; const sources = [];
    while (true) {
      const { done, value } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true }); let nl;
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
        if (!line.startsWith('data:')) continue;
        let f; try { f = JSON.parse(line.slice(5).trim()); } catch (e) { continue; }
        const bi = f.base_info; if (bi) { traceId = bi.trace_id || traceId; agentMode = bi.agent_mode || agentMode; }
        const ap = f.answer_path, sec = f.section;
        if (ap === 'progress/searching_for' && sec && sec.status === 'reasoning_summary_text.delta') { if (phase !== 'answering') phase = 'thinking'; reasoning += (sec.info_texts || []).join(''); }
        else if (ap === 'progress/searching_for' && phase !== 'answering') { phase = 'thinking'; }
        if (ap === 'other/openAnswer' && sec) { phase = 'answering'; answer += sec.rich_text != null ? sec.rich_text : (sec.text_answer || ''); }
        if (ap === 'extraInfo/sourceLink' && f.extra && f.extra.link_ids) { for (const s of f.extra.link_ids) if (s && sources.indexOf(s) < 0) sources.push(s); }
        if (onUpdate) onUpdate(phase, { answer, reasoning, sources });
      }
    }
    return { answer, reasoning, sources, traceId, agentMode };
  }
  const V_HEADERS = () => ({ 'Content-Type': 'application/json', 'accept': 'text/event-stream', 'X-Source': 'Ths_iwencai_Xuangu', 'hexin-v': getCookie('v') });

  function onUpdate(phase, s) {
    if (s.reasoning) setReasoning(s.reasoning);
    if (phase === 'answering' && s.answer) { setStage('接收中 · ' + s.answer.length + '字', 'blue', true); setBodyMd(s.answer, true); }
    else if (phase === 'thinking' && !s.answer) { setStage('思考中', 'amber', true); setBodyText('问财正在分析你的需求…（智能调度深度推理，约 10~20 秒；思考过程可点上方展开）'); }
  }

  async function runAsk(question) {
    setQuestion(question); setStage('准备中'); setReasoning(''); setBodyText('…'); panel().ft.style.display = 'none';
    if (!getCookie('v')) { setStage('未登录', 'red'); setBodyText('没取到 v cookie，请确认已登录 iwencai。'); return; }
    const userId = getCookie('userid') || ''; const sessionId = genSessionId();

    setStage('建会话', '', true); setBodyText('正在建立会话…');
    try { await fetch('/gateway/aime/robotdata/user_session/add', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: question, session_id: sessionId }) }); } catch (e) {}

    setStage('提问中', '', true); setBodyText('正在把问题提交给问财…');
    const events = [{ event_name: 'auto_agent', event_type: 'user_input' }];
    if (DEEP_RESEARCH) events.push({ event_name: 'ab_test', event_type: 'front_trigger', content: { deep_research: 1 } });
    const body = { version: '3.4.1', session_id: sessionId, user_id: userId, source: 'Ths_iwencai_Xuangu', input_type: 'typewrite', question: question + FORMAT_SUFFIX, deviceType: 'browser', add_info: { merge_repeat: true, async_generate_data: true, show_searching: true, urp: { is_lowcode: 1, component_version: '1.1.4' } }, entity_info: {}, events: events };
    let res;
    try { const resp = await fetch('/gateway/aime/stream-query', { method: 'POST', credentials: 'include', headers: V_HEADERS(), body: JSON.stringify(body) }); res = await readAimeSSE(resp, onUpdate); }
    catch (e) { setStage('失败', 'red'); setBodyText('问财 stream-query 失败：' + e.message); return; }

    if (res.answer.length < 50 && res.traceId) {
      setStage('补拉', '', true);
      try { const resp2 = await fetch('/gateway/aime/stream-query2/?historyTraceId=' + encodeURIComponent(res.traceId), { method: 'GET', credentials: 'include', headers: V_HEADERS() }); const r2 = await readAimeSSE(resp2, onUpdate); if (r2.answer.length > res.answer.length) res = r2; } catch (e) {}
    }
    if (!res.answer.trim()) { setStage('无结果', 'red'); setBodyText('没抓到答案话术（可能被风控，或问题被判为非推荐意图）。'); return; }

    const rawAnswer = res.answer;
    const displayAnswer = stripMarkerLine(stripEmbeds(rawAnswer));
    setStage('上报中', 'blue', true);
    const conc = extractConclusion(rawAnswer, []);
    let r = {};
    try {
      r = await gmPost('/api/wencai/opinion', { question, answer_text: displayAnswer, reasoning: res.reasoning || '', conclusion: conc, trace_id: res.traceId, agent_mode: res.agentMode || (DEEP_RESEARCH ? 'deep_research' : 'normal'), uploader: getCookie('userid') || '' });
      setStage('✓ 已存档', 'green');
    } catch (e) { setStage('上报失败(仍可看)', 'amber'); r = {}; }

    const items = r.stock_items || (r.stocks || []).map((n) => ({ name: n }));
    const state = { question, displayAnswer, rawAnswer, items, sources: res.sources };
    renderResult(state, null);        // 先无现价出卡
    const stocks = r.stocks || items.map((x) => x.name);
    const chips = stocks.length ? stocks.map((s, i) => '<span class="chip' + (i === 0 ? ' hot' : '') + '">' + esc(s) + (i === 0 ? ' ·主推' : '') + '</span>').join('') : '<span style="color:#9ca3af">未识别出具体个股</span>';
    const src = (res.sources && res.sources.length) ? '<div class="src">来源维度：' + res.sources.slice(0, 6).map(esc).join(' · ') + '</div>' : '';
    setFoot('<span class="lbl">识别个股：</span>' + chips + src + '<br><a class="btn" href="' + SERVER_URL + '/wencai-opinion" target="_blank">去「问财观点」页查看 →</a>');

    const code = items[0] && items[0].code;
    if (code) gmGet('/api/wencai/quote?code=' + code).then((q) => { if (q && q.price) renderResult(state, +q.price); }).catch(() => {});
  }

  GM_registerMenuCommand('① 问一句 → 上报观点', () => { const q = prompt('输入要问问财的口语问题:', DEFAULT_Q); if (q && q.trim()) runAsk(q.trim()); });
  GM_registerMenuCommand('② 用预置问题直接上报', () => runAsk(DEFAULT_Q));

  console.log('%c[问财观点] 已加载 v1.6 (研判速览决策卡+表格+距现价%). 菜单手动问一句并上报。deep_research=' + DEEP_RESEARCH, 'color:#3d4ee0;font-weight:bold');
})();
