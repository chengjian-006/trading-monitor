// 问财观点扩展 · 设置弹窗逻辑
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

function load() {
  chrome.storage.sync.get(DEFAULTS, (s) => {
    $('serverUrl').value = s.serverUrl; $('token').value = s.token; $('uploader').value = s.uploader || '';
    $('presets').value = (s.presets || []).join('\n');
    $('deepResearch').checked = !!s.deepResearch; $('autoUpload').checked = !!s.autoUpload; $('onlyWithStock').checked = !!s.onlyWithStock;
    const sc = s.schedule || DEFAULTS.schedule;
    $('schedEnabled').checked = !!sc.enabled; $('schedTimes').value = (sc.times || []).join(','); $('schedQuestions').value = (sc.questions || []).join('\n');
    renderAskButtons(s.presets || []);
  });
}

function save() {
  const data = {
    serverUrl: $('serverUrl').value.trim() || DEFAULTS.serverUrl, token: $('token').value.trim(), uploader: $('uploader').value.trim(),
    presets: linesToArr($('presets').value), deepResearch: $('deepResearch').checked, autoUpload: $('autoUpload').checked, onlyWithStock: $('onlyWithStock').checked,
    schedule: { enabled: $('schedEnabled').checked, times: ($('schedTimes').value || '').split(',').map((x) => x.trim()).filter((x) => /^\d{1,2}:\d{2}$/.test(x)), questions: linesToArr($('schedQuestions').value) },
  };
  chrome.storage.sync.set(data, () => { $('saveMsg').textContent = '已保存 ✓'; renderAskButtons(data.presets); setTimeout(() => { $('saveMsg').textContent = ''; }, 1800); });
}

function renderAskButtons(presets) {
  const box = $('askBtns'); box.innerHTML = '';
  (presets || []).forEach((q) => { const b = document.createElement('button'); b.textContent = q.length > 22 ? q.slice(0, 22) + '…' : q; b.title = q; b.onclick = () => runBg(q); box.appendChild(b); });
}

function runBg(question) {
  const st = $('askStatus'); st.className = 'status run'; st.textContent = '后台提交中…（约10~20秒，完成会弹通知）';
  chrome.runtime.sendMessage({ type: 'runBg', question }, (resp) => {
    if (chrome.runtime.lastError) { st.className = 'status err'; st.textContent = '失败：' + chrome.runtime.lastError.message; return; }
    if (resp && resp.ok) {
      const r = resp.result || {};
      if (r.skipped) { st.className = 'status'; st.textContent = '未抽出个股，按设置未上报。'; }
      else { st.className = 'status ok'; st.textContent = '✓ 已存档：' + ((r.stocks || []).join('、') || '未识别出个股') + '（' + (r.answerLen || 0) + '字）'; loadHistory(); loadQuota(); }
    } else { st.className = 'status err'; st.textContent = '失败：' + ((resp && resp.error) || '未知错误'); }
  });
}

function loadQuota() {
  chrome.runtime.sendMessage({ type: 'quota' }, (q) => {
    const el = $('quota');
    if (chrome.runtime.lastError || !q || !q.ok) { el.textContent = (q && q.error) === '未登录' ? '⚠️ 未登录问财（去 iwencai 登录一次）' : '额度未知'; el.className = 'quota warn'; return; }
    const parts = [];
    if (q.normal != null) parts.push('普通剩 ' + q.normal);
    if (q.deepInfer != null) parts.push('深研剩 ' + q.deepInfer);
    if (q.leftTime) parts.push('额度 ' + q.leftTime + ' 后刷新');
    el.textContent = '今日额度：' + (parts.join(' · ') || '—');
    el.className = 'quota';
  });
}

function loadHistory() {
  chrome.storage.local.get({ history: [] }, (o) => {
    const box = $('history'); const h = o.history || [];
    if (!h.length) { box.innerHTML = '<span class="muted">暂无</span>'; return; }
    box.innerHTML = '';
    h.forEach((it, idx) => {
      const row = document.createElement('div'); row.className = 'hrow';
      const stk = (it.stocks || []).map((s) => s.name).slice(0, 2).join('、');
      row.innerHTML = '<div class="hq">' + (it.q || '').replace(/[<>&]/g, '') + '</div><div class="hm">' + (stk ? '📈 ' + stk : '（无个股）') + '</div>';
      row.title = '点击重看'; row.onclick = () => chrome.tabs.create({ url: chrome.runtime.getURL('viewer.html?i=' + idx) });
      box.appendChild(row);
    });
  });
}

$('save').onclick = save;
$('tokToggle').onclick = () => { const i = $('token'); const on = i.type === 'password'; i.type = on ? 'text' : 'password'; $('tokToggle').textContent = on ? '隐藏' : '显示'; };
$('askGo').onclick = () => { const q = $('askInput').value.trim(); if (q) runBg(q); };
$('askInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') { const q = $('askInput').value.trim(); if (q) runBg(q); } });
load(); loadHistory(); loadQuota();
