"""拉取/更新所有可免费获取的指标，写入 SQLite（data/macro.db）。

用法：
    python3 monitor/update_data.py            # 增量更新全部
    python3 monitor/update_data.py --full     # 从 START 起全量重拉
    python3 monitor/update_data.py --breadth  # 额外计算标普500站上200日均线占比（16线程并行，约1分钟）
    python3 monitor/update_data.py --only DGS10,SPY

下载在线程池里并行（--workers，默认 8），入库只在主线程。本机运行 moomoo OpenD（127.0.0.1:11111）
时，顺带用选股器记录当日标普500/全美股站上200日均线的占比；没开 OpenD 就跳过。

依赖：pandas、requests、openpyxl（读 Excel）、xlrd（读 EIA 的 .xls）；可选 moomoo-api
"""
import argparse
import html as htmllib
import io
import logging
import re
import socket
import sqlite3
import sys
import threading
import time
import traceback
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from indicators import DERIVED, SERIES, START, THESES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "macro.db"
MANUAL = ROOT / "manual_data.csv"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh) indicator-monitor research contact@example.com"}
SEC_UA = {"User-Agent": "indicator-monitor research contact@example.com"}
SESSION = requests.Session()
SESSION.headers.update(UA)
# 多线程共用一个 Session：连接池放大到不小于线程数，免得反复建连
SESSION.mount("https://", requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=32))


class RateLimiter:
    """按主机限速，线程间共享。SEC 要求每秒不超过 10 次请求，这里取 8 次留余量。"""

    def __init__(self, per_sec):
        self.interval, self.lock, self.next = 1.0 / per_sec, threading.Lock(), 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next)
            self.next = t + self.interval
        time.sleep(max(0.0, t - now))


_SEC_LIMIT = RateLimiter(8)
LIMITS = {"data.sec.gov": _SEC_LIMIT, "www.sec.gov": _SEC_LIMIT, "query1.finance.yahoo.com": RateLimiter(12)}


# ---------------------------------------------------------------------------
# 数据库
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS series (
    series_id TEXT PRIMARY KEY, name TEXT, unit TEXT, freq TEXT, source TEXT, code TEXT,
    last_updated TEXT, last_obs TEXT, n_obs INTEGER, status TEXT
);
CREATE TABLE IF NOT EXISTS observations (
    series_id TEXT, date TEXT, value REAL, PRIMARY KEY (series_id, date)
);
CREATE TABLE IF NOT EXISTS auctions (
    cusip TEXT, auction_date TEXT, security_type TEXT, security_term TEXT, offering_amt REAL,
    high_yield REAL, bid_to_cover REAL, indirect_accepted REAL, direct_accepted REAL,
    primary_dealer_accepted REAL, competitive_accepted REAL,
    PRIMARY KEY (cusip, auction_date)
);
CREATE TABLE IF NOT EXISTS manual (
    ref TEXT, date TEXT, item TEXT, value REAL, text TEXT, source TEXT,
    PRIMARY KEY (ref, date, item)
);
"""


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    return con


def last_date(con, sid):
    r = con.execute("SELECT MAX(date) FROM observations WHERE series_id=?", (sid,)).fetchone()
    return r[0]


def save_series(con, sid, s: pd.Series, meta, status="ok"):
    s = s.dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    rows = [(sid, pd.Timestamp(d).strftime("%Y-%m-%d"), float(v)) for d, v in s.items()]
    con.executemany("INSERT OR REPLACE INTO observations VALUES (?,?,?)", rows)
    n, last = con.execute("SELECT COUNT(*), MAX(date) FROM observations WHERE series_id=?", (sid,)).fetchone()
    con.execute("INSERT OR REPLACE INTO series VALUES (?,?,?,?,?,?,?,?,?,?)",
                (sid, meta.get("name"), meta.get("unit"), meta.get("freq"), meta.get("source"),
                 meta.get("code"), datetime.now().isoformat(timespec="seconds"), last, n, status))
    con.commit()
    return len(rows)


def mark_failed(con, sid, meta, err):
    row = con.execute("SELECT n_obs, last_obs FROM series WHERE series_id=?", (sid,)).fetchone()
    con.execute("INSERT OR REPLACE INTO series VALUES (?,?,?,?,?,?,?,?,?,?)",
                (sid, meta.get("name"), meta.get("unit"), meta.get("freq"), meta.get("source"),
                 meta.get("code"), datetime.now().isoformat(timespec="seconds"),
                 row[1] if row else None, row[0] if row else 0, f"error: {err}"[:300]))
    con.commit()


def load(con, sid) -> pd.Series:
    df = pd.read_sql("SELECT date, value FROM observations WHERE series_id=? ORDER BY date", con, params=(sid,))
    if df.empty:
        return pd.Series(dtype=float)
    return pd.Series(df["value"].values, index=pd.to_datetime(df["date"]), name=sid)


def get(url, headers=None, tries=3, **kw):
    lim = LIMITS.get(urlparse(url).hostname)
    for i in range(tries):
        try:
            if lim:
                lim.wait()
            r = SESSION.get(url, headers=headers, timeout=60, **kw)
            if r.status_code == 429:
                time.sleep(5 * (i + 1))
                continue
            r.raise_for_status()
            return r
        except requests.RequestException:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


# ---------------------------------------------------------------------------
# 各数据源
# ---------------------------------------------------------------------------
def fetch_fred(code, start):
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={code}&cosd={start}"
    df = pd.read_csv(io.StringIO(get(url).text))
    df.columns = ["date", "value"]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return pd.Series(df["value"].values, index=pd.to_datetime(df["date"]))


def fetch_yahoo(ticker, start):
    p1 = int(pd.Timestamp(start).timestamp())
    p2 = int(time.time()) + 86400
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(ticker)}"
           f"?period1={p1}&period2={p2}&interval=1d&events=div%2Csplit")
    js = get(url).json()["chart"]["result"][0]
    ts = pd.to_datetime(js["timestamp"], unit="s").normalize()
    ind = js["indicators"]
    px = ind.get("adjclose", [{}])[0].get("adjclose") or ind["quote"][0]["close"]
    return pd.Series(px, index=ts, dtype=float)


CIK = {"MSFT": 789019, "GOOGL": 1652044, "AMZN": 1018724, "META": 1326801,
       "ORCL": 1341439, "CRWV": 1769628, "NBIS": 1513845}
CAPEX_CONCEPTS = ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
                  "PaymentsForProceedsFromProductiveAssets"]
_facts_cache = {}
_facts_locks = {t: threading.Lock() for t in CIK}


def sec_facts(ticker):
    with _facts_locks[ticker]:  # 同一公司的资本开支和 RPO 在不同线程里取，只下载一次
        if ticker not in _facts_cache:
            url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK[ticker]:010d}.json"
            _facts_cache[ticker] = get(url, headers=SEC_UA).json()
    return _facts_cache[ticker]


def _usd_rows(facts, concept):
    try:
        rows = facts["facts"]["us-gaap"][concept]["units"]["USD"]
    except KeyError:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df[df["form"].isin(["10-Q", "10-K", "10-Q/A", "10-K/A", "20-F", "6-K"])]
    df["end"] = pd.to_datetime(df["end"])
    if "start" in df:
        df["start"] = pd.to_datetime(df["start"])
    return df


def quarterly_from_cumulative(df):
    """现金流量表在10-Q里多是年初至今累计值：有单季（约3个月）的值直接用，
    其余按相同 start 的累计链（3→6→9→12个月）相邻差分出单季。"""
    df = df.dropna(subset=["start"]).copy()
    df["days"] = (df["end"] - df["start"]).dt.days
    df = df.sort_values("filed").drop_duplicates(["start", "end", "val", "filed"])
    out = {}
    for _, r in df[df["days"].between(80, 100)].iloc[::-1].iterrows():
        out.setdefault(r["end"], r["val"])  # 申报晚的（含重述）优先
    for _, g in df[df["days"] <= 380].groupby("start"):
        prev = None
        for end, v in g.groupby("end"):  # v：同一累计期的历次申报，按申报日排序
            # 链条起点可以是半年等非单季值，只在相邻两期相差约一个季度时出数
            if prev is not None and end not in out and 80 <= v["days"].iloc[0] - prev["days"].iloc[0] <= 100:
                # 有重述时两期要取同一版口径：前一期取最新值，后一期取该值首次出现之后的第一版
                # （10-K 会重述前两年全年数、10-Q 只重述上年同期，直接都取最新会混用新旧口径）
                a_val = prev["val"].iloc[-1]
                a_t = prev.loc[prev["val"] == a_val, "filed"].iloc[0]
                later = v[v["filed"] >= a_t]
                out[end] = (later["val"].iloc[0] if len(later) else v["val"].iloc[-1]) - a_val
            prev = v
    return pd.Series(out, dtype=float).sort_index()


def fetch_sec_capex(ticker, start):
    facts = sec_facts(ticker)
    # 公司会换科目（如亚马逊 2017 年前后用过三个），视为同一行项目合并；口径变化（亚马逊 2016 年及以前
    # 为扣除激励款的净额、之后为总额）由 quarterly_from_cumulative 按申报版本对齐处理
    frames = [df for df in (_usd_rows(facts, c) for c in CAPEX_CONCEPTS) if not df.empty]
    if not frames:
        raise ValueError("未找到资本开支科目")
    best = quarterly_from_cumulative(pd.concat(frames, ignore_index=True))
    return (best / 1e9)[best.index >= pd.Timestamp(start)]


RPO_CONCEPT = "RevenueRemainingPerformanceObligation"
RPO_TIMING_AXIS = "us-gaap:RevenueRemainingPerformanceObligationExpectedTimingOfSatisfactionStartDateAxis"


def sec_filings(ticker):
    """submissions 接口 recent 部分里的 10-Q/10-K（约最近 1000 份申报）。"""
    js = get(f"https://data.sec.gov/submissions/CIK{CIK[ticker]:010d}.json", headers=SEC_UA).json()
    r = js["filings"]["recent"]
    return [dict(report=pd.Timestamp(rd), acc=a, doc=d)
            for f, rd, a, d in zip(r["form"], r["reportDate"], r["accessionNumber"], r["primaryDocument"])
            if f in ("10-Q", "10-K") and rd]


def ixbrl_rpo(ticker, filing):
    """从一份 10-Q/10-K 的 iXBRL 原文取期末 RPO 总额。有的公司（如亚马逊 2020 年三季度起）只按
    “预计履约起始日”维度标注 RPO，companyfacts 接口不收带维度的数据，只能读原文。"""
    url = (f"https://www.sec.gov/Archives/edgar/data/{CIK[ticker]}/"
           f"{filing['acc'].replace('-', '')}/{filing['doc']}")
    doc = get(url, headers=SEC_UA).text
    day0 =filing["report"].strftime("%Y-%m-%d")
    day1 = (filing["report"] + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    ok = set()
    for cid, body in re.findall(r'<(?:xbrli:)?context id="([^"]+)">(.*?)</(?:xbrli:)?context>', doc, flags=re.S):
        if f">{day0}<" not in body or "instant>" not in body or "explicitMember" in body:
            continue
        typed = re.findall(r'typedMember dimension="([^"]+)">\s*<[^>]+>\s*([^<\s]+)', body)
        # 不带维度，或只带“从期末次日起履约”这一个维度 = 全部 RPO
        if all(d == RPO_TIMING_AXIS and v == day1 for d, v in typed):
            ok.add(cid)
    vals = []
    for attrs, txt in re.findall(r'<ix:nonFraction([^>]*name="us-gaap:' + RPO_CONCEPT + r'"[^>]*)>(.*?)</ix:nonFraction>',
                                 doc, flags=re.S):
        cref = re.search(r'contextRef="([^"]+)"', attrs)
        scale = re.search(r'scale="(-?\d+)"', attrs)
        try:
            v = float(htmllib.unescape(re.sub(r"<[^>]+>", "", txt)).replace(",", "").strip())
        except ValueError:
            continue
        if cref and cref.group(1) in ok:
            vals.append(v * 10 ** int(scale.group(1) if scale else 0) * (-1 if 'sign="-"' in attrs else 1))
    return max(vals) if vals else None


def fetch_sec_rpo(ticker, start):
    df = _usd_rows(sec_facts(ticker), RPO_CONCEPT)
    s = pd.Series(dtype=float)
    if not df.empty:
        df = df.sort_values("filed").drop_duplicates("end", keep="last")
        s = pd.Series(df["val"].values / 1e9, index=df["end"]).sort_index()
    # companyfacts 断更时，逐份读之后的财报原文补齐
    last = s.index.max() if len(s) else pd.Timestamp(start)
    todo = [f for f in sec_filings(ticker) if f["report"] > last and f["report"] >= pd.Timestamp(start)]
    with ThreadPoolExecutor(4) as ex:  # 原文每份数 MB，并行下载；总频率仍受 SEC 限速约束
        vals = list(ex.map(lambda f: ixbrl_rpo(ticker, f), todo))
    extra = {f["report"]: v / 1e9 for f, v in zip(todo, vals) if v is not None}
    if extra:
        s = pd.concat([s, pd.Series(extra)]).sort_index()
    if s.empty:
        raise ValueError("未找到 RPO 科目")
    return s[s.index >= pd.Timestamp(start)]


def fetch_cftc(code, start):
    url = ("https://publicreporting.cftc.gov/resource/72hh-3qpy.json"
           f"?cftc_contract_market_code={code}&$limit=50000"
           f"&$where=report_date_as_yyyy_mm_dd>='{start}T00:00:00'"
           "&$select=report_date_as_yyyy_mm_dd,m_money_positions_long_all,m_money_positions_short_all")
    df = pd.DataFrame(get(url).json())
    df["d"] = pd.to_datetime(df["report_date_as_yyyy_mm_dd"])
    net = pd.to_numeric(df["m_money_positions_long_all"]) - pd.to_numeric(df["m_money_positions_short_all"])
    return pd.Series(net.values, index=df["d"]).sort_index()


def fetch_nyfed_srf(_, start):
    start = max(pd.Timestamp(start), pd.Timestamp("2021-07-28"))
    out = {}
    cur = start
    while cur <= pd.Timestamp.today():
        end = min(cur + pd.DateOffset(years=1), pd.Timestamp.today())
        url = (f"https://markets.newyorkfed.org/api/rp/results/search.json"
               f"?startDate={cur:%Y-%m-%d}&endDate={end:%Y-%m-%d}&operationTypes=repo")
        ops = get(url).json().get("repo", {}).get("operations", [])
        for op in ops:
            d = pd.Timestamp(op["operationDate"])
            out[d] = out.get(d, 0.0) + float(op.get("totalAmtAccepted", 0) or 0) / 1e9
        cur = end + pd.Timedelta(days=1)
    return pd.Series(out).sort_index()


def fetch_lw_rstar(_, start):
    url = ("https://www.newyorkfed.org/medialibrary/media/research/economists/williams/data/"
           "Laubach_Williams_current_estimates.xlsx")
    raw = pd.read_excel(io.BytesIO(get(url).content), sheet_name=None, header=None)
    # 找到含 "rstar" / "r*" 的表头行
    for _, df in raw.items():
        for i in range(min(15, len(df))):
            row = [str(x).lower() for x in df.iloc[i].tolist()]
            col = next((j for j, x in enumerate(row) if x.strip() in ("rstar", "r*", "r* (one-sided)") or "rstar" in x), None)
            if col is not None:
                body = df.iloc[i + 1:, [0, col]].dropna()
                d = pd.to_datetime(body.iloc[:, 0], errors="coerce")
                v = pd.to_numeric(body.iloc[:, 1], errors="coerce")
                s = pd.Series(v.values, index=d).dropna()
                s = s[s.index.notna()]
                if len(s):
                    return s[s.index >= pd.Timestamp(start)]
    raise ValueError("未能解析 LW r* 表格")


def fetch_eia(code, start):
    url = f"https://www.eia.gov/dnav/pet/hist_xls/{code}w.xls"
    df = pd.read_excel(io.BytesIO(get(url).content), sheet_name="Data 1", skiprows=2)
    s = pd.Series(pd.to_numeric(df.iloc[:, 1], errors="coerce").values, index=pd.to_datetime(df.iloc[:, 0]))
    return s[s.index >= pd.Timestamp(start)]


AUCTION_TENORS = (2, 3, 5, 7, 10, 20, 30)


def fetch_auctions(_, start):
    """返回待入库的行（在线程里运行，不碰数据库），由主线程 save_auctions 写入。"""
    base = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query"
    fields = ("cusip,auction_date,security_type,security_term,original_security_term,inflation_index_security,"
              "floating_rate,offering_amt,high_yield,bid_to_cover_ratio,"
              "indirect_bidder_accepted,direct_bidder_accepted,primary_dealer_accepted,comp_accepted")
    page, out = 1, []
    while True:
        url = (f"{base}?fields={fields}&filter=auction_date:gte:{start},security_type:in:(Note,Bond)"
               f"&sort=-auction_date&page[size]=1000&page[number]={page}")
        js = get(url).json()
        data = js.get("data", [])
        if not data:
            break

        def f(x):
            try:
                return float(x)
            except (TypeError, ValueError):
                return None

        def term(d):
            # 按本次发行的剩余期限归入最近的标准期限：续发（如 9-Year 10-Month）归入 10-Year；
            # 不能直接用 original_security_term——2年/5年期拍卖有时续发旧的7年期券（原始期限记为 7-Year）。
            # TIPS、FRN 单独标注，不混进名义附息国债
            m = re.fullmatch(r"(?:(\d+)-Year)?\s*(?:(\d+)-Month)?", (d["security_term"] or "").strip())
            yrs = int(m.group(1) or 0) + int(m.group(2) or 0) / 12 if m else 0
            t = f"{min(AUCTION_TENORS, key=lambda x: abs(x - yrs))}-Year" if yrs else d["security_term"]
            if d.get("inflation_index_security") == "Yes":
                t += " TIPS"
            if d.get("floating_rate") == "Yes":
                t += " FRN"
            return t
        rows = [(d["cusip"], d["auction_date"], d["security_type"], term(d), f(d["offering_amt"]),
                 f(d["high_yield"]), f(d["bid_to_cover_ratio"]), f(d["indirect_bidder_accepted"]),
                 f(d["direct_bidder_accepted"]), f(d["primary_dealer_accepted"]), f(d["comp_accepted"]))
                for d in data]
        out += rows
        if page >= js.get("meta", {}).get("total-pages", 1):
            break
        page += 1
    return out


def save_auctions(con, rows, meta):
    con.executemany("INSERT OR REPLACE INTO auctions VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.execute("INSERT OR REPLACE INTO series VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("AUCTIONS", meta["name"], "", "E", "auctions", "fiscaldata",
                 datetime.now().isoformat(timespec="seconds"),
                 con.execute("SELECT MAX(auction_date) FROM auctions").fetchone()[0],
                 con.execute("SELECT COUNT(*) FROM auctions").fetchone()[0], "ok"))
    con.commit()
    return len(rows)


FETCHERS = {"fred": fetch_fred, "yahoo": fetch_yahoo, "sec_capex": fetch_sec_capex, "sec_rpo": fetch_sec_rpo,
            "cftc": fetch_cftc, "nyfed_srf": fetch_nyfed_srf, "lw_rstar": fetch_lw_rstar, "eia": fetch_eia,
            "auctions": fetch_auctions}


# ---------------------------------------------------------------------------
# 派生序列
# ---------------------------------------------------------------------------
def yoy(s):
    """同比：按日期对齐到一年前的同一期，而不是按行数平移——序列缺月时（如 2025 年 10 月 CPI
    因政府停摆没有发布）按位置平移会变成与 13 个月前比较。"""
    if s.empty:
        return s
    prev = pd.Series(s.values, index=s.index + pd.DateOffset(years=1))
    prev = prev[~prev.index.duplicated()]
    return ((s / prev.reindex(s.index) - 1) * 100).dropna()


def ratio(a, b):
    df = pd.concat([a, b], axis=1, join="inner").dropna()
    return df.iloc[:, 0] / df.iloc[:, 1]


def diff(a, b, scale=1.0):
    df = pd.concat([a, b], axis=1, join="inner").dropna()
    return (df.iloc[:, 0] - df.iloc[:, 1]) * scale


def derive(con):
    L = lambda sid: load(con, sid)  # noqa: E731
    out = {}
    out["X_RSP_SPY"] = ratio(L("RSP"), L("SPY"))
    out["X_ICSA_4W"] = L("ICSA").rolling(4).mean()
    out["X_PHL_NO_INV"] = diff(L("PHL_NO"), L("PHL_INV"))
    out["X_DSPIC96_YOY"] = yoy(L("DSPIC96"))
    out["X_RSAFS_YOY"] = yoy(L("RSAFS"))
    out["X_CPI_YOY"] = yoy(L("CPIAUCSL"))
    out["X_CORECPI_YOY"] = yoy(L("CPILFESL"))
    out["X_PPI_YOY"] = yoy(L("PPIFIS"))
    core_pce_yoy = yoy(L("PCEPILFE"))
    out["X_COREPCE_YOY"] = core_pce_yoy
    pce = L("PCE")
    pce_q = pce.resample("QS").mean()[pce.resample("QS").count() == 3]  # 季度汽油支出 ÷ 同季度PCE均值
    out["X_GAS_SHARE"] = ratio(L("PCE_GAS"), pce_q) * 100
    out["X_ENERGY_SHARE"] = ratio(L("PCE_ENERGY"), pce) * 100
    out["X_2S10S"] = diff(L("DGS10"), L("DGS2"), 100)
    dff_m = L("DFF").resample("MS").mean()
    real_ff = diff(dff_m, core_pce_yoy)
    out["X_REAL_FF"] = real_ff
    rstar = L("RSTAR")
    if not rstar.empty and not real_ff.empty:
        rq = rstar.copy()
        rq.index = rq.index.to_period("Q").to_timestamp()
        rstar_m = rq.resample("MS").ffill().reindex(real_ff.index, method="ffill")
        out["X_REAL_GAP"] = (real_ff - rstar_m).dropna()
    walcl = L("WALCL") / 1000  # 百万 -> 十亿
    tga = L("WTREGEN")
    rrp = L("RRPONTSYD")
    if not walcl.empty:
        idx = walcl.index
        net = walcl - tga.reindex(idx, method="ffill") - rrp.reindex(idx, method="ffill").fillna(0)
        out["X_NETLIQ"] = net
    iorb = pd.concat([L("IOER"), L("IORB")]).sort_index()
    iorb = iorb[~iorb.index.duplicated(keep="last")]
    sofr, sofr99 = L("SOFR"), L("SOFR99")
    if not iorb.empty:
        out["X_SOFR_IORB"] = diff(sofr, iorb.reindex(sofr.index, method="ffill"), 100)
        out["X_SOFR99_IORB"] = diff(sofr99, iorb.reindex(sofr99.index, method="ffill"), 100)
    r = pd.concat([L("SPY").pct_change(), L("IEF").pct_change()], axis=1, join="inner").dropna()
    out["X_CORR_SPY_IEF"] = r.iloc[:, 0].rolling(63).corr(r.iloc[:, 1])
    out["X_CCC_BB"] = diff(L("CCC_OAS"), L("BB_OAS"))
    capex = pd.concat([L(f"CAPEX_{t}") for t in ["MSFT", "GOOGL", "AMZN", "META", "ORCL", "CRWV"]], axis=1, sort=True)
    if not capex.empty:
        # 不同财年的季末日期略有差异，按日历季度对齐
        capex.index = capex.index.to_period("Q").to_timestamp("Q")
        capex = capex.groupby(level=0).sum(min_count=1)
        core = capex.iloc[:, :4].dropna()  # 四大云厂商齐全的季度
        total = capex.loc[core.index].sum(axis=1)
        out["X_CAPEX_TOTAL"] = total
        out["X_CAPEX_TOTAL_YOY"] = yoy(total)
    info, sw, fpi, gdp = L("INFO_EQ"), L("SOFTWARE"), L("FPI"), L("GDP")
    if not info.empty and not sw.empty:
        ai = (info + sw).dropna()
        out["X_INFO_SW_GDP"] = (ai / gdp * 100).dropna()
        ex = (fpi - ai).dropna()
        out["X_FPI_EX_AI"] = ex
        out["X_FPI_EX_AI_YOY"] = yoy(ex)
    out["X_NEWORDER_YOY"] = yoy(L("NEWORDER"))
    out["X_ANXAVS_YOY"] = yoy(L("ANXAVS"))
    gold = L("GOLD")
    out["X_GOLD_SILVER"] = ratio(gold, L("SILVER"))
    out["X_GOLD_OIL"] = ratio(gold, L("WTI")[L("WTI") > 5])
    out["X_GOLD_TLT"] = ratio(gold, L("TLT"))
    g = pd.concat([np.log(gold), L("DFII10")], axis=1, join="inner").dropna()
    g.columns = ["lg", "r"]
    if len(g) > 800:
        w = 756
        cov = g["lg"].rolling(w).cov(g["r"])
        beta = cov / g["r"].rolling(w).var()
        alpha = g["lg"].rolling(w).mean() - beta * g["r"].rolling(w).mean()
        out["X_GOLD_RESID"] = (g["lg"] - (alpha + beta * g["r"])).dropna()
        out["X_GOLD_CORR"] = g["lg"].diff().rolling(126).corr(g["r"].diff()).dropna()
    for sid, s in out.items():
        if s is None or len(s.dropna()) == 0:
            continue
        name, unit = DERIVED[sid]
        con.execute("DELETE FROM observations WHERE series_id=?", (sid,))
        save_series(con, sid, s.replace([np.inf, -np.inf], np.nan), dict(name=name, unit=unit, source="derived", code=sid))


# ---------------------------------------------------------------------------
# 标普500 宽度（可选）
# ---------------------------------------------------------------------------
def compute_breadth(con, start, workers=16):
    html = get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies").text
    tickers = pd.read_html(io.StringIO(html))[0]["Symbol"].str.replace(".", "-", regex=False).tolist()
    above, valid = None, None
    fetch_from = (pd.Timestamp(start) - pd.DateOffset(days=320)).strftime("%Y-%m-%d")

    def prices(t):
        try:
            return fetch_yahoo(t, fetch_from).dropna()  # 个别交易日收盘为空会让200日均线断档200天
        except Exception:  # noqa: BLE001
            return None

    n = 0
    with ThreadPoolExecutor(workers) as ex:  # 并行下载，汇总在主线程
        for px in ex.map(prices, tickers):
            if px is None or px.empty:
                continue
            n += 1
            ma = px.rolling(200).mean()
            a = (px > ma).astype(float).where(ma.notna())
            above = a if above is None else above.add(a, fill_value=0)
            v = ma.notna().astype(float)
            valid = v if valid is None else valid.add(v, fill_value=0)
    print(f"  宽度：取到 {n}/{len(tickers)} 只成分股", flush=True)
    s = (above / valid * 100)[valid > 100]
    name, unit = DERIVED["X_BREADTH_200"]
    save_series(con, "X_BREADTH_200", s[s.index >= pd.Timestamp(start)], dict(name=name, unit=unit, source="derived", code="breadth"))


# ---------------------------------------------------------------------------
# moomoo OpenD 选股器宽度（可选：本机开着 OpenD 才取，每次运行记一行）
# ---------------------------------------------------------------------------
OPEND = ("127.0.0.1", 11111)
MOOMOO_BREADTH = [("X_BREADTH_MOOMOO_SPX", "US..SPX"), ("X_BREADTH_MOOMOO_US", None)]  # 板块代码 None = 全美股


def moomoo_breadth(con):
    try:
        socket.create_connection(OPEND, timeout=1).close()
    except OSError:
        print(f"· 未连上 moomoo OpenD（{OPEND[0]}:{OPEND[1]}），跳过选股器宽度", flush=True)
        return
    try:
        from moomoo import (KLType, Market, OpenQuoteContext, RelativePosition, RET_OK, CustomIndicatorFilter,
                            StockField)
    except ImportError:
        print("· 未安装 moomoo-api（.venv/bin/pip install moomoo-api），跳过选股器宽度", flush=True)
        return
    logging.getLogger("FTConsoleLog").setLevel(logging.WARNING)  # SDK 默认把连接日志打到控制台

    def price_vs_ma200(pos):
        f = CustomIndicatorFilter()
        f.stock_field1, f.stock_field2, f.stock_field2_para = StockField.PRICE, StockField.MA, [200]
        f.relative_position, f.ktype, f.is_no_filter = pos, KLType.K_DAY, False
        return f

    # 选股器反映的交易日：取刚更新过的 SPY 最后一天（美股盘中运行即当天）
    d = pd.Timestamp(last_date(con, "SPY") or date.today())
    ctx = OpenQuoteContext(host=OPEND[0], port=OPEND[1])
    try:
        for sid, plate in MOOMOO_BREADTH:
            cnt = []
            for pos in (RelativePosition.MORE, RelativePosition.LESS):
                ret, data = ctx.get_stock_filter(market=Market.US, filter_list=[price_vs_ma200(pos)], plate_code=plate,
                                                 begin=0, num=1)
                if ret != RET_OK:
                    raise RuntimeError(data)
                cnt.append(data[1])  # all_count：满足条件的总数，不必翻页
            # 分母 = 站上 + 低于 200 日均线（即有满 200 日数据的股票），不含上市不足 200 天的
            pct = cnt[0] / sum(cnt) * 100
            name, unit = DERIVED[sid]
            save_series(con, sid, pd.Series([pct], index=[d]), dict(name=name, unit=unit, source="moomoo", code=plate or "US"))
            print(f"✓ {sid:<14} {name}  {pct:.1f}%（{cnt[0]}/{sum(cnt)}，{d:%Y-%m-%d}）", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"✗ moomoo 选股器宽度 {e!r}", flush=True)
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# 手工数据
# ---------------------------------------------------------------------------
def load_manual(con):
    if not MANUAL.exists():
        return 0
    df = pd.read_csv(MANUAL, comment="#", dtype=str).fillna("")
    con.execute("DELETE FROM manual")
    rows = []
    for _, r in df.iterrows():
        if not r.get("ref") or not r.get("date"):
            continue
        try:
            v = float(r["value"]) if r.get("value", "") != "" else None
        except ValueError:
            v = None
        rows.append((r["ref"].strip(), r["date"].strip(), r.get("item", "").strip(), v,
                     r.get("text", "").strip(), r.get("source", "").strip()))
    con.executemany("INSERT OR REPLACE INTO manual VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    return len(rows)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--breadth", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--workers", type=int, default=8, help="并行下载线程数")
    ap.add_argument("-v", "--verbose", action="store_true", help="失败时打印完整 traceback")
    args = ap.parse_args()
    con = connect()
    only = [x for x in args.only.split(",") if x]
    ok, fail = 0, []
    t0 = time.time()
    jobs = {}
    with ThreadPoolExecutor(args.workers) as ex:
        for sid, meta in SERIES.items():
            if only and sid not in only:
                continue
            if meta["source"] == "auctions":
                ld = con.execute("SELECT MAX(auction_date) FROM auctions").fetchone()[0]
                st = START if args.full or not ld else (pd.Timestamp(ld) - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
            else:
                ld = None if args.full else last_date(con, sid)
                # 增量：回看一段时间以吸收修订
                st = START if not ld else (pd.Timestamp(ld) - pd.DateOffset(days=400)).strftime("%Y-%m-%d")
            jobs[ex.submit(FETCHERS[meta["source"]], meta["code"], st)] = sid
        # 下载在线程池里并行，入库只在主线程（sqlite 连接不跨线程共享）
        for fut in as_completed(jobs):
            sid = jobs[fut]
            meta = SERIES[sid]
            try:
                res = fut.result()
                if meta["source"] == "auctions":
                    n = save_auctions(con, res, meta)
                else:
                    n = save_series(con, sid, res * meta["scale"] if meta.get("scale") else res, meta)
                ok += 1
                print(f"✓ {sid:<14} {meta['name']}  +{n}", flush=True)
            except Exception as e:  # noqa: BLE001
                fail.append(sid)
                mark_failed(con, sid, meta, repr(e))
                print(f"✗ {sid:<14} {meta['name']}  {e!r}", flush=True)
                if args.verbose:
                    traceback.print_exception(e)
    print(f"  下载入库用时 {time.time() - t0:.0f} 秒", flush=True)
    if args.breadth:
        try:
            t1 = time.time()
            compute_breadth(con, START)
            print(f"✓ X_BREADTH_200（{time.time() - t1:.0f} 秒）")
        except Exception as e:  # noqa: BLE001
            print(f"✗ X_BREADTH_200 {e!r}")
    if not only or any(s.startswith("X_BREADTH_MOOMOO") for s in only):
        moomoo_breadth(con)
    derive(con)
    m = load_manual(con)
    print(f"\n完成：成功 {ok}，失败 {len(fail)} {fail}；手工记录 {m} 条；数据库 {DB}")


if __name__ == "__main__":
    main()
