"""从 data/macro.db 生成单文件看板 dashboard.html（双击用浏览器打开即可）。

用法：python3 monitor/build_dashboard.py
"""
import copy
import json
import math
import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from indicators import DERIVED, SERIES, THESES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "macro.db"
OUT = ROOT / "dashboard.html"
TEMPLATE = Path(__file__).resolve().parent / "dashboard_template.html"
TIPS = ROOT / "指标解释.md"


def rnd(v):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    if v == 0:
        return 0
    return float(f"{v:.5g}")


def expand(sid, have, manual):
    """看板配置里的几种简写，展开成实际序列：
      ZQ_*          库里所有以 ZQ_ 开头的序列（利率期货合约随月份滚动，不在配置里写死）
      ZQ_DEC+0/+1   今年/明年 12 月的联邦基金期货
      M:ref         manual_data.csv 里该 ref 的数值记录，每个 item 一条序列"""
    if sid.endswith("*"):
        return sorted(k for k in have if k.startswith(sid[:-1]))
    if sid.startswith("ZQ_DEC+"):
        return [f"ZQ_{date.today().year + int(sid[7:])}12"]
    if sid.startswith("M:"):
        rows = [r for r in manual.get(sid[2:], []) if r[2] is not None]
        return [f"{sid}:{item}" for item in dict.fromkeys(r[1] for r in rows)]
    return [sid]


def load_tips():
    """指标解释.md：每个“## 卡片标题”下面的段落，是看板上悬停该指标名时显示的解释。"""
    if not TIPS.exists():
        return {}
    tips, name, buf = {}, None, []
    for line in TIPS.read_text(encoding="utf-8").splitlines() + ["## "]:
        if line.startswith("## "):
            if name:
                tips[name] = re.sub(r"\n{3,}", "\n\n", "\n".join(buf).strip())
            name, buf = line[3:].strip(), []
        elif name:
            buf.append(line.rstrip())
    return tips


def main():
    con = sqlite3.connect(DB)
    meta = {r[0]: dict(name=r[1], unit=r[2], freq=r[3], source=r[4], last_updated=r[6], last_obs=r[7],
                       n=r[8], status=r[9])
            for r in con.execute("SELECT * FROM series")}
    manual = {}
    # 手工录入与自动抓取的事件（FOMC 异议等）同结构，按 ref 合并展示
    for table in ("manual", "events"):
        for ref, d, item, v, text, src in con.execute(f"SELECT * FROM {table} ORDER BY date"):
            manual.setdefault(ref, []).append([d, item, v, text, src])
    for rows in manual.values():
        rows.sort(key=lambda r: r[0])
    have = {r[0] for r in con.execute("SELECT DISTINCT series_id FROM observations")}
    theses = copy.deepcopy(THESES)
    tips = load_tips()
    for name in tips.keys() - {i["name"] for t in theses for i in t["indicators"]}:
        print(f"⚠ 指标解释.md 里的“{name}”没有对应的卡片（标题要与看板上的卡片标题完全一致）")
    data = {}
    for t in theses:
        for ind in t["indicators"]:
            if ind["name"] in tips:
                ind["tip"] = tips[ind["name"]]
            ind["series"] = [x for sid in ind["series"] for x in expand(sid, have, manual)]
            for sid in ind["series"]:
                if sid.startswith("M:"):  # 手工数值记录当作普通序列画
                    ref, item = sid[2:].split(":", 1)
                    data[sid] = [[r[0], rnd(r[2])] for r in manual[ref] if r[1] == item and r[2] is not None]
                    meta[sid] = dict(name=item, unit=ind["unit"] or "", source="manual", status="ok", n=len(data[sid]))
    needed = {s for t in theses for i in t["indicators"] for s in i["series"] if not s.startswith("M:")}
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
    payload = dict(theses=theses, meta=meta, data=data, auctions=auc, manual=manual,
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
