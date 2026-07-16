// 问财观点扩展 · 后台 Service Worker
// 职责: ①跨域上报中转(避页面CSP) ②后台不开页直跑(读cookie调aime) ③定时触发 ④系统通知反馈。
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

function cookieVal(name) {
  return new Promise((res) => {
    chrome.cookies.get({ url: 'https://www.iwencai.com', name }, (c) => res(c ? c.value : ''));
  });
}

function notify(title, message) {
  try {
    chrome.notifications.create('wop-' + Date.now(), {
      type: 'basic', iconUrl: 'icons/icon48.png', title: title, message: message.slice(0, 300),
    });
  } catch (e) { /* 通知失败忽略 */ }
}

async function uploadTo(serverUrl, payload) {
  const r = await fetch(serverUrl + '/api/wencai/opinion', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  });
  const t = await r.text();
  let j = null; try { j = JSON.parse(t); } catch (e) { /* 非JSON */ }
  if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + (j && j.detail || t).toString().slice(0, 160));
  return j || {};
}

// 后台不开页直跑一次: 读cookie → aime查询 → 上报 → 通知。
async function runBg(question, opts) {
  opts = opts || {};
  const s = await getSettings();
  if (!s.token) throw new Error('未配置 token（点扩展图标在设置里填）');
  const res = await WOP.runAimeQuery(question, {
    deep: s.deepResearch,
    getV: () => cookieVal('v'), getUserId: () => cookieVal('userid'),
  });
  if (!res.answer.trim()) throw new Error('没抓到答案（可能被风控或非推荐意图）');
  const r = await uploadTo(s.serverUrl, {
    token: s.token, question, answer_text: res.answer, trace_id: res.traceId,
    agent_mode: res.agentMode || (s.deepResearch ? 'deep_research' : 'normal'),
    uploader: s.uploader || '', only_with_stock: !!s.onlyWithStock,
  });
  const stocks = (r.stocks || []).join('、');
  if (!opts.silent) {
    if (r.skipped) notify('问财观点 · 未上报', '「' + question.slice(0, 20) + '」没抽出个股，按设置未入库。');
    else notify('问财观点 · 已存档', '「' + question.slice(0, 18) + '」→ ' + (stocks || '未识别出个股') + '（' + res.answer.length + '字）');
  }
  return { ...r, answerLen: res.answer.length, stocks: r.stocks || [] };
}

// ---------- 消息路由 ----------
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg) return;
  if (msg.type === 'upload') {
    fetch(msg.url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(msg.payload) })
      .then(async (r) => { const t = await r.text(); let j = null; try { j = JSON.parse(t); } catch (e) { /* 非JSON */ } sendResponse({ ok: r.ok, status: r.status, data: j, text: t.slice(0, 200) }); })
      .catch((e) => sendResponse({ ok: false, error: String(e.message || e) }));
    return true;
  }
  if (msg.type === 'runBg') {
    runBg(msg.question, { silent: false })
      .then((r) => sendResponse({ ok: true, result: r }))
      .catch((e) => { notify('问财观点 · 失败', String(e.message || e)); sendResponse({ ok: false, error: String(e.message || e) }); });
    return true;
  }
});

// ---------- 定时触发 ----------
chrome.runtime.onInstalled.addListener(() => chrome.alarms.create('wop-tick', { periodInMinutes: 1 }));
chrome.runtime.onStartup.addListener(() => chrome.alarms.create('wop-tick', { periodInMinutes: 1 }));

function hhmm(d) { return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); }

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== 'wop-tick') return;
  const s = await getSettings();
  if (!s.schedule || !s.schedule.enabled) return;
  const now = new Date();
  if (now.getDay() === 0 || now.getDay() === 6) return;   // 周末不跑(节假日客户端难判, 从简)
  const cur = hhmm(now);
  const times = s.schedule.times || [];
  if (times.indexOf(cur) < 0) return;
  const dedupeKey = 'wop_ran_' + now.toISOString().slice(0, 10) + '_' + cur;
  const seen = await new Promise((res) => chrome.storage.local.get([dedupeKey], (o) => res(o[dedupeKey])));
  if (seen) return;
  chrome.storage.local.set({ [dedupeKey]: 1 });
  const qs = (s.schedule.questions && s.schedule.questions.length) ? s.schedule.questions : [(s.presets || [])[0]].filter(Boolean);
  for (const q of qs) {
    try { await runBg(q, { silent: false }); } catch (e) { notify('问财观点 · 定时失败', q.slice(0, 18) + '：' + (e.message || e)); }
    await new Promise((r) => setTimeout(r, 3000));   // 间隔降风控
  }
});
