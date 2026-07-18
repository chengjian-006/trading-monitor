// 问财观点扩展 · 完整设置页(宽屏 options)。与 popup 设置 Tab 共用同一套 storage.sync 键，改完即存。
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

let toastTimer = null;
function toast(msg) {
  const el = $('toast'); el.textContent = msg; el.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove('show'), 1400);
}

let saving = false; // 防止 load 回填触发保存
function loadSettings() {
  chrome.storage.sync.get(DEFAULTS, (s) => {
    saving = true;
    $('uploader').value = s.uploader || '';
    $('presets').value = (s.presets || []).join('\n');
    $('deepResearch').checked = !!s.deepResearch;
    $('autoUpload').checked = !!s.autoUpload;
    $('onlyWithStock').checked = !!s.onlyWithStock;
    const sc = s.schedule || DEFAULTS.schedule;
    $('schedEnabled').checked = !!sc.enabled;
    $('schedTimes').value = (sc.times || []).join(',');
    $('schedQuestions').value = (sc.questions || []).join('\n');
    $('schedBody').classList.toggle('off', !sc.enabled);
    saving = false;
  });
}

function collectSettings() {
  return {
    serverUrl: DEFAULTS.serverUrl, uploader: $('uploader').value.trim(),
    presets: linesToArr($('presets').value),
    deepResearch: $('deepResearch').checked, autoUpload: $('autoUpload').checked, onlyWithStock: $('onlyWithStock').checked,
    schedule: {
      enabled: $('schedEnabled').checked,
      times: ($('schedTimes').value || '').split(',').map((x) => x.trim()).filter((x) => /^\d{1,2}:\d{2}$/.test(x)),
      questions: linesToArr($('schedQuestions').value),
    },
  };
}

function saveSettings() {
  if (saving) return;
  const data = collectSettings();
  chrome.storage.sync.set(data, () => {
    toast('已保存 ✓');
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
['uploader', 'presets', 'schedTimes', 'schedQuestions'].forEach((id) => {
  $(id).addEventListener('input', saveDebounced);
  $(id).addEventListener('change', () => { clearTimeout(saveTimer); saveSettings(); });
});

// 别处(弹窗/问财页浮层)改了设置 → 同步回填, 避免多页不一致
chrome.storage.onChanged.addListener((ch, area) => {
  if (area === 'sync' && (ch.presets || ch.deepResearch || ch.uploader || ch.autoUpload || ch.onlyWithStock || ch.schedule)) loadSettings();
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
