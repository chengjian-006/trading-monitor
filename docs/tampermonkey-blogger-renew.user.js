// ==UserScript==
// @name         同花顺博主cookie自动续签(观潮)
// @namespace    chaoxiao.blogger.renew
// @version      1.0
// @description  在登录态同花顺页读取cookie(含httpOnly)自动续签到观潮服务器, 免手抓cURL
// @match        https://t.10jqka.com.cn/*
// @grant        GM_cookie
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_registerMenuCommand
// @grant        GM_notification
// @connect      <你的服务器地址>
// ==/UserScript==

(function () {
  'use strict';

  // ============ 配置(按需改这三行) ============
  // 观潮系统公网地址 + /api/blogger/renew (nginx在80端口反代, 不是8888)
  const RENEW_URL = 'http://<你的服务器地址>/api/blogger/renew';
  const RENEW_TOKEN = '<与服务器 config.json 的 renew_token 一致>';   // 勿外泄
  const USER_CODE = '<博主 user_code>';                     // 例: 全能的野人
  const AUTO_MIN_INTERVAL_HOURS = 6;                        // 自动续签最小间隔(防每次开页都打)
  // 注: 若把 RENEW_URL 改成别的域名/IP, 上面 @connect 也要同步改成那个主机
  // ===========================================

  function log(msg, notify) {
    console.log('[博主续签] ' + msg);
    if (notify) { try { GM_notification({ title: '博主cookie续签', text: msg, timeout: 4000 }); } catch (e) {} }
  }

  function buildAndSend(manual) {
    GM_cookie.list({}, function (cookies, error) {
      if (error || !cookies || !cookies.length) {
        log('读取cookie失败(可能需在油猴里授予cookie权限): ' + (error || '空'), manual);
        return;
      }
      // 只取同花顺域的 cookie
      const jq = cookies.filter(c => (c.domain || '').indexOf('10jqka.com.cn') >= 0);
      const use = jq.length ? jq : cookies;
      const cookieStr = use.map(c => c.name + '=' + c.value).join('; ');
      const vc = use.find(c => c.name === 'v');
      const hexin = vc ? vc.value : '';
      if (!hexin) { log('未找到 v(hexin-v) cookie, 可能未登录同花顺', manual); return; }

      GM_xmlhttpRequest({
        method: 'POST',
        url: RENEW_URL,
        headers: { 'Content-Type': 'application/json' },
        data: JSON.stringify({ token: RENEW_TOKEN, cookie: cookieStr, hexin_v: hexin, user_code: USER_CODE }),
        timeout: 20000,
        onload: function (resp) {
          let r = {};
          try { r = JSON.parse(resp.responseText); } catch (e) {}
          if (r.ok) {
            GM_setValue('last_renew_ts', Date.now());
            log('续签成功 ✓ 最新帖 ' + (r.latest_post_time || '?') + ' (' + r.posts_count + '帖)', manual);
          } else {
            log('续签失败: ' + (r.error || ('HTTP ' + resp.status)), true);
          }
        },
        onerror: function (e) { log('请求出错(检查RENEW_URL/@connect/网络): ' + JSON.stringify(e), true); },
        ontimeout: function () { log('请求超时', true); },
      });
    });
  }

  // 手动: 油猴菜单"立即续签博主cookie"
  GM_registerMenuCommand('立即续签博主cookie', function () { buildAndSend(true); });

  // 自动: 进同花顺页且距上次续签超过阈值才打(避免频繁请求)
  const last = GM_getValue('last_renew_ts', 0);
  if (Date.now() - last > AUTO_MIN_INTERVAL_HOURS * 3600 * 1000) {
    setTimeout(function () { buildAndSend(false); }, 3000);
  }
})();
