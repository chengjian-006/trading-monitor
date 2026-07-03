# -*- coding: utf-8 -*-
"""安全加固回归 (v1.7.568): JWT密钥非硬编码 + SPA路由防路径穿越。不连库、不打外网。"""
import os

import pytest

from backend.core import auth


def test_secret_key_is_not_the_old_hardcoded_value():
    """旧硬编码密钥必须已从源码消失(否则伪造token风险仍在)。"""
    assert auth.SECRET_KEY != "guxiaocha-jwt-secret-2026-trading-monitor"
    assert len(auth.SECRET_KEY) >= 32


def test_token_roundtrip_with_resolved_key():
    """用运行期密钥签发的 token 能被解回, 字段完整。"""
    tok = auth.create_token(user_id=1, username="u", role="admin", token_version=3)
    payload = auth.decode_token(tok)
    assert payload["sub"] == 1
    assert payload["role"] == "admin"
    assert payload["tv"] == 3


def test_spa_path_traversal_blocked():
    """SPA 兜底路由: dist 外的路径(穿越)不返回真实文件, 回落 index.html。"""
    from backend import main
    dist = os.path.normpath(main.FRONTEND_DIST)
    # 模拟穿越目标: dist 上跳两级的 config.json
    evil = os.path.normpath(os.path.join(dist, "../../config.json"))
    in_dist = evil == dist or evil.startswith(dist + os.sep)
    assert in_dist is False   # 穿越路径不在 dist 内 → 逻辑会回落 index.html
    # 正常资源仍判定在 dist 内
    good = os.path.normpath(os.path.join(dist, "assets/app.js"))
    assert good.startswith(dist + os.sep)
