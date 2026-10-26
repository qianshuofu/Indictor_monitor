"""命题与指标配置。

SERIES：数据库里的每一条原始序列（来源 + 代码）。
THESES：看板结构。每个命题下若干指标，每个指标引用一条或多条序列，并指定展示方式。

展示方式 chart：
  line   折线图（可放多条同量纲序列）
  bar    柱状图（季度/月度金额、增速）
  table  表格（拍卖结果、事件、手工录入）
  manual 无免费自动来源，展示来源说明 + 手工录入数据（manual_data.csv）
"""

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
    "PHL_PR": fred("PRCDFSA066MSFRBPHI", "费城联储制造业：收取价格", "扩散指数", "M"),
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
    "GFDEBTN": fred("GFDEBTN", "联邦债务总额", "百万美元", "Q"),
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
    "MU": yahoo("MU", "美光"),
    "HYNIX": yahoo("000660.KS", "SK海力士（韩元）"),
    "SEC_KR": yahoo("005930.KS", "三星电子（韩元）"),
    **{f"PX_{t}": yahoo(t, n) for t, n in [
        ("CRWV", "CoreWeave"), ("ORCL", "甲骨文"), ("NBIS", "Nebius"), ("NVDA", "英伟达"),
        ("META", "Meta"), ("MSFT", "微软"), ("AMZN", "亚马逊"), ("GOOGL", "谷歌"), ("SFTBY", "软银ADR")]},
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
    "FRO": yahoo("FRO", "Frontline 油轮股"),
    "DHT": yahoo("DHT", "DHT 油轮股"),
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
}

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
}

# ---------------------------------------------------------------------------
# 看板结构
# ---------------------------------------------------------------------------
def ind(name, chart, series=(), note="", source="", ref=None, unit=None):
    return dict(name=name, chart=chart, series=list(series), note=note, source=source,
                ref=ref, unit=unit)


THESES = [
    dict(id="equity", title="美股分板块的分化程度和估值水平", indicators=[
        ind("等权/市值加权之比 RSP/SPY", "line", ["X_RSP_SPY"], "比值下行=头部大盘股跑赢、市场集中度上升", "Yahoo Finance"),
        ind("行业ETF相对标普表现（近1年）", "sector", ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC", "SPY"],
            "各行业过去252个交易日回报，衡量板块分化", "Yahoo Finance"),
        ind("站上200日均线的个股占比", "line", ["X_BREADTH_200", "X_BREADTH_MOOMOO_SPX", "X_BREADTH_MOOMOO_US"],
            "Yahoo 线用当前标普500成分股自算，越早期越高估（幸存者偏差）；moomoo 为本机 OpenD 选股器每次运行时的快照"
            "（分母为有满200日数据的股票，全美股含OTC）；StockCharts $SPXA200R 可对照", "Yahoo Finance 自算 / moomoo OpenD"),
        ind("远期市盈率", "manual", [], "FactSet Earnings Insight 每周五免费 PDF，手工录入 manual_data.csv", "FactSet / Yardeni", ref="forward_pe", unit="倍"),
    ]),
    dict(id="recession", title="美国12个月内是否进入衰退", indicators=[
        ind("初请失业金（含4周均值）", "line", ["ICSA", "X_ICSA_4W"], "每周四公布", "FRED"),
        ind("续请失业金", "line", ["CCSA"], "", "FRED"),
        ind("ISM新订单−库存（替代：费城联储）", "line", ["X_PHL_NO_INV"],
            "ISM数据已不在FRED；用费城联储同口径替代，ISM原值可手工录入", "FRED / ISM", ref="ism_no_inv"),
        ind("实际可支配收入同比", "line", ["X_DSPIC96_YOY"], "", "FRED"),
        ind("储蓄率", "line", ["PSAVERT"], "", "FRED"),
        ind("信用卡/消费贷拖欠率（商业银行）", "line", ["DRCCLACBS", "DRCLACBS"],
            "纽约联储分类违约转移率、次级车贷ABS 60+逾期率请手工录入", "FRED / 纽约联储 / 惠誉", ref="subprime_auto_60d"),
        ind("密歇根大学消费者信心", "line", ["UMCSENT"], "", "FRED"),
        ind("零售销售同比（银行卡消费替代）", "line", ["X_RSAFS_YOY"], "美国银行卡消费数据无免费接口，用普查局零售销售替代", "FRED"),
    ]),
    dict(id="inflation", title="美国通胀是否继续上行", indicators=[
        ind("CPI / 核心CPI / 核心PCE 同比", "line", ["X_CPI_YOY", "X_CORECPI_YOY", "X_COREPCE_YOY"], "", "FRED"),
        ind("PPI 最终需求同比", "line", ["X_PPI_YOY"], "", "FRED"),
        ind("通胀预期：密大1年、5年盈亏平衡、5y5y", "line", ["MICH", "T5YIE", "T5YIFR"],
            "密大5–10年预期、纽约联储SCE请手工录入或后续接入", "FRED / 密大 / 纽约联储", ref="umich_5_10"),
        ind("价格指数（ISM替代：费城联储支付/收取价格）", "line", ["PHL_PP", "PHL_PR"], "NFIB提价计划请手工录入", "FRED / NFIB", ref="nfib_price_plans"),
        ind("汽油能源支出占PCE比重", "line", ["X_GAS_SHARE", "X_ENERGY_SHARE"],
            "汽油口径在FRED上只有季度数据；月度线为更宽的能源商品与服务（含电力、燃气）", "FRED（BEA）"),
    ]),
    dict(id="fed", title="加息终点预测", indicators=[
        ind("2y / 10y / 30y 美债收益率", "line", ["DGS2", "DGS10", "DGS30"], "", "FRED"),
        ind("2s10s 利差", "line", ["X_2S10S"], "", "FRED"),
        ind("点阵图中位数", "dots", ["FEDTARMD", "FEDTARMDLR"], "最近一次SEP各年份联邦基金利率中位数", "FRED"),
        ind("SOFR期货隐含路径", "manual", [], "CME FedWatch 免费查看，建议每周记录年底隐含利率", "CME", ref="sofr_implied_eoy", unit="%"),
        ind("FOMC异议票与主席措辞", "manual", [], "每次会议记录异议数与鹰鸽标签", "美联储官网", ref="fomc_dissent", unit="票"),
        ind("金融条件指数 NFCI", "line", ["NFCI", "ANFCI"], "正值=偏紧；高盛FCI为付费数据", "FRED"),
        ind("实际政策利率与 r*", "line", ["X_REAL_FF", "RSTAR", "X_REAL_GAP"], "实际利率=有效联邦基金利率−核心PCE同比", "FRED / 纽约联储"),
    ]),
    dict(id="liquidity", title="美元流动性衡量", indicators=[
        ind("准备金 / 逆回购 / TGA", "line", ["WRESBAL", "RRPONTSYD", "WTREGEN"], "", "FRED"),
        ind("净流动性", "line", ["X_NETLIQ"], "美联储总资产 − TGA − 隔夜逆回购", "FRED"),
        ind("SOFR − IORB 利差（含99分位）", "line", ["X_SOFR_IORB", "X_SOFR99_IORB"], "季末跳升在99分位上最明显", "FRED"),
        ind("常备回购便利 SRF 使用量", "bar", ["SRF"], "", "纽约联储"),
        ind("美国债务上限与联邦债务", "line", ["GFDEBTN"], "债务上限修订事件请录入 manual_data.csv", "FRED / 财政部", ref="debt_ceiling"),
    ]),
    dict(id="sovereign", title="全球主权债务危机预测", indicators=[
        ind("主要国家10年期国债收益率", "line", ["DGS10", "Y10_DE", "Y10_JP", "Y10_GB", "Y10_FR", "Y10_IT"], "FRED/OECD 为月均值", "FRED"),
        ind("美债拍卖：投标倍数、间接投标人占比", "auctions", ["AUCTIONS"], "尾差需发行前收益率（无免费源），可手工录入", "财政部 Fiscal Data", ref="auction_tail"),
        ind("标普与美债回报 3个月滚动相关", "line", ["X_CORR_SPY_IEF"], "正相关=股债同跌，对冲失效", "Yahoo Finance"),
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
        ind("台积电月度营收", "manual", [], "每月10日前后台积电官网公布", "台积电IR", ref="tsmc_rev", unit="亿新台币"),
        ind("韩国半导体出口", "manual", [], "每月1日韩国产业通商资源部公布", "MOTIE / KITA", ref="korea_semi_exports", unit="亿美元"),
        ind("存储股价格（DRAM/HBM价格替代）", "rebased", ["MU", "HYNIX", "SEC_KR"],
            "美光为美元、海力士/三星为韩元，按所选区间起点=100定基；TrendForce DRAM现货可手工录入", "Yahoo / TrendForce", ref="dram_spot"),
    ]),
    dict(id="ai_credit", title="AI基础设施融资是否演变为信用事件", indicators=[
        ind("相关公司股价（CDS替代）", "rebased", ["PX_CRWV", "PX_ORCL", "PX_NBIS", "PX_SFTBY", "PX_META", "PX_NVDA"],
            "CDS为付费数据；以一年前=100重新定基", "Yahoo Finance", ref="cds"),
        ind("新云厂商债券收益率 / GPU ABS 利差", "manual", [], "FINRA TRACE 逐只查询后录入", "FINRA TRACE", ref="neocloud_bond_yield", unit="%"),
        ind("GPU租赁价格（H100/H200/B200/GB200）", "manual", [], "Silicon Data、Vast.ai 等，从现在起每日/每周记录", "Silicon Data / Vast.ai", ref="gpu_rental", unit="美元/小时"),
    ]),
    dict(id="ai_return", title="AI商业回报是否增速二阶导下降", indicators=[
        ind("RPO / 积压订单", "line", ["RPO_ORCL", "RPO_MSFT", "RPO_GOOGL", "RPO_AMZN", "RPO_CRWV"], "", "SEC EDGAR"),
        ind("头部模型公司ARR", "manual", [], "媒体报道，注明来源", "新闻", ref="model_arr", unit="十亿美元"),
        ind("token调用量与单价", "manual", [], "OpenRouter 排行、财报披露", "OpenRouter / 财报", ref="tokens", unit=""),
        ind("云业务增速与折旧年限", "manual", [], "Azure/GCP/AWS 同比增速与折旧政策调整", "财报", ref="cloud_growth", unit="%"),
    ]),
    dict(id="ai_gdp", title="AI投资对GDP的支撑效果", indicators=[
        ind("信息处理设备+软件投资占GDP", "line", ["X_INFO_SW_GDP"], "", "FRED（BEA）"),
        ind("剔除AI相关后私人固定投资同比", "line", ["X_FPI_EX_AI_YOY"], "近似：私人固定投资−信息处理设备−软件", "FRED（BEA）"),
        ind("住宅投资与非住宅建筑", "line", ["PRFI", "NRS"], "数据中心建筑分项见普查局建筑支出（未剔除）", "FRED（BEA）"),
        ind("核心资本品订单与出货同比", "line", ["X_NEWORDER_YOY", "X_ANXAVS_YOY"], "", "FRED"),
    ]),
    dict(id="energy", title="能源问题", indicators=[
        ind("原油价格", "line", ["WTI", "BRENT"], "", "Yahoo Finance"),
        ind("油轮股（VLCC运价替代）", "line", ["FRO", "DHT"], "波罗的海运价为付费数据，可手工录入TD3C", "Yahoo / 波罗的海交易所", ref="vlcc_td3c"),
        ind("美国战略石油储备与商业库存", "line", ["SPR", "CRUDE_COM"], "", "EIA"),
        ind("海湾出口量 / OECD库存 / OPEC+闲置产能", "manual", [], "Kpler/Vortexa付费；EIA STEO需免费key", "EIA / IEA / Kpler", ref="opec_spare", unit="百万桶/天"),
    ]),
    dict(id="gold", title="黄金", indicators=[
        ind("金价", "line", ["GOLD"], "", "Yahoo Finance"),
        ind("COMEX管理基金净多头", "bar", ["COT_GOLD"], "每周五公布（周二数据）", "CFTC"),
        ind("黄金相对实际利率的残差", "line", ["X_GOLD_RESID"], "log(金价) 对 10年TIPS 收益率做滚动3年回归的残差", "Yahoo / FRED"),
        ind("金价与实际利率滚动相关", "line", ["X_GOLD_CORR"], "传统上为负相关，转正说明定价逻辑变化", "Yahoo / FRED"),
        ind("金银比", "line", ["X_GOLD_SILVER"], "", "Yahoo Finance"),
        ind("金油比 / 金TLT比", "line", ["X_GOLD_OIL", "X_GOLD_TLT"], "", "Yahoo Finance"),
        ind("美元指数", "line", ["DXY", "DTWEXBGS"], "", "Yahoo / FRED"),
        ind("央行购金 / 人行储备 / ETF持仓", "manual", [], "世界黄金协会 Goldhub、外汇管理局", "WGC / SAFE", ref="cb_gold", unit="吨"),
    ]),
]
