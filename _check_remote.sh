#!/bin/bash
echo "===== 1. ERROR/WARNING 总数 (近1h) ====="
journalctl -u trading-monitor --since "1 hour ago" --no-pager | grep -cE "WARNING|ERROR"

echo ""
echo "===== 2. 错误类型分布 (近1h) ====="
journalctl -u trading-monitor --since "1 hour ago" --no-pager | grep -E "WARNING|ERROR" | \
  grep -oE "Server disconnected|JSONDecodeError|NOT_ENOUGH_MEMBER_BUDGET|popularity_ai|EastMoney|sinajs|10jqka|wechat|429|403|500|502|503|Timeout|失败|Failed|失败" | \
  sort | uniq -c | sort -rn | head -20

echo ""
echo "===== 3. 各外部接口最近调用结果 (近30min) ====="
echo "--- 东方财富 行情/K线/板块 ---"
journalctl -u trading-monitor --since "30 minutes ago" --no-pager | grep -oE "push2[^ ]*eastmoney[^ ]*" | sort -u | head -5
journalctl -u trading-monitor --since "30 minutes ago" --no-pager | grep "EastMoney" | grep -E "failed|FAIL|ERROR" | tail -5

echo ""
echo "--- 新浪 行情 ---"
journalctl -u trading-monitor --since "30 minutes ago" --no-pager | grep -E "sinajs|sina" | grep -E "failed|FAIL|ERROR|warn" -i | tail -5

echo ""
echo "--- 同花顺 (10jqka) ---"
journalctl -u trading-monitor --since "30 minutes ago" --no-pager | grep -E "10jqka|ths" | grep -E "failed|FAIL|ERROR" | tail -5

echo ""
echo "--- 企业微信 ---"
journalctl -u trading-monitor --since "1 day ago" --no-pager | grep -iE "wechat|qyapi|webhook" | tail -10

echo ""
echo "--- AI接口 (DeepSeek/PPIO) ---"
journalctl -u trading-monitor --since "6 hours ago" --no-pager | grep -iE "ppio|deepseek|popularity_ai|ai_analyst|api\.deepseek" | grep -iE "失败|fail|error|warn|403|429" | tail -10

echo ""
echo "===== 4. MySQL 连接情况 ====="
journalctl -u trading-monitor --since "6 hours ago" --no-pager | grep -iE "mysql|aiomysql|database|repository" | grep -iE "error|exception|fail|disconnect" | tail -5
echo "(空表示无错误)"

echo ""
echo "===== 5. 定时任务最近执行情况 ====="
journalctl -u trading-monitor --since "30 minutes ago" --no-pager | grep -E "apscheduler" | grep -E "executed successfully|failed|missed|skipped" | awk '{print $NF, $(NF-1)}' | sort | uniq -c | sort -rn | head

echo ""
echo "===== 6. CPU/内存 ====="
ps -o pid,pcpu,pmem,rss,cmd -p $(pidof -s python3 || pgrep -f uvicorn) | head
echo ""
free -m | head -2

echo ""
echo "===== 7. 磁盘 ====="
df -h / /opt 2>/dev/null | head
