// 问财观点扩展 · 完整设置页(宽屏 options)。与 popup 设置 Tab 共用同一套 storage.sync 键，改完即存。
const DEFAULTS = {
  serverUrl: 'https://app.guxiaocha.com', token: '', uploader: '',
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
    d.textContent = '还没有预置问题，先在下面「预置问题」里加几条';
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

let saving = false; // 防止 load 回填触发保存
let activeServerUrl = DEFAULTS.serverUrl; // 保留已有部署覆盖；页面没有服务器地址输入框
function loadSettings() {
  chrome.storage.sync.get(DEFAULTS, (s) => {
    saving = true;
    activeServerUrl = s.serverUrl || DEFAULTS.serverUrl;
    $('uploader').value = s.uploader || '';
    $('presets').value = (s.presets || []).join('\n');
    $('deepResearch').checked = !!s.deepResearch;
    $('autoUpload').checked = !!s.autoUpload;
    $('onlyWithStock').checked = !!s.onlyWithStock;
    const sc = s.schedule || DEFAULTS.schedule;
    $('schedEnabled').checked = !!sc.enabled;
    schedPickedTimes = cleanTimes(sc.times);                                  // 存量 "9:35" 在这里补零
    schedPickedQs = (sc.questions || []).map((x) => String(x).trim()).filter(Boolean);
    $('schedBody').classList.toggle('off', !sc.enabled);
    renderSchedTimes();
    renderSchedQuestions(s.presets || []);
    saving = false;
    promptForIngestToken(s);
  });
}

function promptForIngestToken(s) {
  if (String(s.token || '').trim()) return;
  const token = window.prompt('First-time setup: enter config.json wencai_opinion.ingest_token. This limits abuse; it is not a non-extractable browser secret.', '');
  if (!token || !token.trim()) return;
  chrome.storage.sync.set({ token: token.trim() }, () => toast('Opinion upload token saved'));
}

function collectSettings() {
  const presets = linesToArr($('presets').value);
  return {
    serverUrl: activeServerUrl, uploader: $('uploader').value.trim(),
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
    renderSchedQuestions(data.presets);   // 预置改了 → 定时勾选列表同步刷新
    const h = $('saveHint'); h.textContent = '已保存'; clearTimeout(saveSettings._t); saveSettings._t = setTimeout(() => (h.textContent = ''), 1500);
  });
}

let saveTimer = null;
const saveDebounced = () => { clearTimeout(saveTimer); saveTimer = setTimeout(saveSettings, 700); };
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

// 别处(弹窗/问财页浮层)改了设置 → 同步回填, 避免多页不一致
chrome.storage.onChanged.addListener((ch, area) => {
  if (area === 'sync' && (ch.presets || ch.deepResearch || ch.uploader || ch.autoUpload || ch.onlyWithStock || ch.schedule)) loadSettings();
  if (area === 'local' && ch.schedLastRun) renderLastRun(ch.schedLastRun.newValue);   // 后台跑完即刷新"上次运行"
});

// 版本 / 检查更新(复用 background 的 checkUpdate 消息)
$('verNow').textContent = 'v' + chrome.runtime.getManifest().version;
$('checkUpdate').onclick = () => {
  const btn = $('checkUpdate'); btn.disabled = true; btn.textContent = '检查中…';
  chrome.runtime.sendMessage({ type: 'checkUpdate' }, (r) => {
    btn.disabled = false; btn.textContent = '检查更新';
    if (chrome.runtime.lastError || !r || !r.ok) { toast('检查失败，稍后再试'); return; }
    toast(r.hasNew ? ('发现新版 v' + r.latest + '，去弹窗顶部下载') : ('已是最新 v' + r.current));
  });
};

loadSettings();
loadLastRun();
