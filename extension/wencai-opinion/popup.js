// 问财观点扩展 · 弹窗逻辑（三 Tab：问答 / 历史 / 设置，设置改完即存）
const DEFAULTS = {
  serverUrl: 'http://124.71.75.5', token: '', uploader: '',
  presets: [
    '给我推荐一只股票,目前处于买入区间,持股一周以内,盈利7%以上',
    '当前有哪些板块在起, 适合低吸跟随?',
    '今日大盘走势与操作建议',
  ],
  deepResearch: false, autoUpload: true, onlyWithStock: false,
  schedule: { enabled: false, times: ['09:35', '13:05'], questions: [] },
};
const $ = (id) => document.getElementById(id);
const linesToArr = (s) => (s || '').split('\n').map((x) => x.trim()).filter(Boolean);
const RUN_STALE_MS = 5 * 60 * 1000;    // 超过5分钟的"进行中"视为僵死
const RESULT_FRESH_MS = 15 * 60 * 1000; // 15分钟内的结果重开弹窗还展示

// ---------- Tab 切换 ----------
document.querySelectorAll('.tab').forEach((t) => {
  t.onclick = () => {
    document.querySelectorAll('.tab').forEach((x) => x.classList.toggle('active', x === t));
    document.querySelectorAll('.panel').forEach((p) => p.classList.toggle('active', p.id === t.dataset.panel));
  };
});

// ---------- Toast ----------
let toastTimer = null;
function toast(msg) {
  const el = $('toast'); el.textContent = msg; el.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove('show'), 1400);
}

// ---------- 定时设置：时间点 / 问题（勾选式，杜绝手敲格式问题） ----------
// 快捷时间点：值一律零填充 HH:MM
const QUICK_TIMES = [['09:35', '开盘后'], ['11:25', '午盘'], ['13:05', '午后'], ['14:40', '尾盘']];
// 界面上的定时状态（真源，collectSettings 从这里取，不再从文本框解析）
let schedPickedTimes = [];
let schedPickedQs = [];

// 归一化成零填充 "HH:MM"。旧版本允许存 "9:35"，而后台是拿它跟 "09:35" 做字符串精确比对，
// 结果永远不触发也不报错；存量值读进来先过这里，显示和回存都是补零后的。
function normTime(t) {
  const m = /^(\d{1,2})[:：](\d{1,2})$/.exec(String(t == null ? '' : t).trim());
  if (!m) return '';
  const h = +m[1], mi = +m[2];
  if (h > 23 || mi > 59) return '';
  return String(h).padStart(2, '0') + ':' + String(mi).padStart(2, '0');
}
// 去重 + 按时间排序（字符串排序对零填充 HH:MM 就是时间序）
const cleanTimes = (arr) => Array.from(new Set((arr || []).map(normTime).filter(Boolean))).sort();

function renderSchedTimes() {
  // 快捷勾选
  const quick = $('schedQuick'); quick.innerHTML = '';
  QUICK_TIMES.forEach(([t, label]) => {
    const on = schedPickedTimes.indexOf(t) >= 0;
    const lb = document.createElement('label'); lb.className = on ? 'on' : '';
    const cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = on;
    cb.onchange = () => {
      schedPickedTimes = cb.checked ? cleanTimes(schedPickedTimes.concat([t])) : schedPickedTimes.filter((x) => x !== t);
      renderSchedTimes(); saveSettings();
    };
    const tx = document.createElement('span'); tx.textContent = t;
    const lz = document.createElement('span'); lz.className = 'q-lbl'; lz.textContent = label;
    lb.appendChild(cb); lb.appendChild(tx); lb.appendChild(lz);
    quick.appendChild(lb);
  });

  // 已选标签（含自定义时间点），可逐个删除
  const tags = $('schedTimeTags'); tags.innerHTML = '';
  if (!schedPickedTimes.length) {
    const d = document.createElement('div'); d.className = 'empty';
    d.textContent = '还没选时间点，定时不会执行';
    tags.appendChild(d);
    return;
  }
  schedPickedTimes.forEach((t) => {
    const tag = document.createElement('span'); tag.className = 'ttag';
    const tx = document.createElement('span'); tx.textContent = t;
    const del = document.createElement('button'); del.type = 'button'; del.textContent = '×'; del.title = '删除';
    del.onclick = () => { schedPickedTimes = schedPickedTimes.filter((x) => x !== t); renderSchedTimes(); saveSettings(); };
    tag.appendChild(tx); tag.appendChild(del);
    tags.appendChild(tag);
  });
}

function addCustomTime() {
  const v = normTime($('schedCustomTime').value);   // input[type=time] 原生就是 HH:MM
  if (!v) { toast('请先选一个时间'); return; }
  if (schedPickedTimes.indexOf(v) >= 0) { toast('已经有 ' + v + ' 了'); return; }
  schedPickedTimes = cleanTimes(schedPickedTimes.concat([v]));
  $('schedCustomTime').value = '';
  renderSchedTimes(); saveSettings();
}

// 定时问题：从当前预置问题里勾选，存的是问题原文（后台按原文跑）
function renderSchedQuestions(presets) {
  const ps = presets || [];
  const box = $('schedQList'); box.innerHTML = '';
  if (!ps.length) {
    const d = document.createElement('div'); d.className = 'empty';
    d.textContent = '还没有预置问题，先在上面加几条';
    box.appendChild(d);
  }
  ps.forEach((q) => {
    const on = schedPickedQs.indexOf(q) >= 0;
    const lb = document.createElement('label'); lb.className = on ? 'on' : '';
    const cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = on;
    cb.onchange = () => {
      schedPickedQs = cb.checked ? schedPickedQs.concat([q]) : schedPickedQs.filter((x) => x !== q);
      renderSchedQuestions(ps); saveSettings();
    };
    const tx = document.createElement('span'); tx.textContent = q;
    lb.appendChild(cb); lb.appendChild(tx);
    box.appendChild(lb);
  });

  // 用户改过预置问题后，已选里可能残留对不上的旧原文 —— 明确列出来而不是悄悄丢掉
  schedPickedQs.filter((q) => ps.indexOf(q) < 0).forEach((q) => {
    const row = document.createElement('div'); row.className = 'orphan';
    const tx = document.createElement('span'); tx.className = 'o-txt'; tx.textContent = q;
    const tip = document.createElement('small'); tip.className = 'o-tip'; tip.textContent = '预置里已没有这条';
    tx.appendChild(tip);
    const del = document.createElement('button'); del.type = 'button'; del.textContent = '×'; del.title = '删除';
    del.onclick = () => { schedPickedQs = schedPickedQs.filter((x) => x !== q); renderSchedQuestions(ps); saveSettings(); };
    row.appendChild(tx); row.appendChild(del);
    box.appendChild(row);
  });

  // 「一条都没勾 = 跑第一条预置」写在界面上，不再只藏在 placeholder 里
  const first = ps[0] || '';
  $('schedQHint').textContent = schedPickedQs.length
    ? ('每个时间点依次跑上面勾选的 ' + schedPickedQs.length + ' 条')
    : (first ? ('一条都没勾 = 只跑第一条预置问题：' + first) : '一条都没勾 = 跑第一条预置问题（当前一条预置都没有，不会跑）');
}

// 上次运行（后台写 storage.local.schedLastRun，这里只读）
function renderLastRun(r) {
  const el = $('schedLastRun');
  if (!el) return;
  el.classList.remove('bad');
  if (!r || !r.at) { el.textContent = '上次运行：还没跑过'; return; }
  const d = new Date(r.at);                       // 毫秒时间戳 → 本地时间
  const d0 = new Date(r.at); d0.setHours(0, 0, 0, 0);
  const t0 = new Date(); t0.setHours(0, 0, 0, 0);
  const dayDiff = Math.round((t0 - d0) / 86400000);
  const dayTxt = dayDiff === 0 ? '今天' : dayDiff === 1 ? '昨天' : (d.getMonth() + 1) + '/' + d.getDate();
  const hhmm = normTime(r.time) || (String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'));
  let tail;
  if (r.error) { tail = '失败（' + r.error + '）'; el.classList.add('bad'); }
  else if (r.total) tail = (r.ok || 0) + '/' + r.total + ' 成功';
  else tail = '没有可跑的问题';
  el.textContent = '上次运行：' + dayTxt + ' ' + hhmm + ' · ' + tail;
}
function loadLastRun() {
  chrome.storage.local.get({ schedLastRun: null }, (o) => renderLastRun(o.schedLastRun));
}

// ---------- 设置：加载 + 改完即存 ----------
let saving = false; // 防止 load 回填触发保存
function loadSettings() {
  chrome.storage.sync.get(DEFAULTS, (s) => {
    saving = true;
    $('uploader').value = s.uploader || '';
    $('presets').value = (s.presets || []).join('\n');
    $('deepResearch').checked = !!s.deepResearch; $('autoUpload').checked = !!s.autoUpload; $('onlyWithStock').checked = !!s.onlyWithStock;
    const sc = s.schedule || DEFAULTS.schedule;
    $('schedEnabled').checked = !!sc.enabled;
    schedPickedTimes = cleanTimes(sc.times);                                  // 存量 "9:35" 在这里补零
    schedPickedQs = (sc.questions || []).map((x) => String(x).trim()).filter(Boolean);
    $('schedBody').classList.toggle('off', !sc.enabled);
    renderSchedTimes();
    renderSchedQuestions(s.presets || []);
    renderPresets(s.presets || []);
    saving = false;
  });
}

function collectSettings() {
  const presets = linesToArr($('presets').value);
  return {
    serverUrl: DEFAULTS.serverUrl, uploader: $('uploader').value.trim(),
    presets,
    deepResearch: $('deepResearch').checked, autoUpload: $('autoUpload').checked, onlyWithStock: $('onlyWithStock').checked,
    schedule: {
      enabled: $('schedEnabled').checked,
      times: cleanTimes(schedPickedTimes),     // 回存前再归一化一次，落库一定是零填充 HH:MM
      questions: schedPickedQs.slice(),
    },
  };
}

function saveSettings() {
  if (saving) return;
  const data = collectSettings();
  chrome.storage.sync.set(data, () => {
    toast('已保存 ✓');
    renderPresets(data.presets);
    renderSchedQuestions(data.presets);   // 预置改了 → 定时勾选列表同步刷新
  });
}

let saveTimer = null;
const saveDebounced = () => { clearTimeout(saveTimer); saveTimer = setTimeout(saveSettings, 700); };
// 开关：立即存；文本：停顿0.7秒或失焦即存
['deepResearch', 'autoUpload', 'onlyWithStock', 'schedEnabled'].forEach((id) => {
  $(id).addEventListener('change', () => {
    if (id === 'schedEnabled') $('schedBody').classList.toggle('off', !$('schedEnabled').checked);
    saveSettings();
  });
});
['uploader', 'presets'].forEach((id) => {
  $(id).addEventListener('input', saveDebounced);
  $(id).addEventListener('change', () => { clearTimeout(saveTimer); saveSettings(); });
});
$('schedAddTime').onclick = addCustomTime;
// 在 time 输入框里回车也能添加，省一次点击
$('schedCustomTime').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addCustomTime(); } });

// 别的页面（问财页浮层等）改了设置，同步回填深研开关
chrome.storage.onChanged.addListener((ch, area) => {
  if (area === 'sync' && ch.deepResearch) { saving = true; $('deepResearch').checked = !!ch.deepResearch.newValue; saving = false; }
  if (area === 'local' && ch.runState) renderRunState(ch.runState.newValue);
  if (area === 'local' && ch.history) { loadHistory(); refreshFirstRunTip(); }
  if (area === 'local' && ch.updateInfo) renderUpdateBar(ch.updateInfo.newValue);
  if (area === 'local' && ch.schedLastRun) renderLastRun(ch.schedLastRun.newValue);   // 后台跑完即刷新"上次运行"
});

// 后台监听到问财登录 cookie 变化 → 自动刷新额度/登录态(弹窗切走再回来也能生效)
chrome.runtime.onMessage.addListener((msg) => {
  if (msg && msg.type === 'loginRefreshed') loadQuota();
});

// ---------- 问答页 ----------
function renderPresets(presets) {
  const box = $('askBtns'); box.innerHTML = '';
  if (!presets || !presets.length) { const d = document.createElement('div'); d.className = 'empty'; d.textContent = '还没有预置问题，去「设置」里加几条'; box.appendChild(d); return; }
  presets.forEach((q) => {
    const b = document.createElement('button'); b.textContent = q; b.title = '点击提问';
    b.onclick = () => ask(q);
    box.appendChild(b);
  });
}

function ask(question) {
  chrome.storage.local.get({ runState: null }, (o) => {
    const st = o.runState;
    if (st && st.status === 'running' && Date.now() - st.startedAt < RUN_STALE_MS) { toast('已有一个提问在跑，稍等'); return; }
    chrome.runtime.sendMessage({ type: 'runBg', question }, () => {
      // 结果统一走 storage runState 渲染；这里只兜底通信错误
      if (chrome.runtime.lastError) toast('提交失败：' + chrome.runtime.lastError.message);
      loadQuota();
    });
  });
}
$('askGo').onclick = () => { const q = $('askInput').value.trim(); if (q) { ask(q); $('askInput').value = ''; } };
$('askInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); const q = $('askInput').value.trim(); if (q) { ask(q); $('askInput').value = ''; } }
});

// ---------- 运行状态（后台写 storage，弹窗随开随恢复） ----------
let elapsedTimer = null;
function renderRunState(st) {
  const run = $('runCard'), res = $('resultCard');
  clearInterval(elapsedTimer);
  if (!st) { run.classList.add('hide'); res.classList.add('hide'); return; }

  if (st.status === 'running') {
    if (Date.now() - st.startedAt > RUN_STALE_MS) { chrome.storage.local.remove('runState'); return; }
    res.classList.add('hide');
    $('runQ').textContent = st.q || '';
    const tick = () => { $('runElapsed').textContent = '已 ' + Math.max(0, Math.round((Date.now() - st.startedAt) / 1000)) + 's'; };
    tick(); elapsedTimer = setInterval(tick, 1000);
    run.classList.remove('hide');
    run.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    return;
  }

  run.classList.add('hide');
  if (Date.now() - (st.finishedAt || 0) > RESULT_FRESH_MS) { chrome.storage.local.remove('runState'); return; }
  renderResultCard(st);
  if (st.status === 'done') { loadQuota(); }
}

function kvRow(k, v) {
  const row = document.createElement('div'); row.className = 'kv';
  const ke = document.createElement('span'); ke.className = 'k'; ke.textContent = k;
  const ve = document.createElement('span'); ve.className = 'v'; ve.textContent = v;
  row.appendChild(ke); row.appendChild(ve); return row;
}

function fetchQuoteDirect(code) {
  return fetch(DEFAULTS.serverUrl + '/api/wencai/quote?code=' + encodeURIComponent(code), { cache: 'no-store' })
    .then((r) => (r.ok ? r.json() : null)).catch(() => null);
}
// 弹窗决策卡(与详情页/浮层同一套): 主推 + 买入/止盈/止损价位磁贴(带距现价%) + 逻辑/风险
function fillPopupDc(el, c, items, cur) {
  const W = window.WOP; if (!W) { el.innerHTML = ''; return; }
  const esc = W.esc;
  const fmtN = (v) => Number.isInteger(v) ? String(v) : String(+v.toFixed(2));
  const signPct = (p) => (p >= 0 ? '+' : '−') + Math.abs(p).toFixed(1) + '%';
  const tile = (cls, cn, en, val) => {
    const v = W.cleanConcVal(val || '', cn); const pr = v ? W.extractPrice(v) : null;
    const k = '<div class="pt-k">' + cn + '<i>' + en + '</i></div>';
    if (!pr) return '<div class="ptile ' + cls + '">' + k + '<div class="pt-only">' + esc(v || '—') + '</div></div>';
    const num = pr.lo === pr.hi ? fmtN(pr.lo) : fmtN(pr.lo) + '–' + fmtN(pr.hi);
    let d = '', bar = '';
    if (cur) {
      const loP = (pr.lo - cur) / cur * 100, hiP = (pr.hi - cur) / cur * 100, mid = (loP + hiP) / 2;
      const dir = mid >= 0 ? 'up' : 'down', arr = mid >= 0 ? '↑' : '↓';
      d = '<span class="pt-d ' + dir + '">' + arr + ' ' + (pr.lo === pr.hi ? signPct(loP) : signPct(loP) + '~' + signPct(hiP)) + '</span>';
      bar = '<div class="pt-bar"><i style="width:' + Math.max(6, Math.min(100, Math.abs(mid) / 10 * 100)).toFixed(0) + '%"></i></div>';
    }
    return '<div class="ptile ' + cls + '">' + k + '<div class="pt-num">' + num + d + '</div>' + bar + '<div class="pt-cap">' + esc(v) + '</div></div>';
  };
  const top = items[0] || {};
  const name = top.name || W.cleanConcVal(c.stock || '', '标的').replace(/\s*\(.*$/, '');
  const code = top.code || '';
  const logic = W.cleanConcVal(c.logic || '', '逻辑'), risk = W.cleanConcVal(c.risk || '', '风险');
  let h = '';
  if (name || code) h += '<div class="pdc-name">' + esc(name || '—') + (code ? '<span class="pdc-code">' + esc(code) + '</span>' : '') + (cur ? '<span class="pdc-cur">现价 ' + fmtN(cur) + '</span>' : '') + '</div>';
  h += '<div class="ptiles">' + tile('buy', '买入', 'BUY', c.buy) + tile('tp', '止盈', 'TP', c.takeProfit) + tile('sl', '止损', 'SL', c.stopLoss) + '</div>';
  let th = '';
  if (logic) th += '<div class="pth"><b>逻辑</b>' + esc(logic) + '</div>';
  if (risk) th += '<div class="pth risk"><b>风险</b>' + esc(risk) + '</div>';
  if (th) h += '<div class="pthesis">' + th + '</div>';
  el.innerHTML = h;
}

function renderResultCard(st) {
  const res = $('resultCard'); res.innerHTML = ''; res.classList.remove('err');

  const head = document.createElement('div'); head.className = 'res-head';
  const body = document.createElement('div'); body.className = 'res-body';
  const q = document.createElement('div'); q.className = 'res-q'; q.textContent = st.q || '';
  body.appendChild(q);

  if (st.status === 'error') {
    res.classList.add('err'); head.classList.add('bad'); head.textContent = '✕ 提问失败';
    const msg = document.createElement('div'); msg.className = 'res-err-msg'; msg.textContent = st.error || '未知错误';
    body.appendChild(msg);
    const foot = document.createElement('div'); foot.className = 'res-foot';
    const retry = document.createElement('button'); retry.className = 'btn primary'; retry.textContent = '重试';
    retry.onclick = () => ask(st.q);
    foot.appendChild(retry); body.appendChild(foot);
  } else {
    head.classList.add(st.skipped ? 'skip' : 'ok');
    head.textContent = st.skipped ? '⚠ 没抽出个股，按设置没存' : '✓ 已存档';
    const items = st.stockItems || [];
    const dc = document.createElement('div'); dc.className = 'pdc'; body.appendChild(dc);
    fillPopupDc(dc, st.conclusion || {}, items, null);
    const dcode = items[0] && items[0].code;
    if (dcode) fetchQuoteDirect(dcode).then((q) => { if (q && q.price && dc.isConnected) fillPopupDc(dc, st.conclusion || {}, items, +q.price); });
    const foot = document.createElement('div'); foot.className = 'res-foot';
    const full = document.createElement('button'); full.className = 'btn ghost'; full.textContent = '看全文';
    full.onclick = () => chrome.tabs.create({ url: chrome.runtime.getURL('viewer.html?i=0') });
    foot.appendChild(full);
    if (st.answerLen) { const m = document.createElement('span'); m.className = 'res-meta'; m.textContent = st.answerLen + ' 字' + (st.deep ? ' · 深研' : ''); foot.appendChild(m); }
    body.appendChild(foot);
  }

  const close = document.createElement('span'); close.className = 'res-close'; close.textContent = '×'; close.title = '收起';
  close.onclick = () => { chrome.storage.local.remove('runState'); res.classList.add('hide'); };
  head.appendChild(close);

  res.appendChild(head); res.appendChild(body); res.classList.remove('hide');
  res.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// ---------- 历史页 ----------
function relTime(ts) {
  if (!ts) return '';
  const d = Date.now() - ts;
  if (d < 60e3) return '刚刚';
  if (d < 3600e3) return Math.floor(d / 60e3) + ' 分钟前';
  if (d < 86400e3) return Math.floor(d / 3600e3) + ' 小时前';
  const t = new Date(ts);
  return (t.getMonth() + 1) + '/' + t.getDate() + ' ' + String(t.getHours()).padStart(2, '0') + ':' + String(t.getMinutes()).padStart(2, '0');
}

function loadHistory() {
  chrome.storage.local.get({ history: [] }, (o) => {
    const box = $('history'); const h = o.history || [];
    box.innerHTML = '';
    if (!h.length) {
      const d = document.createElement('div'); d.className = 'hist-empty';
      d.innerHTML = '<div class="big">🗒️</div>还没有问答记录<br>去「问答」页问一句试试';
      box.appendChild(d); return;
    }
    h.forEach((it, idx) => {
      const row = document.createElement('div'); row.className = 'hrow'; row.title = '点击查看全文';
      const q = document.createElement('div'); q.className = 'hq'; q.textContent = it.q || '';
      const meta = document.createElement('div'); meta.className = 'hmeta';
      (it.stocks || []).slice(0, 3).forEach((s, i) => {
        const c = document.createElement('span'); c.className = 'chip' + (i === 0 ? ' hot' : '');
        c.textContent = typeof s === 'string' ? s : (s.name || '');
        meta.appendChild(c);
      });
      if (!(it.stocks || []).length) { const c = document.createElement('span'); c.className = 'chip'; c.textContent = '无个股'; meta.appendChild(c); }
      if (it.deep) { const b = document.createElement('span'); b.className = 'deep-badge'; b.textContent = '深研'; meta.appendChild(b); }
      const t = document.createElement('span'); t.className = 'htime'; t.textContent = relTime(it.ts);
      meta.appendChild(t);
      row.appendChild(q); row.appendChild(meta);
      row.onclick = () => chrome.tabs.create({ url: chrome.runtime.getURL('viewer.html?i=' + idx) });
      box.appendChild(row);
    });
  });
}

// ---------- 登录 ----------
function setLoginUI(state) {
  // state: 'ok' | 'out' | 'unknown'
  $('loginTip').classList.toggle('hide', state !== 'out');
  const st = $('loginStatus');
  if (st) {
    st.textContent = state === 'ok' ? '已登录 ✓' : state === 'out' ? '未登录' : '检测中…';
    st.className = 'login-status ' + (state === 'ok' ? 'ok' : state === 'out' ? 'out' : '');
  }
  const btn = $('reLogin');
  if (btn) {
    // 已登录时按钮弱化成不起眼的「切换账号」小链接, 避免误以为要重新登录; 未登录才给显眼按钮
    if (state === 'ok') { btn.textContent = '切换账号'; btn.className = 'relogin-link'; }
    else if (state === 'out') { btn.textContent = '去登录'; btn.className = 'btn primary'; }
    else { btn.textContent = '登录'; btn.className = 'relogin-link'; }
  }
}

let loginPollTimer = null;
function openWencaiLogin() {
  chrome.tabs.create({ url: 'https://www.iwencai.com/' });
  toast('登录后会自动刷新');
  // 轮询等待登录成功(弹窗没被关掉时生效; 关掉后靠后台 cookie 监听兜底)
  let tries = 0;
  clearInterval(loginPollTimer);
  loginPollTimer = setInterval(() => {
    tries++;
    chrome.runtime.sendMessage({ type: 'quota' }, (q) => {
      if (chrome.runtime.lastError) return;
      if (q && q.ok) { clearInterval(loginPollTimer); renderQuota(q); toast('已登录 ✓'); }
      else if (tries >= 40) clearInterval(loginPollTimer);   // 最多轮询约2分钟
    });
  }, 3000);
}
$('ltGo').onclick = openWencaiLogin;
$('reLogin').onclick = openWencaiLogin;

// ---------- 额度 ----------
function renderQuota(q) {
  const el = $('quota'); el.innerHTML = '';
  const pill = (txt, cls, onClick) => {
    const p = document.createElement('span'); p.className = 'qpill ' + (cls || ''); p.innerHTML = txt;
    if (onClick) { p.classList.add('clickable'); p.onclick = onClick; }
    el.appendChild(p);
  };
  if (!q || !q.ok) {
    if (q && q.error === '未登录') { pill('⚠️ 未登录问财，点此去登录 →', 'warn', openWencaiLogin); setLoginUI('out'); }
    else { pill('额度未知', 'warn'); setLoginUI('unknown'); }
    return;
  }
  setLoginUI('ok');
  if (q.normal != null) pill('普通剩 <b>' + q.normal + '</b>');
  if (q.deepInfer != null) pill('深研剩 <b>' + q.deepInfer + '</b>', 'deep');
  if (q.leftTime) pill(q.leftTime + ' 后刷新额度', 'ghost');
}
// 打开弹窗即拉一次额度 = 顺手 touch 问财 user-info, 给登录态保温(软保活)。
function loadQuota() {
  chrome.runtime.sendMessage({ type: 'quota' }, (q) => {
    renderQuota(chrome.runtime.lastError ? null : q);
  });
}

// ---------- 版本 / 更新 ----------
function renderUpdateBar(info) {
  const bar = $('updateBar');
  if (info && info.latest && info.url) {
    $('ubVer').textContent = 'v' + info.latest;
    bar.href = info.url;
    bar.classList.remove('hide');
  } else {
    bar.classList.add('hide');
  }
}
function initUpdateUI() {
  $('verNow').textContent = 'v' + chrome.runtime.getManifest().version;
  chrome.storage.local.get({ updateInfo: null }, (o) => renderUpdateBar(o.updateInfo));
  // 打开弹窗即视为已看到红点，清掉角标
  try { chrome.action.setBadgeText({ text: '' }); } catch (e) {}
}
$('checkUpdate').onclick = () => {
  const btn = $('checkUpdate'); btn.disabled = true; btn.textContent = '检查中…';
  chrome.runtime.sendMessage({ type: 'checkUpdate' }, (r) => {
    btn.disabled = false; btn.textContent = '检查更新';
    if (chrome.runtime.lastError || !r || !r.ok) { toast('检查失败，稍后再试'); return; }
    if (r.hasNew) { renderUpdateBar({ latest: r.latest, url: r.url }); toast('发现新版 v' + r.latest); }
    else { renderUpdateBar(null); toast('已是最新 v' + r.current); }
  });
};

// 打开完整设置页
$('openOptions').onclick = () => {
  if (chrome.runtime.openOptionsPage) chrome.runtime.openOptionsPage();
  else chrome.tabs.create({ url: chrome.runtime.getURL('options.html') });
};

// ---------- 上手引导 ----------
const openWelcome = () => chrome.tabs.create({ url: chrome.runtime.getURL('welcome.html') });
$('openWelcome').onclick = openWelcome;
$('frGo').onclick = openWelcome;

// 首次提示只在「一次都没问过」时出现; history 与引导页第三步同一个判据, 问过一次两边一起消失。
function refreshFirstRunTip() {
  chrome.storage.local.get({ history: [] }, (o) => {
    $('firstRunTip').classList.toggle('hide', (o.history || []).length > 0);
  });
}

// ---------- 启动 ----------
loadSettings(); loadHistory(); loadQuota(); initUpdateUI(); refreshFirstRunTip(); loadLastRun();
chrome.storage.local.get({ runState: null }, (o) => renderRunState(o.runState));
