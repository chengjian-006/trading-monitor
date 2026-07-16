// 问财观点扩展 · 后台 Service Worker
// ①跨域上报中转 ②后台不开页直跑 ③定时触发 ④额度查询 ⑤快捷键 ⑥首次装欢迎页 ⑦系统通知。
importScripts('common.js');
const WOP = self.WOP;

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
const getSettings = () => new Promise((res) => chrome.storage.sync.get(DEFAULTS, res));
const cookieVal = (name) => new Promise((res) => chrome.cookies.get({ url: 'https://www.iwencai.com', name }, (c) => res(c ? c.value : '')));
function pushHistory(rec) { chrome.storage.local.get({ history: [] }, (o) => chrome.storage.local.set({ history: [rec, ...(o.history || [])].slice(0, 15) })); }

function notify(title, message) {
  try { chrome.notifications.create('wop-' + Date.now(), { type: 'basic', iconUrl: 'icons/icon48.png', title, message: (message || '').slice(0, 300) }); } catch (e) {}
}

async function uploadTo(serverUrl, payload) {
  const r = await fetch(serverUrl + '/api/wencai/opinion', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  const t = await r.text(); let j = null; try { j = JSON.parse(t); } catch (e) {}
  if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + (j && j.detail || t).toString().slice(0, 160));
  return j || {};
}

async function runBg(question, opts) {
  opts = opts || {};
  const s = await getSettings();
  if (!s.token) throw new Error('未配置 token（点扩展图标在设置里填）');
  const res = await WOP.runAimeQuery(question, { deep: s.deepResearch, askText: question + WOP.FORMAT_SUFFIX, getV: () => cookieVal('v'), getUserId: () => cookieVal('userid') });
  if (!res.answer.trim()) throw new Error('没抓到答案（可能被风控或非推荐意图）');
  const rawAnswer = res.answer;
  res.answer = WOP.stripMarkerLine(WOP.stripEmbeds(rawAnswer));   // 去图表占位块+结论标记行
  const uid = await cookieVal('userid');
  const conc = WOP.extractConclusion(rawAnswer, []);
  const r = await uploadTo(s.serverUrl, { token: s.token, question, answer_text: res.answer, reasoning: res.reasoning || '', conclusion: conc, trace_id: res.traceId, agent_mode: res.agentMode || (s.deepResearch ? 'deep_research' : 'normal'), uploader: s.uploader || uid || '', only_with_stock: !!s.onlyWithStock });
  const items = r.stock_items || (r.stocks || []).map((n) => ({ name: n }));
  pushHistory({ q: question, answer: res.answer, stocks: items, conclusion: WOP.extractConclusion(rawAnswer, items), ts: Date.now() });
  if (!opts.silent) {
    if (r.skipped) notify('问财观点 · 未上报', '「' + question.slice(0, 20) + '」没抽出个股，按设置未入库。');
    else notify('问财观点 · 已存档', '「' + question.slice(0, 18) + '」→ ' + ((r.stocks || []).join('、') || '未识别出个股') + '（' + res.answer.length + '字）');
  }
  return { ...r, answerLen: res.answer.length, stocks: r.stocks || [] };
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg) return;
  if (msg.type === 'upload') {
    fetch(msg.url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(msg.payload) })
      .then(async (r) => { const t = await r.text(); let j = null; try { j = JSON.parse(t); } catch (e) {} sendResponse({ ok: r.ok, status: r.status, data: j, text: t.slice(0, 200) }); })
      .catch((e) => sendResponse({ ok: false, error: String(e.message || e) }));
    return true;
  }
  if (msg.type === 'runBg') {
    runBg(msg.question, { silent: false }).then((r) => sendResponse({ ok: true, result: r }))
      .catch((e) => { notify('问财观点 · 失败', String(e.message || e)); sendResponse({ ok: false, error: String(e.message || e) }); });
    return true;
  }
  if (msg.type === 'quota') {
    WOP.fetchQuota(() => cookieVal('v')).then((q) => sendResponse(q)).catch((e) => sendResponse({ ok: false, error: String(e.message || e) }));
    return true;
  }
});

// ---------- 定时触发 ----------
chrome.runtime.onInstalled.addListener((d) => {
  chrome.alarms.create('wop-tick', { periodInMinutes: 1 });
  if (d && d.reason === 'install') chrome.tabs.create({ url: chrome.runtime.getURL('welcome.html') });
});
chrome.runtime.onStartup.addListener(() => chrome.alarms.create('wop-tick', { periodInMinutes: 1 }));

function hhmm(d) { return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); }
chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== 'wop-tick') return;
  const s = await getSettings();
  if (!s.schedule || !s.schedule.enabled) return;
  const now = new Date();
  if (now.getDay() === 0 || now.getDay() === 6) return;
  const cur = hhmm(now);
  if ((s.schedule.times || []).indexOf(cur) < 0) return;
  const key = 'wop_ran_' + now.toISOString().slice(0, 10) + '_' + cur;
  const seen = await new Promise((res) => chrome.storage.local.get([key], (o) => res(o[key])));
  if (seen) return;
  chrome.storage.local.set({ [key]: 1 });
  const qs = (s.schedule.questions && s.schedule.questions.length) ? s.schedule.questions : [(s.presets || [])[0]].filter(Boolean);
  for (const q of qs) {
    try { await runBg(q, { silent: false }); } catch (e) { notify('问财观点 · 定时失败', q.slice(0, 18) + '：' + (e.message || e)); }
    await new Promise((r) => setTimeout(r, 3000));
  }
});

// ---------- 快捷键 ----------
chrome.commands.onCommand.addListener(async (cmd) => {
  if (cmd !== 'ask-preset') return;
  const s = await getSettings();
  const q = (s.presets || [])[0];
  if (!q) return;
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  const tab = tabs && tabs[0];
  if (tab && /:\/\/www\.iwencai\.com\//.test(tab.url || '')) chrome.tabs.sendMessage(tab.id, { type: 'askForeground', question: q });
  else runBg(q, { silent: false }).catch((e) => notify('问财观点 · 失败', String(e.message || e)));
});
