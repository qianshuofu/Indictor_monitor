"""从 data/macro.db 生成单文件看板 dashboard.html（双击用浏览器打开即可）。

用法：python3 monitor/build_dashboard.py
"""
import json
import math
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from indicators import DERIVED, SERIES, THESES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "macro.db"
OUT = ROOT / "dashboard.html"
TEMPLATE = Path(__file__).resolve().parent / "dashboard_template.html"


def rnd(v):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    if v == 0:
        return 0
    return float(f"{v:.5g}")


def main():
    con = sqlite3.connect(DB)
    meta = {r[0]: dict(name=r[1], unit=r[2], freq=r[3], source=r[4], last_updated=r[6], last_obs=r[7],
                       n=r[8], status=r[9])
            for r in con.execute("SELECT * FROM series")}
    needed = {s for t in THESES for i in t["indicators"] for s in i["series"]}
    data = {}
    for sid in needed:
        if sid == "AUCTIONS":
            continue
        rows = con.execute("SELECT date, value FROM observations WHERE series_id=? ORDER BY date", (sid,)).fetchall()
        if rows:
            data[sid] = [[d, rnd(v)] for d, v in rows]
        if sid not in meta:
            m = SERIES.get(sid) or dict(zip(("name", "unit"), DERIVED.get(sid, (sid, ""))))
            meta[sid] = dict(name=m.get("name", sid), unit=m.get("unit", ""), status="未拉取", n=0)
    auctions = pd.read_sql(
        "SELECT auction_date, security_type, security_term, offering_amt, high_yield, bid_to_cover, "
        "indirect_accepted, direct_accepted, primary_dealer_accepted, competitive_accepted "
        "FROM auctions ORDER BY auction_date", con)
    if not auctions.empty:
        comp = auctions["competitive_accepted"].where(auctions["competitive_accepted"] > 0)
        auctions["indirect_pct"] = (auctions["indirect_accepted"] / comp * 100).round(1)
        auctions["dealer_pct"] = (auctions["primary_dealer_accepted"] / comp * 100).round(1)
    auc = auctions[["auction_date", "security_type", "security_term", "offering_amt", "high_yield", "bid_to_cover",
                    "indirect_pct", "dealer_pct"]].astype(object).where(auctions.notna(), None).values.tolist() if not auctions.empty else []
    manual = {}
    for ref, d, item, v, text, src in con.execute("SELECT * FROM manual ORDER BY date"):
        manual.setdefault(ref, []).append([d, item, v, text, src])
    payload = dict(theses=THESES, meta=meta, data=data, auctions=auc, manual=manual,
                   built=datetime.now().strftime("%Y-%m-%d %H:%M"))
    html = TEMPLATE.read_text(encoding="utf-8").replace(
        "/*__DATA__*/null", json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
    # 内联 ECharts，离线也能打开
    lib = Path(__file__).resolve().parent / "echarts.min.js"
    if lib.exists():
        tag = '<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"></script>'
        html = html.replace(tag, "<script>" + lib.read_text(encoding="utf-8") + "</script>")
    OUT.write_text(html, encoding="utf-8")
    print(f"已生成 {OUT}（{OUT.stat().st_size / 1e6:.1f} MB）")


if __name__ == "__main__":
    main()
