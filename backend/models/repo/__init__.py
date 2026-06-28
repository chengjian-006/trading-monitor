# v1.7.x: repository 拆分 — 各业务域 sub-module 由 backend/models/repository.py 聚合 re-export.
# 外部代码继续 `from backend.models import repository` 调用, 不受影响.
