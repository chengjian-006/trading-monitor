// ==UserScript==
// @name         问财探测 v1.1 (任意问题/chat)
// @namespace    guxiaocha-wencai-probe
// @version      1.1
// @description  抓所有POST(含跨域)+所有WS/SSE, 流式tee, 滤静态噪音, 按 Alt+Shift+C 复制剪贴板。
// @match        https://www.iwencai.com/*
// @match        http://www.iwencai.com/*
// @match        https://*.10jqka.com.cn/*
// @match        https://*.hexin.cn/*
// @grant        GM_registerMenuCommand
// @grant        GM_setClipboard
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';

  const captured = [];
  // 滤掉明确的静态/噪音接口, 其余 POST 全抓
  const NOISE = /user-info|user_session\/scroll|scene\/v1\/item|recommend\/routine|\.(js|css|png|jpg|jpeg|gif|svg|woff|woff2|ttf|ico)(\?|$)|\/(log|report|stat|track|monitor|skywalking|beacon|favicon)/i;

  function note(tag, url) { console.log('%c[probe] ' + tag + ': ' + url, 'color:green;font-weight:bold'); }
  function noise(url) { try { return NOISE.test(url); } catch (e) { return false; } }

  // ---- fetch: 所有 POST 都抓(含跨域), 克隆响应 reader 边收边录 ----
  try {
    const origFetch = window.fetch;
    window.fetch = function (input, init) {
      let url = '', method = 'GET', body;
      try {
        url = typeof input === 'string' ? input : (input && input.url) || '';
        method = (init && init.method) || (input && input.method) || 'GET';
        body = init && init.body;
      } catch (e) {}
      const p = origFetch.apply(this, arguments);
      try {
        if (String(method).toUpperCase() === 'POST' && !noise(url)) {
          const rec = { kind: 'FETCH', url, method, body: typeof body === 'string' ? body.slice(0, 4000) : (body ? '(非字符串)' : ''), resp: '' };
          captured.push(rec);
          note('FETCH POST', url);
          p.then((r) => {
            try {
              const c = r.clone();
              if (c.body && c.body.getReader) {
                const reader = c.body.getReader();
                const dec = new TextDecoder('utf-8');
                (function pump() {
                  reader.read().then(({ done, value }) => {
                    if (done) { note('FETCH-done(' + rec.resp.length + '字)', url); return; }
                    try { rec.resp = (rec.resp + dec.decode(value, { stream: true })).slice(-40000); } catch (e) {}
                    pump();
                  }).catch(() => {});
                })();
              } else { c.text().then((t) => { rec.resp = t.slice(0, 20000); }).catch(() => {}); }
            } catch (e) {}
          }).catch(() => {});
        }
      } catch (e) {}
      return p;
    };
  } catch (e) { console.warn('[probe] hook fetch 失败', e); }

  // ---- XMLHttpRequest: 所有 POST ----
  try {
    const oOpen = XMLHttpRequest.prototype.open;
    const oSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (m, u) { try { this.__p = { m: m, u: u }; } catch (e) {} return oOpen.apply(this, arguments); };
    XMLHttpRequest.prototype.send = function (body) {
      try {
        const info = this.__p || {};
        if (String(info.m || '').toUpperCase() === 'POST' && !noise(info.u || '')) {
          const rec = { kind: 'XHR', url: info.u, method: info.m, body: typeof body === 'string' ? body.slice(0, 4000) : '', resp: '' };
          captured.push(rec); note('XHR POST', info.u);
          this.addEventListener('load', function () { try { rec.resp = (this.responseText || '').slice(0, 20000); } catch (e) {} });
          this.addEventListener('progress', function () { try { rec.resp = (this.responseText || '').slice(-40000); } catch (e) {} });
        }
      } catch (e) {}
      return oSend.apply(this, arguments);
    };
  } catch (e) { console.warn('[probe] hook XHR 失败', e); }

  // ---- EventSource: 全抓 ----
  try {
    const OrigES = window.EventSource;
    if (OrigES) {
      window.EventSource = function (url, cfg) {
        const es = new OrigES(url, cfg);
        try {
          if (!noise(String(url))) {
            const rec = { kind: 'SSE', url: String(url), method: 'GET', body: '', resp: '' };
            captured.push(rec); note('SSE-open', String(url));
            es.addEventListener('message', (ev) => { try { rec.resp = (rec.resp + '\n' + (ev.data || '')).slice(-40000); } catch (e) {} });
          }
        } catch (e) {}
        return es;
      };
      window.EventSource.prototype = OrigES.prototype;
    }
  } catch (e) { console.warn('[probe] hook EventSource 失败', e); }

  // ---- WebSocket: 全抓 ----
  try {
    const OrigWS = window.WebSocket;
    if (OrigWS) {
      window.WebSocket = function (url, protocols) {
        const ws = protocols ? new OrigWS(url, protocols) : new OrigWS(url);
        try {
          if (!noise(String(url))) {
            const rec = { kind: 'WS', url: String(url), method: 'WS', body: '', resp: '' };
            captured.push(rec); note('WS-open', String(url));
            ws.addEventListener('message', (ev) => { try { const d = typeof ev.data === 'string' ? ev.data : '(binary)'; rec.resp = (rec.resp + '\n' + d).slice(-40000); } catch (e) {} });
            const oSendWs = ws.send;
            ws.send = function (d) { try { rec.body = (rec.body + '\n' + (typeof d === 'string' ? d : '(binary)')).slice(-4000); } catch (e) {} return oSendWs.apply(this, arguments); };
          }
        } catch (e) {}
        return ws;
      };
      window.WebSocket.prototype = OrigWS.prototype;
    }
  } catch (e) { console.warn('[probe] hook WebSocket 失败', e); }

  function dumpText() {
    if (!captured.length) return '';
    const out = ['===== chat 抓包 v1.1 (所有POST+WS/SSE) =====', '共 ' + captured.length + ' 条', ''];
    captured.forEach((c, i) => {
      out.push('--- #' + (i + 1) + ' [' + c.kind + '] ' + c.method + ' ---');
      out.push('URL: ' + c.url);
      if (c.body) out.push('BODY: ' + c.body);
      out.push('RESP(' + (c.resp || '').length + '字): ' + (c.resp || '(空)'));
      out.push('');
    });
    return out.join('\n');
  }

  function copyDump() {
    const txt = dumpText();
    if (!txt) { alert('还没抓到 POST 请求。先在 chat 提问并等答案吐完, 再按 Alt+Shift+C。'); return; }
    try { GM_setClipboard(txt, 'text'); } catch (e) {}
    console.log('%c[probe] ==== 抓包全文(已复制剪贴板) ====\n', 'color:green', txt);
    alert('已抓到 ' + captured.length + ' 条, 已复制到剪贴板! 直接 Ctrl+V 贴给 Claude。');
  }

  window.addEventListener('keydown', (e) => {
    if (e.altKey && e.shiftKey && (e.key === 'C' || e.key === 'c')) { e.preventDefault(); copyDump(); }
  }, true);

  try { GM_registerMenuCommand('② 抓包→复制剪贴板', copyDump); } catch (e) {}

  console.log('%c[probe] v1.1 已加载 (所有POST+WS/SSE, 含跨域). 提问等答完按 Alt+Shift+C.', 'color:blue;font-weight:bold');
})();
