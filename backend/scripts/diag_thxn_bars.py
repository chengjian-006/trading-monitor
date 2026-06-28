import json, pymysql
from backend.core.config import load_config
cfg = load_config()["database"]
conn = pymysql.connect(host=cfg["host"], port=cfg.get("port",3306), user=cfg["user"],
                       password=cfg["password"], db=cfg["db"], charset="utf8mb4",
                       cursorclass=pymysql.cursors.DictCursor)
with conn.cursor() as cur:
    cur.execute("SELECT trade_date,open,high,low,close,volume FROM cfzy_sys_kline_cache "
                "WHERE code=%s ORDER BY trade_date DESC LIMIT 720", ("300390",))
    rows = cur.fetchall()
conn.close()
rows = list(reversed(rows))
bars=[]
for r in rows:
    o,h,l,c,v = r["open"],r["high"],r["low"],r["close"],r["volume"]
    if None in (o,h,l,c): continue
    bars.append({"o":o,"h":h,"l":l,"c":c,"v":v or 0.0,"vwap":round((h+l+c)/3,4)})
out={"n":len(bars),"first":rows[0]["trade_date"],"last":rows[-1]["trade_date"],
     "last_close":bars[-1]["c"],
     "last25":[{"d":r["trade_date"],"o":r["open"],"h":r["high"],"l":r["low"],"c":r["close"],"v":r["volume"]} for r in rows[-25:]],
     "bars":bars}
json.dump(out, open("scripts/_thxn_bars.json","w"))
print(json.dumps({"n":out["n"],"first":out["first"],"last":out["last"],"last_close":out["last_close"]}, ensure_ascii=False))
