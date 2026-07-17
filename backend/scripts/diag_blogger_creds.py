# -*- coding: utf-8 -*-
"""用「你手动填入的真凭证」直打同花顺, 打印原始响应, 判断 403 到底卡在哪。

凭证只留本机, 不入库、不进对话。跑法(在 backend 的上级目录):
    set PYTHONIOENCODING=utf-8
    py -3 -m backend.scripts.diag_blogger_creds

凭证从哪来: 浏览器 F12 → Network → 重新触发油猴续签 → 找到发往
/api/blogger/renew 的 POST → Payload 里有 cookie / hexin_v / user_code 三个字段,
把它们填进 scratchpad 下的 blogger_creds.json(见下方路径), 或直接改本文件顶部常量。
"""
import asyncio
import json
import os

import httpx

from backend.fetcher.http_client import THS_HEADERS

THS_POST_API = "https://t.10jqka.com.cn/user_center/open/api/content/v2/get_by_uid"

# 凭证文件(自己新建, 填 {"cookie": "...", "hexin_v": "...", "user_code": "..."}):
CREDS_FILE = os.path.join(os.path.dirname(__file__), "blogger_creds.json")


async def main():
    if not os.path.exists(CREDS_FILE):
        print(f"[diag] 请先创建凭证文件: {CREDS_FILE}")
        print('       内容: {"cookie": "...", "hexin_v": "...", "user_code": "..."}')
        print("       (从 F12 Network 里 /api/blogger/renew 的请求 Payload 复制这三个字段)")
        return
    with open(CREDS_FILE, encoding="utf-8") as f:
        creds = json.load(f)

    cookie = creds.get("cookie", "")
    hexin_v = creds.get("hexin_v", "")
    user_code = creds.get("user_code", "")
    print(f"[diag] cookie 长度={len(cookie)} hexin_v 长度={len(hexin_v)} user_code={user_code}")
    # 粗看 cookie 里有没有关键鉴权键(这几个缺了同花顺必拒)
    for key in ("sess_tk", "userid", "user", "ticket", "utk", "u_ttl"):
        print(f"       cookie 含 {key}: {key + '=' in cookie}")

    headers = {
        **THS_HEADERS,
        "Cookie": cookie,
        "hexin-v": hexin_v,
        "Content-Type": "application/json",
        "Referer": f"https://t.10jqka.com.cn/lgt/user_page/?user_code={user_code}",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(THS_POST_API, json={"user_code": user_code},
                                 headers=headers, timeout=15)
    print(f"\n[diag] HTTP {resp.status_code}")
    body = resp.text
    print(f"[diag] 响应体前 500 字符:\n{body[:500]}\n")
    if resp.status_code == 200:
        try:
            data = resp.json()
            print(f"[diag] status_code={data.get('status_code')} status_msg={data.get('status_msg')}")
            n = len(data.get("data", {}).get("contents", []))
            print(f"[diag] 拉到 {n} 条帖子 → 凭证有效, 问题在续签流程/落库")
        except Exception as e:
            print(f"[diag] 200 但非JSON: {e}")
    elif "Nginx forbidden" in body:
        print("[diag] 判定: 边缘 Nginx 拒绝 → 凭证(cookie/hexin-v)未被同花顺接受")
        print("       → 若 cookie 明明是新鲜登录态, 则同花顺改了鉴权口径 / 油猴没读全关键 cookie")


if __name__ == "__main__":
    asyncio.run(main())
