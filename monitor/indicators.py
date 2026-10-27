"""命题与指标配置。

SERIES：数据库里的每一条原始序列（来源 + 代码）。
THESES：看板结构。每个命题下若干指标，每个指标引用一条或多条序列，并指定展示方式。

展示方式 chart：
  line   折线图（可放多条同量纲序列）
  bar    柱状图（季度/月度金额、增速）
  table  表格（拍卖结果、事件、手工录入）
  manual 无免费自动来源，展示来源说明 + 手工录入数据（manual_data.csv）
"""

from datetime import date

START = "2010-01-01"

# ---------------------------------------------------------------------------
# 原始序列：sid -> dict(source, code, name, unit, freq)
#   source: fred | yahoo | sec_capex | sec_rpo | cftc | nyfed_srf | auctions | lw_rstar | eia
# ---------------------------------------------------------------------------
def fred(code, name, unit, freq, scale=None):
    # scale：入库前乘以该系数（如 FRED 以百万美元发布、这里按十亿美元存储时用 1e-3）
    d = dict(source="fred", code=code, name=name, unit=unit, freq=freq)
    if scale:
        d["scale"] = scale
    return d


def yahoo(code, name, unit="价格", freq="D"):
    return dict(source="yahoo", code=code, name=name, unit=unit, freq=freq)


SERIES = {
    # 美股
    "RSP": yahoo("RSP", "标普500等权ETF（复权）"),
    "SPY": yahoo("SPY", "标普500 ETF（复权）"),
    **{t: yahoo(t, f"{n} 行业ETF") for t, n in [
        ("XLK", "科技"), ("XLF", "金融"), ("XLE", "能源"), ("XLV", "医疗"), ("XLI", "工业"),
        ("XLY", "可选消费"), ("XLP", "必需消费"), ("XLU", "公用事业"), ("XLB", "材料"),
        ("XLRE", "地产"), ("XLC", "通信")]},
    # 衰退
    "ICSA": fred("ICSA", "初请失业金人数", "人", "W"),
    "CCSA": fred("CCSA", "续请失业金人数", "人", "W"),
    "PHL_NO": fred("NOCDFSA066MSFRBPHI", "费城联储制造业：新订单", "扩散指数", "M"),
    "PHL_INV": fred("IVCDFSA066MSFRBPHI", "费城联储制造业：库存", "扩散指数", "M"),
    "DSPIC96": fred("DSPIC96", "实际可支配收入", "十亿2017美元", "M"),
    "PSAVERT": fred("PSAVERT", "个人储蓄率", "%", "M"),
    "DRCCLACBS": fred("DRCCLACBS", "商业银行信用卡拖欠率", "%", "Q"),
    "DRCLACBS": fred("DRCLACBS", "商业银行消费贷款拖欠率", "%", "Q"),
    "UMCSENT": fred("UMCSENT", "密歇根大学消费者信心", "指数", "M"),
    "RSAFS": fred("RSAFS", "零售销售", "百万美元", "M"),
    "RSFSXMV": fred("RSFSXMV", "零售销售（除汽车）", "百万美元", "M"),
    # 通胀
    "CPIAUCSL": fred("CPIAUCSL", "CPI", "指数", "M"),
    "CPILFESL": fred("CPILFESL", "核心CPI", "指数", "M"),
    "PPIFIS": fred("PPIFIS", "PPI 最终需求", "指数", "M"),
    "PCEPILFE": fred("PCEPILFE", "核心PCE价格指数", "指数", "M"),
    "MICH": fred("MICH", "密大1年通胀预期", "%", "M"),
    "PHL_PP": fred("PPCDFSA066MSFRBPHI", "费城联储制造业：支付价格", "扩散指数", "M"),
    "PCE": fred("PCE", "个人消费支出", "十亿美元", "M"),
    # FRED 没有月度的“汽油及其他能源商品”名义支出，只有季度；月度用口径更宽的“能源商品与服务”补充
    "PCE_GAS": fred("DGOERC1Q027SBEA", "汽油及其他能源商品支出（季度）", "十亿美元", "Q"),
    "PCE_ENERGY": fred("DNRGRC1M027SBEA", "能源商品与服务支出（月度）", "十亿美元", "M"),
    "T5YIE": fred("T5YIE", "5年盈亏平衡通胀", "%", "D"),
    "T5YIFR": fred("T5YIFR", "5年后5年远期通胀预期", "%", "D"),
    # 利率与政策
    "DGS2": fred("DGS2", "2年期美债", "%", "D"),
    "DGS10": fred("DGS10", "10年期美债", "%", "D"),
    "DGS30": fred("DGS30", "30年期美债", "%", "D"),
    "DFF": fred("DFF", "联邦基金有效利率", "%", "D"),
    "DFEDTARU": fred("DFEDTARU", "联邦基金目标上限", "%", "D"),
    "FEDTARMD": fred("FEDTARMD", "点阵图中位数（各预测年份）", "%", "A"),
    "FEDTARMDLR": fred("FEDTARMDLR", "点阵图长期中位数", "%", "A"),
    "NFCI": fred("NFCI", "芝加哥联储金融条件指数", "指数", "W"),
    "ANFCI": fred("ANFCI", "调整后金融条件指数", "指数", "W"),
    "RSTAR": dict(source="lw_rstar", code="LW", name="r*（Laubach-Williams）", unit="%", freq="Q"),
    # 流动性
    "WALCL": fred("WALCL", "美联储总资产", "百万美元", "W"),
    "WRESBAL": fred("WRESBAL", "银行准备金余额", "十亿美元", "W", scale=1e-3),  # FRED 原值为百万美元
    "RRPONTSYD": fred("RRPONTSYD", "隔夜逆回购余额", "十亿美元", "D"),
    "WTREGEN": fred("WTREGEN", "财政部一般账户 TGA", "十亿美元", "W", scale=1e-3),  # FRED 原值为百万美元
    "SOFR": fred("SOFR", "SOFR", "%", "D"),
    "SOFR99": fred("SOFR99", "SOFR 99分位", "%", "D"),
    "IORB": fred("IORB", "准备金利率 IORB", "%", "D"),
    "IOER": fred("IOER", "超额准备金利率 IOER（2021年前）", "%", "D"),
    "SRF": dict(source="nyfed_srf", code="repo", name="常备回购便利使用量", unit="十亿美元", freq="D"),
    # 主权债
    **{f"Y10_{c}": fred(f"IRLTLT01{c}M156N", f"{n}10年期国债（月均）", "%", "M") for c, n in [
        ("DE", "德国"), ("JP", "日本"), ("GB", "英国"), ("FR", "法国"), ("IT", "意大利"),
        ("CA", "加拿大"), ("AU", "澳大利亚")]},
    "AUCTIONS": dict(source="auctions", code="fiscaldata", name="美债拍卖结果", unit="", freq="E"),
    "IEF": yahoo("IEF", "7-10年美债ETF（复权）"),
    "TLT": yahoo("TLT", "20年以上美债ETF（复权）"),
    # 信用
    "IG_OAS": fred("BAMLC0A0CM", "投资级OAS", "%", "D"),
    "HY_OAS": fred("BAMLH0A0HYM2", "高收益OAS", "%", "D"),
    "CCC_OAS": fred("BAMLH0A3HYC", "CCC及以下OAS", "%", "D"),
    "BB_OAS": fred("BAMLH0A1HYBB", "BB级OAS", "%", "D"),
    "BAA10Y": fred("BAA10Y", "穆迪Baa−10年美债（长历史）", "%", "D"),
    "BKLN": yahoo("BKLN", "杠杆贷款ETF BKLN（价格）"),
    "SRLN": yahoo("SRLN", "杠杆贷款ETF SRLN（价格）"),
    "ECC": yahoo("ECC", "CLO权益基金 ECC（价格）"),
    "OXLC": yahoo("OXLC", "CLO权益基金 OXLC（价格）"),
    # AI
    **{f"CAPEX_{t}": dict(source="sec_capex", code=t, name=f"{n} 资本开支（季度）", unit="十亿美元", freq="Q")
       for t, n in [("MSFT", "微软"), ("GOOGL", "谷歌"), ("AMZN", "亚马逊"), ("META", "Meta"),
                    ("ORCL", "甲骨文"), ("CRWV", "CoreWeave")]},
    **{f"RPO_{t}": dict(source="sec_rpo", code=t, name=f"{n} RPO", unit="十亿美元", freq="Q")
       for t, n in [("MSFT", "微软"), ("ORCL", "甲骨文"), ("CRWV", "CoreWeave"), ("GOOGL", "谷歌"),
                    ("AMZN", "亚马逊")]},
    "TSM": yahoo("TSM", "台积电ADR"),
    # GDP
    "INFO_EQ": fred("Y034RC1Q027SBEA", "信息处理设备投资（名义）", "十亿美元", "Q"),
    "SOFTWARE": fred("B985RC1Q027SBEA", "软件投资（名义）", "十亿美元", "Q"),
    "FPI": fred("FPI", "私人固定投资（名义）", "十亿美元", "Q"),
    "PRFI": fred("PRFI", "住宅投资（名义）", "十亿美元", "Q"),
    "NRS": fred("B009RC1Q027SBEA", "非住宅建筑投资（名义）", "十亿美元", "Q"),
    "GDP": fred("GDP", "名义GDP", "十亿美元", "Q"),
    "NEWORDER": fred("NEWORDER", "核心资本品订单", "百万美元", "M"),
    "ANXAVS": fred("ANXAVS", "核心资本品出货", "百万美元", "M"),
    # 能源
    "WTI": yahoo("CL=F", "WTI原油期货"),
    "BRENT": yahoo("BZ=F", "布伦特原油期货"),
    "SPR": dict(source="eia", code="WCSSTUS1", name="美国战略石油储备", unit="千桶", freq="W"),
    "CRUDE_COM": dict(source="eia", code="WCESTUS1", name="美国商业原油库存", unit="千桶", freq="W"),
    # 黄金
    "GOLD": yahoo("GC=F", "COMEX黄金期货"),
    "SILVER": yahoo("SI=F", "COMEX白银期货"),
    "DXY": yahoo("DX-Y.NYB", "美元指数DXY"),
    "DTWEXBGS": fred("DTWEXBGS", "广义美元指数", "指数", "D"),
    "DFII10": fred("DFII10", "10年期TIPS实际收益率", "%", "D"),
    "COT_GOLD": dict(source="cftc", code="088691", name="COMEX黄金管理基金净多头", unit="手", freq="W"),
    "PBOC_GOLD": dict(source="safe_gold", code="gold", name="人民银行黄金储备", unit="吨", freq="M"),
    "GLD_TONNES": dict(source="gld", code="GLD", name="SPDR GLD 黄金持仓", unit="吨", freq="D"),
    # ---- 以下原列为“手工录入”，后来找到了稳定的免费来源 ----
    # lookback：增量更新时回看的天数（默认 400）。逐期下载文件的来源回看短些，免得每次重下一年的 PDF/网页
    "FWD_PE": dict(source="factset", code="pe", name="标普500远期市盈率", unit="倍", freq="W", lookback=10),
    "FWD_PE_5Y": dict(source="factset", code="avg5", name="5年均值", unit="倍", freq="W", lookback=10),
    "FWD_PE_10Y": dict(source="factset", code="avg10", name="10年均值", unit="倍", freq="W", lookback=10),
    **{f"ISM_{k}": dict(source="ism", code=c, name=n, unit="扩散指数", freq="M", lookback=70) for k, c, n in [
        ("PMI", "M:PMI", "ISM制造业PMI"), ("NO", "M:New Orders", "ISM制造业新订单"),
        ("INV", "M:Inventories", "ISM制造业库存"), ("PRICES", "M:Prices", "ISM制造业价格"),
        ("S_PRICES", "S:Prices", "ISM服务业价格")]},
    "NYF_CC_90": dict(source="nyfed_hhdc", code="CC", name="信用卡：新增严重拖欠（90天+）", unit="%", freq="Q"),
    "NYF_AUTO_90": dict(source="nyfed_hhdc", code="AUTO", name="车贷：新增严重拖欠（90天+）", unit="%", freq="Q"),
    "UMICH_5Y": dict(source="umich", code="PX5_MD", name="密大5–10年通胀预期", unit="%", freq="M"),
    **{f"SCE_{n}Y": dict(source="nyfed_sce", code=c, name=f"纽约联储SCE {n}年通胀预期", unit="%", freq="M")
       for n, c in [(1, "one"), (3, "three"), (5, "five")]},
    "NFIB_PRICE_PLAN": dict(source="nfib", code="price_change_plan", name="NFIB 计划提价净占比", unit="%", freq="M"),
    "NFIB_PRICE_ACT": dict(source="nfib", code="price_change", name="NFIB 实际提价净占比", unit="%", freq="M"),
    "FOMC_DISSENT": dict(source="fomc", code="statement", name="FOMC 异议票数", unit="票", freq="E", lookback=120),
    "DEBT_LIMIT": dict(source="mspd", code="Statutory Debt Limit", name="法定债务上限（暂停期间无值）",
                       unit="万亿美元", freq="M"),
    "DEBT_SUBJ": dict(source="mspd", code="Total Public Debt Subject to Limit", name="受上限约束的债务",
                      unit="万亿美元", freq="M"),
    "TSMC_REV": dict(source="mops", code="2330", name="台积电月营收", unit="亿新台币", freq="M", lookback=62),
    "KR_IC_EXP": dict(source="comtrade", code="8542", name="韩国集成电路出口（HS8542，Comtrade）", unit="亿美元",
                      freq="M", lookback=300),
    # STEO：历史段与当期报告的预测段分开存；预测段每月整段替换（replace）
    "OPEC_SPARE": dict(source="steo", code="cops_opec", name="OPEC闲置产能", unit="百万桶/天", freq="M"),
    "OPEC_SPARE_F": dict(source="steo", code="cops_opec:f", name="OPEC闲置产能（EIA预测）", unit="百万桶/天", freq="M",
                         replace=True),
    "OPEC_OUTAGE": dict(source="steo", code="padi_opec", name="OPEC非计划停产", unit="百万桶/天", freq="M"),
    "OECD_INV": dict(source="steo", code="pasc_oecd_t3", name="OECD商业石油库存", unit="百万桶", freq="M"),
    "OECD_INV_F": dict(source="steo", code="pasc_oecd_t3:f", name="OECD商业石油库存（EIA预测）", unit="百万桶", freq="M",
                       replace=True),
    # 只有当天报价、没有免费历史的来源：每次运行记一行，另存 data/snapshots.csv
    # DRAM 只取与 AI 服务器相关的 DDR5：服务器内存条 RDIMM 和 DDR5 颗粒；DDR4、台式机 UDIMM、GDDR 等 PC/显卡用料不取
    **{f"DRAM_{k}": dict(source="dramx", code=c, name=n, unit="美元", freq="D", snapshot=True) for k, c, n in [
        ("DDR5_RDIMM_32G", "DDR5 RDIMM 32GB 4800/5600", "DDR5 RDIMM 32GB 服务器内存条"),
        ("DDR5_16G", "DDR5 16Gb (2Gx8) 4800/5600", "DDR5 16Gb 颗粒")]},
    **{f"GPU_{k}": dict(source="vast", code=c, name=c, unit="美元/卡时", freq="D", snapshot=True) for k, c in [
        ("B200", "B200"), ("H200", "H200"), ("H100", "H100 SXM"), ("A100", "A100 SXM4")]},
}

FUT_MONTHS = "FGHJKMNQUVXZ"


def rate_futures(today=None):
    """利率期货合约：联邦基金期货（ZQ）当月起 15 个月，3个月 SOFR 期货（SR3）当季起 12 个季月。
    合约随日期滚动，到期的不再下载，但历史留在库里。"""
    t = today or date.today()
    out = {}
    for k in range(15):
        y, m = t.year + (t.month - 1 + k) // 12, (t.month - 1 + k) % 12 + 1
        out[f"ZQ_{y}{m:02d}"] = dict(source="yahoo_fut", code=f"ZQ{FUT_MONTHS[m - 1]}{y % 100:02d}.CBT",
                                     name=f"联邦基金期货 {y}-{m:02d}", unit="%", freq="D")
    q0 = (t.month - 1) // 3 * 3 + 2  # 当季季月（3/6/9/12）减一
    for k in range(12):
        y, m = t.year + (q0 + 3 * k) // 12, (q0 + 3 * k) % 12 + 1
        out[f"SR3_{y}{m:02d}"] = dict(source="yahoo_fut", code=f"SR3{FUT_MONTHS[m - 1]}{y % 100:02d}.CME",
                                      name=f"3个月SOFR期货 {y}-{m:02d}", unit="%", freq="D")
    return out


SERIES.update(rate_futures())

# ---------------------------------------------------------------------------
# 派生序列：由 update_data.derive() 计算，sid 以 "X_" 开头
# ---------------------------------------------------------------------------
DERIVED = {
    "X_RSP_SPY": ("RSP/SPY 比值", "比值"),
    "X_ICSA_4W": ("初请 4周均值", "人"),
    "X_PHL_NO_INV": ("费城联储 新订单−库存", "点"),
    "X_DSPIC96_YOY": ("实际可支配收入同比", "%"),
    "X_RSAFS_YOY": ("零售销售同比", "%"),
    "X_CPI_YOY": ("CPI同比", "%"),
    "X_CORECPI_YOY": ("核心CPI同比", "%"),
    "X_PPI_YOY": ("PPI同比", "%"),
    "X_COREPCE_YOY": ("核心PCE同比", "%"),
    "X_GAS_SHARE": ("汽油及其他能源商品占PCE比重（季度）", "%"),
    "X_ENERGY_SHARE": ("能源商品与服务占PCE比重（月度，含电力燃气）", "%"),
    "X_2S10S": ("2s10s 利差", "bp"),
    "X_REAL_FF": ("实际政策利率（FF−核心PCE同比）", "%"),
    "X_REAL_GAP": ("实际政策利率 − r*", "%"),
    "X_NETLIQ": ("净流动性（美联储资产−TGA−逆回购）", "十亿美元"),
    "X_SOFR_IORB": ("SOFR − IORB", "bp"),
    "X_SOFR99_IORB": ("SOFR 99分位 − IORB", "bp"),
    "X_CORR_SPY_IEF": ("SPY与IEF回报 63日滚动相关", "相关系数"),
    "X_CCC_BB": ("CCC − BB 利差", "%"),
    "X_CAPEX_TOTAL": ("六家合计资本开支（季度）", "十亿美元"),
    "X_CAPEX_TOTAL_YOY": ("六家合计资本开支同比", "%"),
    "X_INFO_SW_GDP": ("信息处理设备+软件投资占GDP", "%"),
    "X_FPI_EX_AI": ("剔除信息设备与软件后的私人固定投资", "十亿美元"),
    "X_FPI_EX_AI_YOY": ("剔除AI相关后私人固定投资同比", "%"),
    "X_NEWORDER_YOY": ("核心资本品订单同比", "%"),
    "X_ANXAVS_YOY": ("核心资本品出货同比", "%"),
    "X_GOLD_SILVER": ("金银比", "比值"),
    "X_GOLD_OIL": ("金油比", "比值"),
    "X_GOLD_TLT": ("金/TLT 比", "比值"),
    "X_GOLD_RESID": ("黄金相对实际利率的残差（滚动3年回归）", "对数偏离"),
    "X_GOLD_CORR": ("金价与TIPS收益率变化 126日滚动相关", "相关系数"),
    "X_BREADTH_200": ("标普500成分股站上200日均线占比（当前成分股，幸存者偏差）", "%"),
    # 以下两条由 update_data.moomoo_breadth() 从本机 moomoo OpenD 选股器取，每次运行记一行
    "X_BREADTH_MOOMOO_SPX": ("标普500（moomoo选股器）", "%"),
    "X_BREADTH_MOOMOO_US": ("全美股含OTC（moomoo选股器）", "%"),
    "X_ISM_NO_INV": ("ISM 新订单−库存", "点"),
    "X_TSMC_YOY": ("台积电月营收同比", "%"),
    "X_KR_IC_YOY": ("韩国集成电路出口同比", "%"),
}

# ---------------------------------------------------------------------------
# 看板结构
# ---------------------------------------------------------------------------
def ind(name, chart, series=(), note="", source="", ref=None, unit=None, links=(), nodata=""):
    """links：能查看该指标数据的网页 [(名称, 网址)]，卡片上可点击跳转。
    nodata：原指标没有免费下载渠道时的说明，卡片上显示“原指标暂无数据”（有手工记录或自记快照时显示“无免费下载渠道”）。"""
    return dict(name=name, chart=chart, series=list(series), note=note, source=source,
                ref=ref, unit=unit, links=[dict(label=k, url=u) for k, u in links], nodata=nodata)


THESES = [
    dict(id="equity", title="美股分板块的分化程度和估值水平", indicators=[
        ind("等权/市值加权之比 RSP/SPY", "line", ["X_RSP_SPY"], "比值下行=头部大盘股跑赢、市场集中度上升", "Yahoo Finance"),
        ind("标普500远期市盈率", "line", ["FWD_PE", "FWD_PE_10Y", "FWD_PE_5Y"],
            "FactSet《Earnings Insight》每周五的免费 PDF（2017 年起；一年以前每月取一期）", "FactSet", ref="forward_pe"),
        ind("站上200日均线的个股占比", "line", ["X_BREADTH_200", "X_BREADTH_MOOMOO_SPX", "X_BREADTH_MOOMOO_US"],
            "Yahoo 线用当前标普500成分股自算，越早期越高估（幸存者偏差）；moomoo 为本机 OpenD 选股器每次运行时的快照"
            "（分母为有满200日数据的股票，全美股含OTC）", "Yahoo Finance 自算 / moomoo OpenD",
            links=[("StockCharts $SPXA200R（对照）", "https://stockcharts.com/sc3/ui/?s=%24SPXA200R")]),
        ind("行业ETF相对标普表现（近1年）", "sector", ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC", "SPY"],
            "各行业过去252个交易日回报，衡量板块分化", "Yahoo Finance"),
    ]),
    dict(id="recession", title="美国12个月内是否进入衰退", indicators=[
        ind("初请失业金（含4周均值）", "line", ["ICSA", "X_ICSA_4W"], "每周四公布", "FRED"),
        ind("ISM制造业新订单−库存", "line", ["X_ISM_NO_INV", "X_PHL_NO_INV"],
            "ISM 取自其在 PR Newswire 发布的月报（2018 年起）；费城联储同口径作长历史对照", "ISM / FRED"),
        ind("续请失业金", "line", ["CCSA"], "", "FRED"),
        ind("密歇根大学消费者信心", "line", ["UMCSENT"], "", "FRED"),
        ind("零售销售同比（银行卡消费替代）", "line", ["X_RSAFS_YOY"], "美国银行卡消费数据无免费接口，用普查局零售销售替代", "FRED",
            links=[("美国银行研究院 Consumer Checkpoint（银行卡消费月报）", "https://institute.bankofamerica.com/consumer-checkpoint.html")]),
        ind("实际可支配收入同比", "line", ["X_DSPIC96_YOY"], "", "FRED"),
        ind("储蓄率", "line", ["PSAVERT"], "", "FRED"),
        ind("信用卡/车贷：新增严重拖欠与拖欠率", "line", ["NYF_CC_90", "NYF_AUTO_90", "DRCCLACBS", "DRCLACBS", "M:subprime_auto_60d"],
            "纽约联储：当季新进入 90 天以上拖欠的余额占比；商业银行拖欠率来自 FRED；惠誉次级车贷 ABS 60+ 逾期率为新闻稿数字",
            "纽约联储 / FRED / 惠誉", ref="subprime_auto_60d", unit="%"),
    ]),
    dict(id="inflation", title="美国通胀是否继续上行", indicators=[
        ind("CPI / 核心CPI / 核心PCE 同比", "line", ["X_CPI_YOY", "X_CORECPI_YOY", "X_COREPCE_YOY"], "", "FRED"),
        ind("ISM 价格指数（制造业/服务业）", "line", ["ISM_PRICES", "ISM_S_PRICES", "PHL_PP"],
            "费城联储支付价格作长历史对照", "ISM / FRED"),
        ind("NFIB 小企业提价：计划与实际", "line", ["NFIB_PRICE_PLAN", "NFIB_PRICE_ACT"],
            "用官方数据接口的原始回答数算净占比，未季调（官网公布值经季调，会差几个点）", "NFIB", ref="nfib_price_plans"),
        ind("PPI 最终需求同比", "line", ["X_PPI_YOY"], "", "FRED"),
        ind("市场通胀预期：5年盈亏平衡、5y5y", "line", ["T5YIE", "T5YIFR"], "", "FRED"),
        ind("消费者通胀预期：密大与纽约联储 SCE", "line", ["MICH", "UMICH_5Y", "SCE_1Y", "SCE_3Y", "SCE_5Y"],
            "均为中位数；密大为 1 年与 5–10 年，SCE 为 1/3/5 年（5 年期 2022 年起）", "密歇根大学 / 纽约联储"),
        ind("汽油能源支出占PCE比重", "line", ["X_GAS_SHARE", "X_ENERGY_SHARE"],
            "汽油口径在FRED上只有季度数据；月度线为更宽的能源商品与服务（含电力、燃气）", "FRED（BEA）"),
    ]),
    dict(id="fed", title="加息终点预测", indicators=[
        ind("利率期货隐含路径 vs 点阵图", "curve", ["ZQ_*", "SR3_*", "FEDTARMD"],
            "近 15 个月为联邦基金期货（月均有效利率），之后接 3 个月 SOFR 期货；Yahoo 没有 SOFR 期货的历史，"
            "过去的曲线只画到联邦基金期货覆盖的月份", "CME（经 Yahoo）/ 美联储"),
        ind("年底隐含利率", "line", ["ZQ_DEC+0", "ZQ_DEC+1", "DFF"], "今年、明年 12 月联邦基金期货隐含的月均有效利率",
            "CME（经 Yahoo）/ FRED", ref="sofr_implied_eoy"),
        ind("点阵图中位数", "dots", ["FEDTARMD", "FEDTARMDLR"], "最近一次SEP各年份联邦基金利率中位数", "FRED"),
        ind("2y / 10y / 30y 美债收益率", "line", ["DGS2", "DGS10", "DGS30"], "", "FRED"),
        ind("2s10s 利差", "line", ["X_2S10S"], "", "FRED"),
        ind("实际政策利率与 r*", "line", ["X_REAL_FF", "RSTAR", "X_REAL_GAP"], "实际利率=有效联邦基金利率−核心PCE同比", "FRED / 纽约联储"),
        ind("金融条件指数 NFCI", "line", ["NFCI", "ANFCI"], "正值=偏紧；高盛FCI为付费数据", "FRED"),
        ind("FOMC 异议票", "bar", ["FOMC_DISSENT"], "每次议息会议的反对票数；异议者及其主张见下表（鹰/鸽为相对实际决议）",
            "美联储议息声明", ref="fomc_dissent"),
    ]),
    dict(id="liquidity", title="美元流动性衡量", indicators=[
        ind("净流动性", "line", ["X_NETLIQ"], "美联储总资产 − TGA − 隔夜逆回购", "FRED"),
        ind("准备金 / 逆回购 / TGA", "line", ["WRESBAL", "RRPONTSYD", "WTREGEN"], "", "FRED"),
        ind("SOFR − IORB 利差（含99分位）", "line", ["X_SOFR_IORB", "X_SOFR99_IORB"], "季末跳升在99分位上最明显", "FRED"),
        ind("常备回购便利 SRF 使用量", "bar", ["SRF"], "", "纽约联储"),
        ind("美国债务上限与受限债务", "line", ["DEBT_LIMIT", "DEBT_SUBJ"], "月末值；上限暂停期间无值（线条断开），修订事件见下表",
            "财政部 Fiscal Data（MSPD）", ref="debt_ceiling"),
    ]),
    dict(id="sovereign", title="全球主权债务危机预测", indicators=[
        ind("主要国家10年期国债收益率", "line", ["DGS10", "Y10_DE", "Y10_JP", "Y10_GB", "Y10_FR", "Y10_IT"], "FRED/OECD 为月均值", "FRED"),
        ind("标普与美债回报 3个月滚动相关", "line", ["X_CORR_SPY_IEF"], "正相关=股债同跌，对冲失效", "Yahoo Finance"),
        ind("美债拍卖：投标倍数、间接投标人占比", "auctions", ["AUCTIONS"], "尾差需发行前收益率（无免费源），可手工录入", "财政部 Fiscal Data", ref="auction_tail"),
    ]),
    dict(id="credit", title="公开市场信用利差是否走阔", indicators=[
        ind("IG / HY 期权调整利差", "line", ["IG_OAS", "HY_OAS"], "FRED 只给最近约3年的ICE数据", "FRED（ICE）"),
        ind("CCC − BB 利差", "line", ["X_CCC_BB"], "", "FRED（ICE）"),
        ind("长历史信用利差：Baa − 10年美债", "line", ["BAA10Y"], "", "FRED"),
        ind("杠杆贷款ETF价格（替代）", "line", ["BKLN", "SRLN"], "LSTA贷款指数为付费数据", "Yahoo Finance"),
        ind("CLO权益基金价格（替代）", "line", ["ECC", "OXLC"], "", "Yahoo Finance"),
        ind("不良债券占比 / 穆迪违约率 / CDX", "manual", [], "付费数据，见新闻时录入", "穆迪 / 标普 / Markit", ref="default_rate", unit="%"),
    ]),
    dict(id="ai_capex", title="AI资本开支的可持续性", indicators=[
        ind("六家公司季度资本开支", "stack", ["CAPEX_MSFT", "CAPEX_GOOGL", "CAPEX_AMZN", "CAPEX_META", "CAPEX_ORCL", "CAPEX_CRWV"],
            "SEC 财报现金流量表；Nebius 为外国发行人未纳入；横轴标*的季度尚有公司未披露；指引修正请手工录入", "SEC EDGAR", ref="capex_guidance"),
        ind("合计资本开支同比", "bar", ["X_CAPEX_TOTAL_YOY"], "", "SEC EDGAR"),
        ind("台积电月营收", "bar", ["TSMC_REV"], "每月 10 日前公布上月营收", "证交所公开资讯观测站"),
        ind("台积电月营收同比", "line", ["X_TSMC_YOY"], "", "证交所公开资讯观测站"),
        ind("韩国半导体出口", "line", ["KR_IC_EXP", "M:korea_semi_exports"],
            "Comtrade 为海关 HS8542 集成电路口径（滞后约半年）；产业部每月 1 日公布的半导体出口初值手工录入",
            "UN Comtrade / 韩国产业通商资源部", ref="korea_semi_exports", unit="亿美元"),
        ind("服务器内存条现货价（DDR5 RDIMM 32GB）", "line", ["DRAM_DDR5_RDIMM_32G", "M:dram_spot"],
            "AI 服务器 CPU 侧用的就是 DDR5 RDIMM；DRAMeXchange 每周更新一次报价、没有免费历史，2026-10 起每次运行记录一次",
            "DRAMeXchange", ref="dram_spot", unit="美元",
            nodata="AI 加速卡用的 HBM 没有公开报价，服务器内存合约价只对付费会员开放",
            links=[("DRAMeXchange 现货价", "https://www.dramexchange.com/")]),
        ind("DDR5 16Gb 颗粒现货价", "line", ["DRAM_DDR5_16G"],
            "服务器 RDIMM 和 PC 内存条共用的 DDR5 颗粒，反映 DDR5 整体供需；只有当日盘均价，2026-10 起每次运行记录一次",
            "DRAMeXchange"),
    ]),
    dict(id="ai_credit", title="AI基础设施融资是否演变为信用事件", indicators=[
        ind("CDS：CoreWeave、甲骨文、软银、Meta、亚马逊、Alphabet、微软、英伟达", "manual", [], "",
            "ICE / S&P Global / Bloomberg（付费）", ref="cds", unit="bp",
            nodata="单名 CDS 报价只在付费终端（ICE、S&P Global、Bloomberg）提供，没有找到可免费下载或查看的网页"),
        ind("GPU 租赁价格", "line", ["GPU_B200", "GPU_H200", "GPU_H100", "GPU_A100", "M:gpu_rental"],
            "图中为 Vast.ai 公开市场按需挂单的每卡每小时中位数（含已租出），2026-10 起每次运行记录；样本少、噪声大",
            "Vast.ai", ref="gpu_rental", unit="美元/卡时",
            nodata="Silicon Data 等 GPU 租赁指数只在网页上公布当日值，没有免费下载或历史数据",
            links=[("Silicon Data GPU 租赁指数（H100/H200/B200 等）", "https://www.silicondata.com/products/silicon-index"),
                   ("Vast.ai 实时价格", "https://vast.ai/pricing")]),
        ind("新云厂商债券收益率 / GPU ABS 利差", "manual", [], "", "FINRA TRACE", ref="neocloud_bond_yield", unit="%",
            nodata="新云厂商债券没有免费下载接口，只能在 FINRA 网站按发行人逐只查询成交价和收益率；GPU 抵押贷款和 ABS 利差没有公开数据",
            links=[("FINRA 公司债行情", "https://www.finra.org/finra-data/fixed-income/corp-and-agency")]),
    ]),
    dict(id="ai_return", title="AI商业回报是否增速二阶导下降", indicators=[
        ind("RPO / 积压订单", "line", ["RPO_ORCL", "RPO_MSFT", "RPO_GOOGL", "RPO_AMZN", "RPO_CRWV"], "", "SEC EDGAR"),
        ind("头部模型公司ARR", "manual", [], "媒体报道，注明来源", "新闻", ref="model_arr", unit="十亿美元"),
        ind("token调用量与单价", "manual", [], "", "OpenRouter / 财报", ref="tokens", unit="",
            nodata="token 调用量没有统一的公开统计，只有 OpenRouter 的路由流量排行和公司财报里偶尔披露的数字",
            links=[("OpenRouter 调用量排行", "https://openrouter.ai/rankings"), ("OpenRouter 模型单价", "https://openrouter.ai/models"),
                   ("Silicon Data token 价格指数", "https://www.silicondata.com/products/silicon-index/llm-token-expenditure-index")]),
        ind("云业务增速与折旧年限", "manual", [], "Azure/GCP/AWS 同比增速与折旧政策调整", "财报", ref="cloud_growth", unit="%",
            links=[("微软投资者关系", "https://www.microsoft.com/en-us/investor"),
                   ("亚马逊季度业绩", "https://ir.aboutamazon.com/quarterly-results/default.aspx"),
                   ("Alphabet 投资者关系", "https://abc.xyz/investor/")]),
    ]),
    dict(id="ai_gdp", title="AI投资对GDP的支撑效果", indicators=[
        ind("信息处理设备+软件投资占GDP", "line", ["X_INFO_SW_GDP"], "", "FRED（BEA）"),
        ind("剔除AI相关后私人固定投资同比", "line", ["X_FPI_EX_AI_YOY"], "近似：私人固定投资−信息处理设备−软件", "FRED（BEA）"),
        ind("住宅投资与非住宅建筑", "line", ["PRFI", "NRS"], "数据中心建筑分项见普查局建筑支出（未剔除）", "FRED（BEA）"),
        ind("核心资本品订单与出货同比", "line", ["X_NEWORDER_YOY", "X_ANXAVS_YOY"], "", "FRED"),
    ]),
    dict(id="energy", title="能源问题", indicators=[
        ind("原油价格", "line", ["WTI", "BRENT"], "", "Yahoo Finance"),
        ind("OPEC 闲置产能与非计划停产", "line", ["OPEC_SPARE", "OPEC_SPARE_F", "OPEC_OUTAGE"],
            "EIA 短期能源展望；虚线为当期报告的预测", "EIA STEO", ref="opec_spare"),
        ind("OECD 商业石油库存", "line", ["OECD_INV", "OECD_INV_F"], "虚线为当期报告的预测；海湾出口量（Kpler/Vortexa）为付费数据",
            "EIA STEO"),
        ind("美国战略石油储备与商业库存", "line", ["SPR", "CRUDE_COM"], "", "EIA"),
        ind("VLCC 运价（TD3C 中东湾—中国）", "manual", [], "", "波罗的海交易所", ref="vlcc_td3c", unit="WS",
            nodata="TD3C 日度运价是波罗的海交易所的付费数据，没有免费下载渠道；交易所官网每周五的免费油轮周报会给出当周 TD3C 的 WS 点数和日租金",
            links=[("波罗的海交易所每周油轮报告（Tanker 栏）", "https://www.balticexchange.com/en/data-services/WeeklyRoundup.html")]),
    ]),
    dict(id="gold", title="黄金", indicators=[
        ind("金价", "line", ["GOLD"], "", "Yahoo Finance"),
        ind("黄金相对实际利率的残差", "line", ["X_GOLD_RESID"], "log(金价) 对 10年TIPS 收益率做滚动3年回归的残差", "Yahoo / FRED"),
        ind("金价与实际利率滚动相关", "line", ["X_GOLD_CORR"], "传统上为负相关，转正说明定价逻辑变化", "Yahoo / FRED"),
        ind("人民银行黄金储备", "line", ["PBOC_GOLD"], "外汇局每月 7 日公布，万盎司换算为吨（2016 年起）", "国家外汇管理局"),
        ind("黄金 ETF 持仓（SPDR GLD）", "line", ["GLD_TONNES"], "全球最大的黄金 ETF，信托持金吨数", "SPDR Gold Shares"),
        ind("COMEX管理基金净多头", "bar", ["COT_GOLD"], "每周五公布（周二数据）", "CFTC"),
        ind("美元指数", "line", ["DXY", "DTWEXBGS"], "", "Yahoo / FRED"),
        ind("金银比", "line", ["X_GOLD_SILVER"], "", "Yahoo Finance"),
        ind("金油比 / 金TLT比", "line", ["X_GOLD_OIL", "X_GOLD_TLT"], "", "Yahoo Finance"),
        ind("全球央行购金（季度）", "manual", [], "世界黄金协会《黄金需求趋势》季报中的央行净购买量", "世界黄金协会",
            ref="cb_gold", unit="吨", links=[("世界黄金协会《黄金需求趋势》", "https://www.gold.org/goldhub/research/gold-demand-trends"),
                                            ("各国央行黄金储备", "https://www.gold.org/goldhub/data/gold-reserves-by-country")]),
    ]),
]
