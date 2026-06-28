#!/bin/bash
cd /opt/trading-monitor
kill $(ps aux | grep bt_refresh | grep python | awk '{print $2}') 2>/dev/null
sleep 1
PYTHONUNBUFFERED=1 /opt/trading-monitor/venv/bin/python backend/scripts/bt_refresh_model_backtest.py > /tmp/bt_refresh2.log 2>&1
echo "EXIT_CODE=$?"
