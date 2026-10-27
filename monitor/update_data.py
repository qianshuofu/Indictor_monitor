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
import json
import logging
import os
import re
import socket
import sqlite3
import sys
import threading
import time
import traceback
import warnings
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from indicators import DERIVED, SERIES, START, THESES  # noqa: E402

warnings.filterwarnings("ignore", message="The xlrd engine is deprecated")  # EIA、外汇局仍发 .xls
logging.getLogger("pypdf").setLevel(logging.ERROR)  # FactSet 旧 PDF 的字体提示，不影响取数

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "macro.db"
MANUAL = ROOT / "manual_data.csv"
# 只有“当天值”、事后无法回补的序列（moomoo 选股器、DRAM 现货、GPU 租价）另存一份文本，删库重建也不丢
SNAPSHOTS = ROOT / "data" / "snapshots.csv"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh) indicator-monitor research contact@example.com"}


def local_setting(key, default=""):
    """本机设置：先读环境变量，再读项目根目录的 .env（KEY=VALUE 每行一条；.env 不进 git）。"""
    if os.environ.get(key):
        return os.environ[key]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() == key:
                return v.strip().strip("\"'")
    return default


# SEC EDGAR 要求请求头带真实联系邮箱；邮箱写在 .env 的 SEC_CONTACT_EMAIL，不随仓库公开
SEC_UA = {"User-Agent": f"indicator-monitor research {local_setting('SEC_CONTACT_EMAIL', 'contact@example.com')}"}
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
LIMITS = {"data.sec.gov": _SEC_LIMIT, "www.sec.gov": _SEC_LIMIT, "query1.finance.yahoo.com": RateLimiter(12),
          # 逐页/逐月抓取的来源放慢些
          "mopsov.twse.com.tw": RateLimiter(4), "www.prnewswire.com": RateLimiter(4),
          "advantage.factset.com": RateLimiter(4), "comtradeapi.un.org": RateLimiter(1)}


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
CREATE TABLE IF NOT EXISTS events (
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


def save_events(con, rows):
    """自动抓取的事件（如 FOMC 异议者及其主张），与 manual 表同结构，看板里和手工记录合并展示。"""
    con.executemany("INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?)", rows)
    con.commit()


def record_snapshot(rows):
    """rows: [(series_id, 日期, 数值, 来源)]，同一序列同一天只留最新一条。"""
    cols = ["series_id", "date", "value", "source"]
    old = pd.read_csv(SNAPSHOTS, dtype=str) if SNAPSHOTS.exists() else pd.DataFrame(columns=cols)
    new = pd.DataFrame([(sid, pd.Timestamp(d).strftime("%Y-%m-%d"), v, src) for sid, d, v, src in rows], columns=cols)
    df = pd.concat([old, new.astype(str)]).drop_duplicates(["series_id", "date"], keep="last")
    df.sort_values(["series_id", "date"]).to_csv(SNAPSHOTS, index=False)


def restore_snapshots(con):
    if not SNAPSHOTS.exists():
        return
    df = pd.read_csv(SNAPSHOTS, dtype={"series_id": str, "date": str, "source": str})
    for sid, g in df.groupby("series_id"):
        meta = SERIES.get(sid) or dict(zip(("name", "unit"), DERIVED.get(sid, (sid, ""))), source=g["source"].iloc[-1],
                                       code=sid)
        save_series(con, sid, pd.Series(g["value"].astype(float).values, index=pd.to_datetime(g["date"])), meta)


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


def get(url, headers=None, tries=3, method="GET", ok404=False, **kw):
    """带限速与重试的请求。ok404=True 时 404 直接返回 None（按日期试探文件名的来源用）。"""
    lim = LIMITS.get(urlparse(url).hostname)
    for i in range(tries):
        try:
            if lim:
                lim.wait()
            r = SESSION.request(method, url, headers=headers, timeout=60, **kw)
            if r.status_code == 429:
                time.sleep(5 * (i + 1))
                continue
            if ok404 and r.status_code == 404:
                return None
            r.raise_for_status()
            if "charset" not in r.headers.get("content-type", "").lower():
                r.encoding = "utf-8"  # 不写字符集时 requests 按 ISO-8859-1 解码，美联储、外汇局等网页实为 UTF-8
            return r
        except requests.RequestException:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))
    raise requests.HTTPError(f"429 Too Many Requests: {url}")


_cache, _cache_lock, _key_locks = {}, threading.Lock(), {}


def cached(key, fn):
    """同一来源的多条序列在不同线程里取时只下载、解析一次。"""
    with _cache_lock:
        lk = _key_locks.setdefault(key, threading.Lock())
    with lk:
        if key not in _cache:
            _cache[key] = fn()
    return _cache[key]


def since(s, start):
    """截取 start 之后的部分；空序列（RangeIndex）原样返回。"""
    return s[s.index >= pd.Timestamp(start)] if len(s) else s


def plain(html_text):
    """网页正文转为单行纯文本，便于正则提取。"""
    t = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", html_text)
    t = htmllib.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", re.sub("[\u2010\u2011]", "-", t))  # 美联储声明里“1-1/2”常用不换行连字符


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
def sec_facts(ticker):
    # 同一公司的资本开支和 RPO 在不同线程里取，只下载一次
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK[ticker]:010d}.json"
    return cached(("sec", ticker), lambda: get(url, headers=SEC_UA).json())


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


# ---------------------------------------------------------------------------
# 原先列为“手工录入”、后来找到稳定免费来源的指标
# ---------------------------------------------------------------------------
def fetch_umich(code, start):
    """密歇根大学消费者调查官网表格：PX_MD=1年、PX5_MD=5–10年通胀预期中位数（月度，1978 年起）。"""
    df = cached("umich", lambda: pd.read_csv(io.StringIO(get("https://www.sca.isr.umich.edu/files/tbmpx1px5.csv").text)))
    d = pd.to_datetime(df["Month"].str.strip() + " " + df["YYYY"].astype(str), format="%B %Y")
    s = pd.Series(pd.to_numeric(df[code], errors="coerce").values, index=d)
    return s[s.index >= pd.Timestamp(start)]


SCE_URL = "https://www.newyorkfed.org/medialibrary/interactives/sce/sce/downloads/data/frbny-sce-data.xlsx"


def fetch_sce(code, start):
    """纽约联储消费者预期调查 SCE：code=one/three/five，即 1/3/5 年通胀预期中位数（月度，2013 年起）。"""
    df = cached("sce", lambda: pd.read_excel(io.BytesIO(get(SCE_URL).content), sheet_name="Inflation expectations",
                                             header=None))
    head = next(i for i in range(15) if any(str(x).startswith("Median one-year") for x in df.iloc[i]))
    col = [str(x).strip() for x in df.iloc[head]].index(f"Median {code}-year ahead expected inflation rate")
    body = df.iloc[head + 1:, [0, col]].dropna()
    d = pd.to_datetime(body.iloc[:, 0].astype(int).astype(str), format="%Y%m")
    s = pd.Series(pd.to_numeric(body.iloc[:, 1], errors="coerce").values, index=d)
    return s[s.index >= pd.Timestamp(start)]


def hhdc_book():
    # 文件名随季度变化（HHD_C_Report_2026Q2.xlsx）：从当前季度往前试，取最新一期
    q = pd.Timestamp.today().to_period("Q")
    for k in range(6):
        p = q - k
        url = ("https://www.newyorkfed.org/medialibrary/interactives/householdcredit/data/xls/"
               f"HHD_C_Report_{p.year}Q{p.quarter}.xlsx")
        try:
            r = get(url, tries=1, ok404=True)
        except requests.RequestException:
            continue
        if r is not None and r.content[:2] == b"PK":  # xlsx 是 zip 包；不存在时可能返回 HTML 页
            return pd.read_excel(io.BytesIO(r.content), sheet_name=None, header=None)
    raise ValueError("未找到最近 6 个季度的纽约联储家庭债务报告")


def fetch_hhdc(code, start):
    """纽约联储《家庭债务与信贷报告》：当季新进入严重拖欠（90天+）的余额占比，code=AUTO/CC 等（季度，2003 年起）。"""
    book = cached("hhdc", hhdc_book)
    df = next(d for d in book.values() if str(d.iat[0, 0]).startswith("New Seriously Delinquent"))
    head = next(i for i in range(10) if code in [str(x).strip() for x in df.iloc[i]])
    col = [str(x).strip() for x in df.iloc[head]].index(code)
    body = df.iloc[head + 1:, [0, col]].dropna()
    body = body[body.iloc[:, 0].astype(str).str.fullmatch(r"\d\d:Q\d")]  # 季度标签如 "03:Q1"
    idx = pd.PeriodIndex([f"20{x[:2]}Q{x[-1]}" for x in body.iloc[:, 0]], freq="Q").to_timestamp()
    s = pd.Series(pd.to_numeric(body.iloc[:, 1], errors="coerce").values, index=idx)
    return s[s.index >= pd.Timestamp(start)]


FACTSET_PDF = ("https://advantage.factset.com/hubfs/Website/Resources%20Section/Research%20Desk/"
               "Earnings%20Insight/EarningsInsight_{:%m%d%y}.pdf")
FACTSET_PE = re.compile(r"forward\s*12\s*-\s*month\s*P/E\s*ratio\s*for\s*the\s*S&P\s*500\s*is\s*(\d+\.\d+)")


def factset_week(friday):
    """一期 FactSet《Earnings Insight》：周五发布，节假日提前到周四/周三。返回 (发布日, {pe, avg5, avg10})。"""
    from pypdf import PdfReader  # 只有这一个来源用到，没装时只影响它
    for k in range(3):
        day = friday - pd.Timedelta(days=k)
        r = get(FACTSET_PDF.format(day), ok404=True)
        if r is None:
            continue
        txt = re.sub(r"\s+", " ", " ".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(r.content)).pages[:3]))
        m = FACTSET_PE.search(txt)
        if not m:
            return day, {}
        out = {"pe": float(m.group(1))}
        tail = txt[m.end():m.end() + 300]  # 紧接着一句给出 5 年、10 年均值
        for key, n in (("avg5", 5), ("avg10", 10)):
            mm = re.search(rf"{n}\s*-\s*year\s*average\s*\(?\s*(?:of\s*)?(\d+\.\d+)", tail)
            if mm:
                out[key] = float(mm.group(1))
        return day, out
    return None, {}


def factset_table(start):
    today = pd.Timestamp.today().normalize()
    # 该路径下的 PDF 从 2017 年起才有
    fridays = pd.date_range(max(pd.Timestamp(start), pd.Timestamp("2017-01-01")), today, freq="W-FRI")
    # 每份 PDF 1–4 MB：一年以前每月只取第一期，近一年逐周
    old = today - pd.DateOffset(years=1)
    dates = [d for d in fridays if d >= old or d.day <= 7]
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(factset_week, dates))
    return pd.DataFrame({d: v for d, v in res if d is not None and v}).T.sort_index()


def fetch_factset(code, start):
    """FactSet 免费周报 PDF：标普500 远期12个月市盈率及其 5 年、10 年均值（2017 年起）。"""
    df = cached(("factset", start), lambda: factset_table(start))
    return df[code].dropna() if code in df else pd.Series(dtype=float)


def fetch_mops(code, start):
    """证交所公开资讯观测站“上市公司营业收入汇总表”：每月一页（上月营收在次月 10 日前公布），千元新台币→亿元。"""
    months = pd.period_range(pd.Timestamp(start), pd.Timestamp.today(), freq="M")

    def one(p):
        r = get(f"https://mopsov.twse.com.tw/nas/t21/sii/t21sc03_{p.year - 1911}_{p.month}_0.html", ok404=True)
        if r is None:
            return None
        m = re.search(rf"(?i)<td[^>]*>\s*{code}\s*</td>\s*<td[^>]*>[^<]*</td>\s*<td[^>]*>\s*([\d,]+)\s*</td>",
                      r.content.decode("cp950", errors="replace"))
        return float(m.group(1).replace(",", "")) / 1e5 if m else None

    with ThreadPoolExecutor(4) as ex:
        vals = list(ex.map(one, months))
    return pd.Series({p.to_timestamp(): v for p, v in zip(months, vals) if v is not None}, dtype=float)


FED = "https://www.federalreserve.gov"


def frac(s):
    """'3-3/4' -> 3.75，'1/4' -> 0.25，'1' -> 1.0"""
    return sum(float(a) / float(b) if b else float(a) for a, _, b in (x.partition("/") for x in s.split("-")))


NUM = r"\d+(?:\.\d+)?(?:-\d+/\d+|/\d+)?"


def rate_view(snip):
    """一段话里的利率主张：(方向 +1/0/−1, 幅度, 目标区间上限)；看不出方向时返回 None。"""
    if re.search(r"no change|maintain|leave|unchanged|keep", snip):
        sign = 0
    elif re.search(r"\b(lower|reduc|cut)", snip):
        sign = -1
    elif re.search(r"\b(raise|increas)", snip):
        sign = 1
    else:
        return None
    by = re.search(rf"by ({NUM}) percentage point|by (\d+) basis points", snip)
    mag = (frac(by.group(1)) if by.group(1) else int(by.group(2)) / 100) if by else None
    rng = re.search(rf"to ({NUM})(?: percent)? to ({NUM}) percent", snip)
    return sign, mag, frac(rng.group(2)) if rng else None


def dissent_label(act, reason):
    """异议者主张 vs 实际决议：只看“preferred ...”从句；主张比决议宽松为鸽、更紧为鹰。分不清时返回空串。"""
    m = re.search(r"prefer(?:red|s)? (?:at this meeting )?(.{0,150})", reason)
    want = rate_view(m.group(1)) if m else None
    if not want:
        return ""
    act = act or (0, None, None)  # 早年声明没有“decided to”，基本都是维持零利率
    if act[2] is not None and want[2] is not None:  # 两边都写了目标区间，直接比上限
        diff = want[2] - act[2]
    else:
        level = lambda v: v[0] * (v[1] if v[1] is not None else 0.25)  # noqa: E731
        diff = level(want) - level(act)
    if abs(diff) < 1e-9:  # 利率方向与决议相同，异议在别处（前瞻指引、缩表节奏等）
        return ""
    what = "维持" if want[0] == 0 else ("加息" if want[0] > 0 else "降息") + (f"{want[1] * 100:.0f}bp" if want[1] else "")
    return f"主张{what}（{'鸽' if diff < 0 else '鹰'}）"


def fomc_statement(url):
    """一份议息声明的投票：返回 (异议票数, 决议说明, [(异议者, 主张)])；不是议息声明时返回 None。"""
    txt = plain(get(url).text)
    # 决议只看联邦基金利率目标区间那一句（“decided to reduce the pace of asset purchases”之类不算）
    m = re.search(r"decided (?:today )?to ([^.]{0,40}target range for the federal funds rate[^.]{0,80})", txt)
    act = rate_view(m.group(1)) if m else \
        (0, None, None) if re.search(r"(?:will maintain|reaffirmed its view)[^.]{0,80}target range", txt) else None
    action = "" if not act else "维持利率" if act[0] == 0 else \
        f"{'加' if act[0] > 0 else '降'}息{act[1] * 100:.0f}bp" if act[1] else \
        f"{'加' if act[0] > 0 else '降'}息（上限至 {act[2]:g}%）" if act[2] is not None else \
        f"{'加' if act[0] > 0 else '降'}息"
    m = re.search(r"Voting against (?:the |this )?(?:[a-z]+ ){0,2}?(?:action|policy)\s+(?:was|were):?\s*(.*?)"
                  r"(?= For media inquiries| Implementation Note| Last Update| Statement Regarding| Related Information|$)",
                  txt)
    if not m:
        tally = re.search(r"by a (\d+)\s*[–-]\s*(\d+) vote", txt)  # 2026 年起有的声明只写票数、不列异议者
        if tally:
            return int(tally.group(2)), action, []
        return (0, action, []) if "Voting for" in txt else None
    # 按逗号、分号、and、句号切开：形如人名的片段即一位异议者，其后的 who ... 是理由
    out, cur = [], None
    for p in re.split(r",\s*(?:and\s+)?|;\s*(?:and\s+)?|\s+and\s+(?=[A-Z])|(?<=[a-z]{2})\.\s+(?=[A-Z])", m.group(1)):
        p = p.strip()
        if re.fullmatch(r"[A-Z][a-z]+(?: [A-Z]\.)?(?: [A-Z][a-zA-Z'\-]+){1,2}\.?", p):
            cur = [p.rstrip("."), ""]
            out.append(cur)
        elif cur is not None:
            cur[1] += (" " if cur[1] else "") + p
    for i in range(len(out) - 2, -1, -1):  # “A and B, who preferred ...”：理由写在最后一人后面，前面的人共用
        out[i][1] = out[i][1] or out[i + 1][1]
    return len(out), action, [(n, dissent_label(act, r) or r[:140]) for n, r in out]


def fetch_fomc(_, start):
    """美联储议息声明（2010 年起）：每次会议的异议票数；异议者及其主张另存为事件，看板表格展示。"""
    start = pd.Timestamp(start)
    pages = [get(f"{FED}/monetarypolicy/fomccalendars.htm").text]
    have = {int(y) for y in re.findall(r"monetary(\d{4})\d{4}a\.htm", pages[0])}
    for y in range(start.year, pd.Timestamp.today().year + 1):
        if y not in have:  # 近五年在日历页，更早的在逐年的历史页
            r = get(f"{FED}/monetarypolicy/fomchistorical{y}.htm", ok404=True)
            if r is not None:
                pages.append(r.text)
    dates = sorted({d for p in pages for d in re.findall(r"/newsevents/press(?:releases)?/monetary/?(\d{8})a\.htm", p)
                    if pd.Timestamp(d) >= start})
    urls = [f"{FED}/newsevents/pressreleases/monetary{d}a.htm" for d in dates]
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(fomc_statement, urls))
    s, events = {}, []
    for d, url, r in zip(dates, urls, res):
        if r is None:
            continue
        n, action, who = r
        day = pd.Timestamp(d)
        s[day] = n
        if n:
            detail = "；".join(f"{name}：{why}" for name, why in who) or "声明只公布票数，未列异议者"
            events.append(("fomc_dissent", f"{day:%Y-%m-%d}", "异议", n, f"{action}；{detail}", url))
    return pd.Series(s, dtype=float), events


def fetch_mspd(code, start):
    """财政部《公共债务月报》表二（2001 年起）：法定债务上限、受上限约束的债务，百万美元→万亿美元。
    暂停上限期间表中上限记为 0，按缺失处理。"""
    def load():
        js = get("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/debt/mspd/mspd_table_2", params={
            "fields": "record_date,debt_limit_class1_desc,total_mil_amt,src_line_nbr",
            "filter": "debt_limit_class1_desc:in:(Statutory Debt Limit,Total Public Debt Subject to Limit)",
            "page[size]": 10000}).json()
        return pd.DataFrame(js["data"])
    df = cached("mspd", load)
    df = df[df["debt_limit_class1_desc"] == code].copy()
    # 2011 年以前表中有两行“受限债务”（加上担保债务前的小计与合计），取同月最后一行
    df["n"] = pd.to_numeric(df["src_line_nbr"])
    df = df.sort_values(["record_date", "n"]).drop_duplicates("record_date", keep="last")
    s = pd.Series(pd.to_numeric(df["total_mil_amt"], errors="coerce").values / 1e6, index=pd.to_datetime(df["record_date"]))
    s = s.where(s > 0).sort_index()
    return s[s.index >= pd.Timestamp(start)]


STEO_FILES = ["archives/jan14_base.xlsx", "archives/jan18_base.xlsx", "archives/jan22_base.xlsx", "xls/STEO_m.xlsx"]


def steo_rows(book, code):
    meta = {str(r[0]).strip(): r[3] for r in book["Dates"].itertuples(index=False)}
    begin = int(meta["Table Beginning Year---"])
    for name, df in book.items():
        hit = df[df.iloc[:, 0].astype(str).str.strip().str.lower() == code]
        if len(hit):
            v = pd.to_numeric(hit.iloc[0, 2:], errors="coerce")
            return pd.Series(v.values, index=pd.date_range(f"{begin}-01-01", periods=len(v), freq="MS")), \
                meta.get("Last Historical Month---")
    return None, None


def fetch_steo(code, start):
    """EIA 短期能源展望 STEO 全量表：当期文件只含近 4 年，更早的用各年 1 月版归档拼接（各取当时的历史月份）。
    code 加后缀 ":f" 取当期报告的预测段。"""
    code, _, part = code.partition(":")
    books = cached("steo", lambda: [pd.read_excel(io.BytesIO(get("https://www.eia.gov/outlooks/steo/" + f).content),
                                                  sheet_name=None, header=None) for f in STEO_FILES])
    hist, fc = pd.Series(dtype=float), pd.Series(dtype=float)
    for f, book in zip(STEO_FILES, books):
        s, last = steo_rows(book, code)
        if s is None:
            continue
        if pd.isna(last):  # 早期归档没有这一栏：1 月版的历史截至上年 12 月
            last = (2000 + int(re.search(r"jan(\d\d)", f).group(1)) - 1) * 100 + 12
        cut = pd.Timestamp(year=int(last) // 100, month=int(last) % 100, day=1)
        hist = s[s.index <= cut].combine_first(hist)  # 新版覆盖旧版的修订
        fc = s[s.index > cut]
    s = (fc if part == "f" else hist).dropna()
    return s[s.index >= pd.Timestamp(start)]


SAFE = "https://www.safe.gov.cn"


def fetch_safe_gold(_, start):
    """国家外汇管理局“官方储备资产”年表（2015 年 6 月起）：黄金储备，万盎司换算为吨。
    每个年度页面挂着当年历次更新的文件（及往年文件），按发布日期依次读取，后发布的覆盖先发布的。"""
    page = lambda u: get(u).text  # noqa: E731
    files = set()
    for href, label in re.findall(r'href="(/safe/\d{4}/\d{4}/\d+\.html)"[^>]*>\s*官方储备资产（([^）]*)）',
                                  page(f"{SAFE}/safe/whcb/index.html")):
        if max(int(y) for y in re.findall(r"\d{4}", label)) >= pd.Timestamp(start).year:
            files |= {urljoin(SAFE + href, f) for f in re.findall(r'href="([^"]+\.xlsx?)"', page(SAFE + href))}
    out = {}
    for url in sorted(files, key=lambda u: re.search(r"/(\d{8})/", u).group(1) if re.search(r"/(\d{8})/", u) else ""):
        for df in pd.read_excel(io.BytesIO(get(url).content), sheet_name=None, header=None).values():
            months = {}  # 列号 -> 月份。表头行首格为“项目 Item”，月份存成小数（2026.1 即 10 月）
            for i in range(min(10, len(df))):
                if "Item" not in str(df.iat[i, 0]):
                    continue
                for j, c in enumerate(df.iloc[i]):
                    m = re.fullmatch(r"(\d{4})\.(\d\d)", f"{c:.2f}" if isinstance(c, float) else str(c).strip())
                    if m and 1 <= int(m.group(2)) <= 12:
                        months[j] = pd.Timestamp(year=int(m.group(1)), month=int(m.group(2)), day=1)
            for i in range(len(df)):
                for j, c in enumerate(df.iloc[i]):
                    m = re.fullmatch(r"([\d.]+)\s*万盎司", str(c).strip())
                    if m and j in months:
                        out[months[j]] = float(m.group(1)) * 0.311034768  # 1 万盎司 = 0.311 吨
    return since(pd.Series(out, dtype=float).sort_index(), start)


def fetch_gld(_, start):
    """SPDR Gold Shares 官网历史档案（2004 年起）：GLD 信托持有黄金吨数，日度。"""
    raw = get("https://api.spdrgoldshares.com/api/v1/historical-archive",
              params=dict(product="gld", exchange="NYSE", lang="en")).content
    df = pd.read_excel(io.BytesIO(raw), sheet_name="US GLD Historical Archive")
    col = next(c for c in df.columns if str(c).strip().lower().startswith("tonnes of gold"))
    d = pd.to_datetime(df.iloc[:, 0].astype(str).str.strip(), format="%d-%b-%Y", errors="coerce")
    s = pd.Series(pd.to_numeric(df[col], errors="coerce").values, index=d)
    s = s[s.index.notna()]
    return s[s.index >= pd.Timestamp(start)]


ISM_LIST = "https://www.prnewswire.com/news/institute-for-supply-management/?page={}&pagesize=100"
MONTHS = "january february march april may june july august september october november december".split()
# 各报告“AT A GLANCE”表里要取的行，按表中先后顺序查找（Inventories 先于 Customers' Inventories 出现）
ISM_ROWS = {"M": [("PMI", r"PMI"), ("New Orders", r"New Orders"), ("Inventories", r"Inventories"), ("Prices", r"Prices")],
            "S": [("PMI", r"(?:PMI|NMI)"), ("Prices", r"Prices")]}


def ism_release(url):
    """一份 ISM 月报：返回 (类型 M/S, 报告月份, {行: (本月, 上月)})；取不到时返回 None。"""
    try:
        txt = plain(get(url, tries=5).text)  # PR Newswire 偶尔 503
    except requests.RequestException:
        return None
    g = re.search(r"AT A GLANCE", txt)
    # 制造业表头“MANUFACTURING AT A GLANCE September 2026”；服务业“SERVICES SURVEY RESULTS AT A GLANCE
    # COMPARISON OF ... SEPTEMBER 2026”（早年叫 NON-MANUFACTURING / NMI）
    m = g and re.search(rf"(?i)\b({'|'.join(MONTHS)})\s+((?:\d\s*){{4}})", txt[g.end():g.end() + 200])
    if not m:
        return None
    head = txt[max(0, g.start() - 60):g.start()]
    kind = "S" if re.search(r"SERVICES|NON-MANUFACTURING|NMI", head) else "M"
    month = pd.Timestamp(year=int(re.sub(r"\s", "", m.group(2))), month=MONTHS.index(m.group(1).lower()) + 1, day=1)
    vals, pos = {}, g.end() + m.end()
    for key, pat in ISM_ROWS[kind]:
        mm = re.compile(pat + r"\s*®?\s*(\d+(?:\.\d+)?)\s*%?\s+(\d+(?:\.\d+)?)").search(txt, pos)
        if mm and mm.start() - pos < 600:
            vals[key] = (float(mm.group(1)), float(mm.group(2)))
            pos = mm.end()
    return kind, month, vals


def ism_table(start):
    """PR Newswire 上 ISM 发布的制造业/服务业月报（2018 年起）。每期给出本月和上月值，上月值以下一期的修订为准。"""
    start = pd.Timestamp(start) - pd.DateOffset(months=1)
    urls = []
    for page in range(1, 10):
        try:
            html = get(ISM_LIST.format(page), tries=5).text
        except requests.RequestException:
            if page == 1:
                raise
            break  # 更早的列表页取不到就用已有的
        links = re.findall(r'href="(/news-releases/[^"]+?\.html)"', html)
        links = [u for u in dict.fromkeys(links) if re.search(r"report-on-business|pmi-report", u)
                 and not re.search(r"hospital|semiannual|seasonal|economic-growth|correction", u)]
        if not links:
            break
        oldest = None
        for u in links:
            m = re.search(rf"-({'|'.join(MONTHS)})-(\d{{4}})-", u)
            month = pd.Timestamp(year=int(m.group(2)), month=MONTHS.index(m.group(1)) + 1, day=1) if m else None
            if month is None or month >= start:  # 早年的链接不含年份，只能下载后看正文
                urls.append("https://www.prnewswire.com" + u)
            oldest = month if month is not None and (oldest is None or month < oldest) else oldest
        if oldest is not None and oldest < start:
            break
    with ThreadPoolExecutor(4) as ex:
        res = [r for r in ex.map(ism_release, urls) if r]
    table = {}
    for kind, month, vals in sorted(res, key=lambda r: r[1]):
        for key, (cur, prev) in vals.items():
            col = table.setdefault(f"{kind}:{key}", {})
            col[month] = cur
            col[month - pd.DateOffset(months=1)] = prev
    return {k: pd.Series(v, dtype=float).sort_index() for k, v in table.items()}


def fetch_ism(code, start):
    return since(cached(("ism", start), lambda: ism_table(start)).get(code, pd.Series(dtype=float)), start)


NFIB_NET = {"price_change_plan": (1, 2), "price_change": (3, 1)}  # 净占比 = 答案a − 答案b


def fetch_nfib(code, start):
    """NFIB 小企业调查官方数据接口：按各答案的回答数算净占比，与官网“未季调”口径一致（官网季调在网页前端计算）。"""
    body = {"app_name": "sbet", "params": [{"name": k, "param_type": "IN", "value": v} for k, v in [
        ("minYear", pd.Timestamp(start).year), ("minMonth", 1), ("maxYear", date.today().year), ("maxMonth", 12),
        ("questions", code), ("industry", ""), ("employee", ""), ("statev", "")]]}
    df = pd.DataFrame(get("https://api.nfib-sbet.org/rest/sbetdb/_proc/getTotals2", method="POST", json=body,
                          headers={"X-DreamFactory-Application-Name": "sbet"}).json())
    df["d"] = pd.to_datetime(dict(year=df["time_year"], month=df["time_month"], day=1))
    cnt = df.pivot_table(index="d", columns="resp_acode", values="totalcount", aggfunc="sum").fillna(0)
    a, b = NFIB_NET[code]
    return (cnt.get(a, 0) - cnt.get(b, 0)) / cnt.sum(axis=1) * 100


def fetch_comtrade(code, start):
    """UN Comtrade 公共预览接口（免 key，每次只能查一个月）：韩国按 HS 编码的出口额，美元→亿美元。
    月度，2015 年起，约滞后半年以上——比韩国产业部每月 1 日的初值晚得多，用作长历史。"""
    out = {}
    for p in pd.period_range(max(pd.Timestamp(start), pd.Timestamp("2015-01-01")), pd.Timestamp.today(), freq="M"):
        try:
            js = get("https://comtradeapi.un.org/public/v1/preview/C/M/HS", params=dict(
                reporterCode=410, period=f"{p.year}{p.month:02d}", cmdCode=code, flowCode="X", partnerCode=0,
                partner2Code=0, customsCode="C00", motCode=0)).json()
        except requests.HTTPError as e:
            # 免 key 接口有每日调用额度（约百余次）：先存下已取到的月份，下次运行从最后一个月接着取
            if out and e.response is not None and e.response.status_code == 403:
                print(f"  Comtrade 额度用完，已取到 {max(out):%Y-%m}，下次运行继续", flush=True)
                break
            raise
        for d in js.get("data") or []:
            out[p.to_timestamp()] = d["primaryValue"] / 1e8
    return pd.Series(out, dtype=float)


def fetch_yahoo_fut(code, start):
    """CME/CBOT 利率期货（联邦基金 ZQ、3个月 SOFR SR3）：隐含利率 = 100 − 价格。"""
    s = fetch_yahoo(code, start).dropna()
    # Yahoo 的 SR3 远月合约混有几年前的零星旧数据：在交合约应连续成交，只保留最后一个超过 60 天空档之后的部分
    gap = s.index.to_series().diff() > pd.Timedelta(days=60)
    if gap.any():
        s = s[s.index >= gap[gap].index[-1]]
    return 100 - s


def fetch_dramx(code, start):
    """DRAMeXchange 首页 DRAM 现货价：只公布当日盘均价、没有免费历史，每次运行记一行（存入 data/snapshots.csv）。"""
    txt = cached("dramx", lambda: plain(get("https://www.dramexchange.com/").text))
    i = txt.find(code)
    m = re.match(r"\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+(-?[\d.]+)\s*%", txt[i + len(code):]) \
        if i >= 0 else None
    upd = re.findall(r"Last Update:\s*([A-Z][a-z]{2})\.?\s*(\d{1,2})\s+(\d{4})", txt[:max(i, 0)])
    if not m or not upd:
        raise ValueError(f"首页未找到 {code} 的报价，页面格式可能变了")
    mon, day, year = upd[-1]  # 该表上方最近的“Last Update”
    return pd.Series([float(m.group(5))], index=[pd.Timestamp(f"{day} {mon} {year}")])


def fetch_ornn(code, start):
    """Ornn 计算价格指数（OCPI，data.ornn.com）：按实际成交的 GPU 租赁交易算出的每卡每小时价格，每天 20:00 UTC 结算。
    免费接口不要 key，但只给最近 3 个月的日度值；整段记入 data/snapshots.csv，更早的历史靠每次运行累积。"""
    d = get(f"https://data.ornn.com/api/public-index/gpu/{quote(code)}/index-history").json()
    s = pd.Series({pd.Timestamp(r["timestamp"][:10]): float(r["index_value"])
                   for r in d.get("data", []) if r.get("index_value") is not None})
    if s.empty:
        raise ValueError(f"{code} 没有返回数据")
    return s.sort_index()


# 同一次下载出多条序列的来源
SHARED_SOURCES = {"factset", "ism", "steo", "nyfed_sce", "nyfed_hhdc", "mspd", "umich", "dramx"}

FETCHERS = {"fred": fetch_fred, "yahoo": fetch_yahoo, "sec_capex": fetch_sec_capex, "sec_rpo": fetch_sec_rpo,
            "cftc": fetch_cftc, "nyfed_srf": fetch_nyfed_srf, "lw_rstar": fetch_lw_rstar, "eia": fetch_eia,
            "auctions": fetch_auctions, "umich": fetch_umich, "nyfed_sce": fetch_sce, "nyfed_hhdc": fetch_hhdc,
            "factset": fetch_factset, "mops": fetch_mops, "fomc": fetch_fomc, "mspd": fetch_mspd, "steo": fetch_steo,
            "safe_gold": fetch_safe_gold, "gld": fetch_gld, "ism": fetch_ism, "nfib": fetch_nfib,
            "comtrade": fetch_comtrade, "yahoo_fut": fetch_yahoo_fut, "dramx": fetch_dramx, "ornn": fetch_ornn}


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
    out["X_ISM_NO_INV"] = diff(L("ISM_NO"), L("ISM_INV"))
    out["X_TSMC_YOY"] = yoy(L("TSMC_REV"))
    out["X_KR_IC_YOY"] = yoy(L("KR_IC_EXP"))
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
            record_snapshot([(sid, d, pct, "moomoo")])
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
    restore_snapshots(con)
    only = [x for x in args.only.split(",") if x]
    ok, fail = 0, []
    t0 = time.time()
    starts = {}
    for sid, meta in SERIES.items():
        if only and sid not in only:
            continue
        if meta["source"] == "auctions":
            ld = con.execute("SELECT MAX(auction_date) FROM auctions").fetchone()[0]
            starts[sid] = START if args.full or not ld else (pd.Timestamp(ld) - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
        else:
            ld = None if args.full else last_date(con, sid)
            # 增量：回看一段时间以吸收修订；逐期下载文件的来源回看得短些（lookback 天）
            lb = meta.get("lookback", 400)
            starts[sid] = START if not ld else (pd.Timestamp(ld) - pd.DateOffset(days=lb)).strftime("%Y-%m-%d")
    # 一次下载出多条序列的来源（FactSet 周报、ISM 月报等）统一用组内最早的起点，只下载一次
    for src in SHARED_SOURCES:
        group = [sid for sid in starts if SERIES[sid]["source"] == src]
        for sid in group:
            starts[sid] = min(starts[g] for g in group)
    jobs = {}
    with ThreadPoolExecutor(args.workers) as ex:
        for sid, st in starts.items():
            meta = SERIES[sid]
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
                    if isinstance(res, tuple):  # (序列, 事件)
                        res, events = res
                        save_events(con, events)
                    if meta.get("replace"):  # 预测段等整段替换的序列，先清掉旧值
                        con.execute("DELETE FROM observations WHERE series_id=?", (sid,))
                    res = res * meta["scale"] if meta.get("scale") else res
                    n = save_series(con, sid, res, meta)
                    if meta.get("snapshot"):
                        record_snapshot([(sid, d, v, meta["source"]) for d, v in res.dropna().items()])
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
