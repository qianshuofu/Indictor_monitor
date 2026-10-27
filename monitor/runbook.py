"""每日更新手册（每日更新手册.md）的辅助命令，定时任务每天按手册跑一遍时用：

    .venv/bin/python monitor/runbook.py due                        # 今天到期的浏览/搜索条目
    .venv/bin/python monitor/runbook.py text 网址 [--grep 关键词]    # 取网页正文（sec.gov 自动带 SEC 要求的请求头）
    .venv/bin/python monitor/runbook.py add REF 日期 ITEM 数值 --text 说明 --source 网址 [--new-item] [--force]
    .venv/bin/python monitor/runbook.py done 条目 [条目 ...]          # 条目今天已处理（不论有无新数），到期前不再列出
    .venv/bin/python monitor/runbook.py status                     # update.sh 跑完后：日志摘要与失败的序列
    .venv/bin/python monitor/runbook.py snap 序列 日期 数值           # 快照序列（DRAM 现货价）补录
    .venv/bin/python monitor/runbook.py build                      # 收尾：重载手工记录并重建看板

add 按 (ref, date, item) 覆盖或插入 manual_data.csv 的一行，并立即重载数据库 manual 表。不要直接写库：
update_data.py 每次运行都按 CSV 整表重载 manual 表，直接写进库里的记录会被清掉。
due、text、add、done 可以和后台的 update.sh 同时运行；status、snap、build 要等 update.sh 跑完。
"""
import argparse
import csv
import io
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update_data as U  # noqa: E402
from indicators import SERIES, THESES  # noqa: E402

# SEC 按 IP 限速（每秒 10 次）。update.sh 可能同时在跑、自己用到每秒 8 次，这里只用 2 次
U.LIMITS["data.sec.gov"] = U.LIMITS["www.sec.gov"] = U.RateLimiter(2)

STATE = U.ROOT / "data" / "runbook_state.json"  # 各条目上次处理的日期
LOG = U.ROOT / "data" / "update.log"  # 手册第 2 步把 update.sh 的输出写在这里
REFS = {i["ref"] for t in THESES for i in t["indicators"] if i["ref"]}
TODAY = date.today()
NEVER = date(1900, 1, 1)

# 业绩 8-K 出来后才会有新数的两项；item 名称与 manual_data.csv 里已有的写法一致
CLOUD_ITEM = {"AMZN": "AWS 收入同比", "MSFT": "Azure 及其他云服务收入同比", "GOOGL": "Google Cloud 收入同比"}
CAPEX_PREFIX = {"META": "Meta", "GOOGL": "Alphabet", "AMZN": "Amazon", "MSFT": "微软", "ORCL": "甲骨文",
                "CRWV": "CoreWeave"}
KEYS = {"vlcc_td3c", "cb_gold", "model_arr", "default_rate", "korea_semi_exports", "subprime_auto_60d",
        *(f"cloud_growth:{t}" for t in CLOUD_ITEM), *(f"capex_guidance:{t}" for t in CAPEX_PREFIX)}


def die(msg):
    sys.exit(f"✗ {msg}")


def db():
    """update.sh 可能正在并行写库：等锁最多 60 秒，而不是 5 秒后报 database is locked。"""
    con = sqlite3.connect(U.DB, timeout=60)
    con.executescript(U.SCHEMA)
    return con


def load_state():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def days_since(st, key):
    return (TODAY - date.fromisoformat(st[key])).days if key in st else 10 ** 4


def manual():
    return pd.read_csv(U.MANUAL, comment="#", dtype=str).fillna("")


def latest(df, ref, item=None, prefix=None):
    g = df[df["ref"] == ref]
    if item is not None:
        g = g[g["item"] == item]
    if prefix is not None:
        g = g[g["item"].str.startswith(prefix)]
    return date.fromisoformat(g["date"].max()) if len(g) else NEVER


def fmt(d):
    return "无" if d == NEVER else d.isoformat()


def month_start(back):
    """往前数 back 个月的月初（0 = 本月）。"""
    y, m = divmod(TODAY.year * 12 + TODAY.month - 1 - back, 12)
    return date(y, m + 1, 1)


def quarter_end_before(d):
    """d 之前最近一个已结束季度的季末。"""
    return date(d.year, (d.month - 1) // 3 * 3 + 1, 1) - timedelta(days=1)


def earnings_8k(ticker):
    """最近一份含 Item 2.02（经营业绩）的 8-K：(申报日, 申报目录网址)。"""
    def fetch():
        js = U.get(f"https://data.sec.gov/submissions/CIK{U.CIK[ticker]:010d}.json", headers=U.SEC_UA).json()
        r = js["filings"]["recent"]
        for form, filed, items, acc in zip(r["form"], r["filingDate"], r["items"], r["accessionNumber"]):
            if form == "8-K" and "2.02" in items:
                return date.fromisoformat(filed), f"https://www.sec.gov/Archives/edgar/data/{U.CIK[ticker]}/{acc.replace('-', '')}/"
        return NEVER, ""
    return U.cached(("8k", ticker), fetch)


def exhibit_991(folder):
    """申报目录里的附件 99.1（业绩新闻稿），找不到就给目录本身。"""
    names = [x["name"] for x in U.get(folder + "index.json", headers=U.SEC_UA).json()["directory"]["item"]]
    return folder + next((n for n in names if re.search(r"ex(hibit)?[-_]?99[-_.]?0?1\D", n, re.I)), "")


# ---------------------------------------------------------------------------
def cmd_due(_):
    st, df = load_state(), manual()
    todo, rest = [], []

    def put(key, due, why, how=""):
        (todo if due else rest).append((key, why, how))

    # B1 VLCC：波罗的海交易所周五（伦敦下午）发周报，北京时间周六起可读；当天的周五不算
    fri = TODAY - timedelta(days=(TODAY.weekday() - 4) % 7 or 7)
    yr, wk, _ = fri.isocalendar()
    last = latest(df, "vlcc_td3c", "TD3C WS")
    put("vlcc_td3c", last < fri and days_since(st, "vlcc_td3c") >= 1,
        f"已录到 {fmt(last)}，最近一期周报 {fri}（第 {wk} 周）",
        f"B1：内置浏览器打开 https://www.balticexchange.com/en/data-services/WeeklyRoundup/tanker/news/{yr}/tanker-report-week-{wk}.html")

    # B2 世界黄金协会：季末后约 30 天发布《黄金需求趋势》，页面未发布时返回 403
    qe = quarter_end_before(TODAY)
    q = (qe.month - 1) // 3 + 1
    url = f"https://www.gold.org/goldhub/research/gold-demand-trends/gold-demand-trends-q{q}-{qe.year}/central-banks"
    last = latest(df, "cb_gold", "央行净购金（季度）")
    if last >= qe:
        put("cb_gold", False, f"{qe.year}Q{q} 已录入")
    elif TODAY < qe + timedelta(days=25) or days_since(st, "cb_gold") < 1:
        put("cb_gold", False, f"{qe.year}Q{q} 报告约在季末后 30 天发布，今天不查")
    else:
        try:
            r = U.SESSION.get(url, timeout=30)
            m = re.search(r"Central Banks and\s+Other Institutions\s+(-?[\d,.]+)\s+(-?[\d,.]+)", U.plain(r.text))
            ok = r.status_code == 200
            hint = f"表中 {qe.year}Q{q} 为 {m.group(2)} 吨（上年同期 {m.group(1)}）" if ok and m else ""
            put("cb_gold", ok, f"{qe.year}Q{q} " + (f"已发布；{hint}" if ok else f"尚未发布（HTTP {r.status_code}）"),
                f"B2：{url}")
        except Exception as e:  # noqa: BLE001
            put("cb_gold", False, f"页面打不开，明天再查（{e!r:.80}）")

    # C1 云业务增速、C2 资本开支指引：有新的业绩 8-K 才到期
    for t, it in CLOUD_ITEM.items():
        key = f"cloud_growth:{t}"
        try:
            filed, folder = earnings_8k(t)
            last, rq = latest(df, "cloud_growth", it), quarter_end_before(filed)
            due = rq > last and st.get(key, "") < filed.isoformat()
            put(key, due, f"最近业绩 8-K {fmt(filed)}（{rq} 季度），已录到 {fmt(last)}",
                f"C1：附件 99.1 {exhibit_991(folder)}" if due else "")
        except Exception as e:  # noqa: BLE001
            put(key, False, f"SEC 查询失败，明天再查（{e!r:.80}）")
    for t, p in CAPEX_PREFIX.items():
        key = f"capex_guidance:{t}"
        try:
            filed, folder = earnings_8k(t)
            last = latest(df, "capex_guidance", prefix=p + " ")
            due = filed > last + timedelta(days=3) and st.get(key, "") < filed.isoformat()
            put(key, due, f"最近业绩 8-K {fmt(filed)}，指引已录到 {fmt(last)}",
                f"C2：附件 99.1 {exhibit_991(folder)}" if due else "")
        except Exception as e:  # noqa: BLE001
            put(key, False, f"SEC 查询失败，明天再查（{e!r:.80}）")

    # C3 模型公司 ARR：每 7 天搜一次
    n = days_since(st, "model_arr")
    put("model_arr", n >= 7, "上次检查：" + ("从未" if n >= 10 ** 4 else f"{n} 天前"), "C3：搜索近 7 天的新闻")

    # C4 穆迪违约率：月中发布上月数据；20 日起找上月，之前找上上月；没找到 5 天后再找
    target = month_start(0 if TODAY.day >= 20 else 1) - timedelta(days=1)
    last = latest(df, "default_rate", "穆迪全球投机级违约率（12个月滚动）")
    put("default_rate", last < target and days_since(st, "default_rate") >= 5,
        f"已录到 {fmt(last)}，应有 {target}", f"C4：找 {target:%Y-%m} 的数据")

    # C5 韩国半导体出口：产业部每月 1 日公布上月初值
    target = month_start(1)
    last = latest(df, "korea_semi_exports", "产业部半导体出口（初值）")
    put("korea_semi_exports", last < target and days_since(st, "korea_semi_exports") >= 2,
        f"已录到 {fmt(last)}，应有 {target:%Y-%m}", f"C5：找 {target:%Y-%m} 的初值")

    # C6 惠誉次级车贷 ABS 60+ 逾期率：月中发布上月数据；没找到 7 天后再找
    target = month_start(1 if TODAY.day >= 20 else 2)
    last = latest(df, "subprime_auto_60d", "惠誉次级车贷 ABS 60+ 逾期率")
    put("subprime_auto_60d", last < target and days_since(st, "subprime_auto_60d") >= 7,
        f"已录到 {fmt(last)}，应有 {target:%Y-%m}", f"C6：找 {target:%Y-%m} 的数据")

    print(f"今天 {TODAY}，要处理 {len(todo)} 项（按顺序；每项处理完运行 done）：")
    for key, why, how in todo:
        print(f"  ● {key}：{why}\n      → {how}")
    print("不用处理：")
    for key, why, _ in rest:
        print(f"  · {key}：{why}")


def cmd_status(_):
    lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines() if LOG.exists() else []
    if not any(ln.startswith("exit=") for ln in lines):
        print(f"⚠ {LOG.name} 里还没有 exit= 结束标记：update.sh 可能还在跑，等它跑完再看")
    print("update.log 摘要：")
    for ln in [ln for ln in lines if re.search(r"✗|跳过|宽度|完成：|已生成|⚠|exit=|Traceback|Error:", ln)][-25:]:
        print("  " + ln.strip())
    bad = db().execute("SELECT series_id, name, last_obs, status FROM series WHERE status != 'ok' "
                       "ORDER BY series_id").fetchall()
    print(f"失败的序列：{len(bad)} 条")
    for sid, name, last_obs, status in bad:
        tip = "  → B3：浏览 DRAMeXchange 后用 snap 补录" if SERIES.get(sid, {}).get("source") == "dramx" else ""
        print(f"  ✗ {sid} {name}（库里最新 {last_obs}）{status[:120]}{tip}")


def cmd_build(_):
    """收尾：按 CSV 重载 manual 表（与 update.sh 并行写入时也不会漏记录），再重建看板。"""
    n = U.load_manual(db())
    import build_dashboard
    build_dashboard.main()
    print(f"（manual 表共 {n} 条）")


def cmd_text(a):
    host = urlparse(a.url).hostname or ""
    t = U.plain(U.get(a.url, headers=U.SEC_UA if host.endswith("sec.gov") else None).text)
    if not a.grep:
        print(t[:a.chars])
        return
    hits = [m.start() for m in re.finditer(re.escape(a.grep), t, re.I)][:a.max]
    if not hits:
        print(f"正文里没有“{a.grep}”（正文共 {len(t)} 字）")
    for i in hits:
        print("…" + t[max(0, i - a.width):i + a.width] + "…\n")


def cmd_add(a):
    if a.ref not in REFS:
        die(f"没有 ref “{a.ref}”，可用：{', '.join(sorted(REFS))}")
    try:
        d = date.fromisoformat(a.date)
    except ValueError:
        die(f"日期要写成 YYYY-MM-DD：{a.date}")
    if d > TODAY:
        die(f"日期 {d} 在今天之后")
    if a.value != "":
        try:
            v = float(a.value)
        except ValueError:
            die(f"数值不是数字：{a.value}")
    if "#" in a.item or not a.item.strip():
        die("item 不能为空、不能含 #")
    src, text = a.source.split("#")[0], a.text.replace("#", "＃")  # CSV 按 # 截断注释
    if not re.match(r"https?://", src):
        die("source 必须是网址")
    df = manual()
    same = df[(df["ref"] == a.ref) & (df["item"] == a.item)].sort_values("date")
    if same.empty and not a.new_item:
        items = sorted(set(df.loc[df["ref"] == a.ref, "item"]))
        die(f"{a.ref} 下还没有 item “{a.item}”，已有：{items or '无'}。确认要新开一条线才加 --new-item")
    prev = pd.to_numeric(same["value"], errors="coerce").dropna()
    if a.value != "" and len(prev) and not a.force and not (0.2 <= v / prev.iloc[-1] <= 5 if prev.iloc[-1] else True):
        die(f"与上一条（{prev.iloc[-1]:g}）相差 5 倍以上，单位可能写错；确认无误加 --force")

    # 原样读写：注释行是 \n、数据行是 \r\n（csv 模块的默认），新行沿用数据行的换行符
    with open(U.MANUAL, encoding="utf-8", newline="") as fh:
        lines = re.findall(r"[^\n]*\n|[^\n]+$", fh.read())
    eol = "\r\n" if any(ln.endswith("\r\n") for ln in lines if not ln.startswith("#")) else "\n"
    buf = io.StringIO()
    csv.writer(buf, lineterminator=eol).writerow([a.ref, a.date, a.item, a.value, text, src])
    row = buf.getvalue()
    hit = after = None
    for i, ln in enumerate(lines):
        if ln.startswith("#") or not ln.strip():
            continue
        f = next(csv.reader([ln]))
        if f[:1] == [a.ref]:
            after = i
            if f[1:3] == [a.date, a.item]:
                hit = i
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += eol
    if hit is not None:
        lines[hit] = row
    elif after is not None:  # 插在同一 ref 的最后一行后面，CSV 里按指标成块
        lines.insert(after + 1, row)
    else:
        lines.append(row)
    # 写临时文件再整体替换：并行运行的 update.sh 读到的要么是旧文件、要么是新文件，不会读到写了一半的
    tmp = U.MANUAL.with_name(U.MANUAL.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write("".join(lines))
    shutil.copymode(U.MANUAL, tmp)
    os.replace(tmp, U.MANUAL)
    n = U.load_manual(db())
    print(f"{'覆盖' if hit is not None else '新增'}：{row.strip()}\n（manual 表已重载，共 {n} 条）")


def cmd_snap(a):
    meta = SERIES.get(a.sid)
    if not meta or not meta.get("snapshot"):
        die(f"{a.sid} 不是快照序列，可用：{', '.join(k for k, m in SERIES.items() if m.get('snapshot'))}")
    d, v = pd.Timestamp(date.fromisoformat(a.date)), float(a.value)
    U.record_snapshot([(a.sid, d, v, meta["source"])])  # update.sh 也写这个文件，所以 snap 要等它跑完
    U.save_series(db(), a.sid, pd.Series([v], index=[d]), meta)
    print(f"已记录 {a.sid} {d:%Y-%m-%d} = {v:g}（data/snapshots.csv 与数据库）")


def cmd_done(a):
    unknown = [k for k in a.keys if k not in KEYS]
    if unknown:
        die(f"未知条目 {unknown}，可用：{', '.join(sorted(KEYS))}")
    st = load_state()
    st.update({k: TODAY.isoformat() for k in a.keys})
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"已记下 {', '.join(a.keys)} 今天处理过")


def main():
    ap = argparse.ArgumentParser(description="每日更新手册的辅助命令")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("due", help="今天要处理的条目").set_defaults(fn=cmd_due)
    p = sub.add_parser("text", help="取网页正文")
    p.add_argument("url")
    p.add_argument("--grep", default="", help="只看关键词前后的文字")
    p.add_argument("--width", type=int, default=400, help="关键词前后各取多少字")
    p.add_argument("--max", type=int, default=5, help="最多显示几处匹配")
    p.add_argument("--chars", type=int, default=4000, help="不加 --grep 时显示前多少字")
    p.set_defaults(fn=cmd_text)
    p = sub.add_parser("add", help="写一条手工记录")
    for k in ("ref", "date", "item", "value"):
        p.add_argument(k)
    p.add_argument("--text", default="")
    p.add_argument("--source", required=True)
    p.add_argument("--new-item", action="store_true", help="该 ref 下新开一条线")
    p.add_argument("--force", action="store_true", help="跳过与上一条数值的量级检查")
    p.set_defaults(fn=cmd_add)
    p = sub.add_parser("done", help="记下条目今天已处理")
    p.add_argument("keys", nargs="+")
    p.set_defaults(fn=cmd_done)
    sub.add_parser("status", help="update.sh 跑完后：日志摘要与失败的序列").set_defaults(fn=cmd_status)
    p = sub.add_parser("snap", help="快照序列补录（等 update.sh 跑完）")
    for k in ("sid", "date", "value"):
        p.add_argument(k)
    p.set_defaults(fn=cmd_snap)
    sub.add_parser("build", help="收尾：重载手工记录并重建看板").set_defaults(fn=cmd_build)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
