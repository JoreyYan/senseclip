"""
宏观与市场数据采集(engine 常驻线程)

按「硬数据 → 利率 → 仓位/微观结构 → 信用与跨市场」四层组织,全部来自官方免费接口,无需密钥:

  fred      美联储圣路易斯分行 FRED(fredgraph.csv):就业、通胀、零售、利率、期限溢价、信用利差、油价、美元
  treasury  美国财政部 Fiscal Data(auctions_query):国债拍卖日历与结果(发行量、中标利率、认购倍数)
  cftc      CFTC Traders in Financial Futures(Socrata):股指/美债/VIX 期货各类交易者净持仓
  cboe      CBOE 官方历史 CSV:VIX、SKEW

每个数据源按自己的更新频率定时拉取,只增量写入(带回看窗口以吸收数据修订),
失败只影响该源、下轮重试;健康状态写 market_series.status 与 app_settings.market_collector_v1。

拿不到的(投行内部/付费):CTA 仓位、Gamma 头寸、盘口深度、Prime Broker 对冲基金仓位、杠杆 ETF 资金流、
CDS、ISM PMI。用公开数据近似:VIX/SKEW、CFTC 杠杆基金净持仓、HY/IG 信用利差,见 docs/market-data.md。
"""

import csv
import io
import json
import logging
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

STATUS_KEY = "market_collector_v1"
HISTORY_START = os.environ.get("MARKET_HISTORY_START", "2000-01-01")
UA = {"User-Agent": "SenseClip market-data collector (+https://github.com/JoreyYan/senseclip)"}

# (id, 中文名, 层, 优先级, 频率, 单位, 变换)
FRED_SERIES = [
    # ── 第一层:硬数据 ──
    ("PAYEMS", "非农就业(总数)", "hard_data", "tier1", "monthly", "千人", "mom_diff"),
    ("CES0500000003", "平均时薪", "hard_data", "tier1", "monthly", "美元/小时", "yoy_pct"),
    ("ICSA", "初请失业金人数", "hard_data", "tier1", "weekly", "人", "level"),
    ("CCSA", "续请失业金人数", "hard_data", "aux", "weekly", "人", "level"),
    ("JTSJOL", "JOLTS 职位空缺", "hard_data", "tier1", "monthly", "千个", "level"),
    ("ADPMNUSNERSA", "ADP 私营部门就业", "hard_data", "aux", "monthly", "人", "mom_diff"),
    ("UNRATE", "失业率", "hard_data", "aux", "monthly", "%", "level"),
    ("PCEPI", "PCE 物价指数", "hard_data", "tier1", "monthly", "指数", "yoy_pct"),
    ("PCEPILFE", "核心 PCE 物价指数", "hard_data", "tier1", "monthly", "指数", "yoy_pct"),
    ("CPIAUCSL", "CPI", "hard_data", "tier1", "monthly", "指数", "yoy_pct"),
    ("CPILFESL", "核心 CPI", "hard_data", "tier1", "monthly", "指数", "yoy_pct"),
    ("RSAFS", "零售销售", "hard_data", "tier1", "monthly", "百万美元", "mom_pct"),
    ("A191RL1Q225SBEA", "实际 GDP 环比折年率", "hard_data", "aux", "quarterly", "%", "level"),
    ("UMCSENT", "密歇根消费者信心(软数据)", "hard_data", "aux", "monthly", "指数", "level"),
    # ── 第二层:利率与国债 ──
    ("DGS10", "10 年期美债收益率", "rates", "tier1", "daily", "%", "level"),
    ("DGS2", "2 年期美债收益率", "rates", "tier1", "daily", "%", "level"),
    ("DGS5", "5 年期美债收益率", "rates", "aux", "daily", "%", "level"),
    ("DGS30", "30 年期美债收益率", "rates", "aux", "daily", "%", "level"),
    ("T10Y2Y", "10年-2年期利差", "rates", "tier1", "daily", "百分点", "level"),
    ("THREEFYTP10", "10 年期期限溢价(Kim-Wright)", "rates", "tier1", "daily", "百分点", "level"),
    ("DFF", "联邦基金有效利率", "rates", "aux", "daily", "%", "level"),
    ("MTSDS133FMS", "联邦财政月度盈余/赤字", "rates", "aux", "monthly", "百万美元", "level"),
    # ── 第三层:仓位与波动(公开可得部分) ──
    ("VIXCLS", "VIX(FRED 备份源)", "positioning", "aux", "daily", "点", "level"),
    # ── 第四层:信用与跨市场 ──
    ("BAMLH0A0HYM2", "高收益债信用利差(ICE BofA OAS)", "credit_cross", "tier1", "daily", "百分点", "level"),
    ("BAMLC0A0CM", "投资级信用利差(ICE BofA OAS)", "credit_cross", "aux", "daily", "百分点", "level"),
    ("DCOILWTICO", "WTI 原油", "credit_cross", "tier1", "daily", "美元/桶", "level"),
    ("DCOILBRENTEU", "布伦特原油", "credit_cross", "aux", "daily", "美元/桶", "level"),
    ("DTWEXBGS", "广义美元指数(DXY 替代)", "credit_cross", "tier1", "daily", "指数", "level"),
]

CBOE_SERIES = [
    ("VIX_CBOE", "VIX 恐慌指数", "positioning", "tier1", "daily", "点",
     "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"),
    ("SKEW_CBOE", "CBOE SKEW 尾部风险指数", "positioning", "tier1", "daily", "点",
     "https://cdn.cboe.com/api/global/us_indices/daily_prices/SKEW_History.csv"),
]

# CFTC TFF 合约代码 → (简称, 中文名)
COT_CONTRACTS = {
    "13874A": ("ES", "标普 500 E-mini"),
    "209742": ("NQ", "纳斯达克 100 E-mini"),
    "043602": ("TY", "10 年期美债期货"),
    "042601": ("TU", "2 年期美债期货"),
    "1170E1": ("VX", "VIX 期货"),
}
COT_TRADERS = {"dealer": "交易商", "asset_mgr": "资产管理人", "lev_money": "杠杆基金", "other_rept": "其他报告者"}

# 每个数据源的检查间隔(秒)。数据源本身日更/周更,这里只是检查频率
SOURCE_INTERVALS = {"fred": 4 * 3600, "cboe": 6 * 3600, "treasury": 3 * 3600, "cftc": 12 * 3600}


def _num(v):
    try:
        if v is None:
            return None
        s = str(v).strip()
        if s in ("", ".", "null", "None", "NaN"):
            return None
        return float(s)
    except (ValueError, TypeError):
        return None


def _get(url: str, params: dict = None, timeout: int = 60, retries: int = 3) -> requests.Response:
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=timeout)
            if r.status_code == 200:
                return r
            last = RuntimeError(f"HTTP {r.status_code}: {r.text[:120]}")
        except Exception as e:
            last = e
        time.sleep(5 * (i + 1))
    raise last


class MarketCollector:
    def __init__(self, supabase):
        self.supabase = supabase
        self._stop = threading.Event()
        self._thread = None
        self._last_run: dict = {}
        self.status: dict = {"running": False, "phase": "idle", "sources": {}}

    # ── 控制 ──────────────────────────────────────────────────
    def start(self) -> dict:
        if self._thread and self._thread.is_alive():
            return {"status": "already_running"}
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.status["running"] = True
        return {"status": "started"}

    def stop(self) -> None:
        self._stop.set()

    def run_now(self, source: str = "") -> dict:
        """管理接口手动触发(同步执行指定源,或全部)。"""
        out = {}
        for src in ([source] if source else list(SOURCE_INTERVALS)):
            out[src] = self._run_source(src)
        return out

    def _loop(self) -> None:
        self._ensure_catalog()
        if self._stop.wait(30):
            return
        while not self._stop.is_set():
            now = time.time()
            for src, interval in SOURCE_INTERVALS.items():
                if self._stop.is_set():
                    break
                if now - self._last_run.get(src, 0) >= interval:
                    self._run_source(src)
            if self._stop.wait(600):
                break
        self.status["running"] = False

    def _run_source(self, src: str) -> dict:
        self.status["phase"] = f"collecting {src}"
        t0 = time.time()
        try:
            fn = {"fred": self.collect_fred, "cboe": self.collect_cboe,
                  "treasury": self.collect_treasury, "cftc": self.collect_cftc}[src]
            res = fn()
            info = {"ok": True, **res}
        except Exception as e:
            logger.warning(f"[market] {src} failed: {str(e)[:200]}")
            info = {"ok": False, "error": str(e)[:300]}
        info.update({"at": datetime.now(timezone.utc).isoformat(), "seconds": round(time.time() - t0, 1)})
        self._last_run[src] = time.time()
        self.status["sources"][src] = info
        self.status["phase"] = "idle"
        try:
            self.supabase.table("app_settings").upsert(
                {"key": STATUS_KEY, "value": json.dumps(self.status["sources"], ensure_ascii=False)}).execute()
        except Exception:
            pass
        logger.info(f"[market] {src}: {info}")
        return info

    # ── 目录与写入 ────────────────────────────────────────────
    def _ensure_catalog(self) -> None:
        rows = [{"id": sid, "name_zh": name, "layer": layer, "priority": pri, "source": "fred",
                 "frequency": freq, "unit": unit, "transform": tf}
                for sid, name, layer, pri, freq, unit, tf in FRED_SERIES]
        rows += [{"id": sid, "name_zh": name, "layer": layer, "priority": pri, "source": "cboe",
                  "frequency": freq, "unit": unit, "transform": "level"}
                 for sid, name, layer, pri, freq, unit, _ in CBOE_SERIES]
        for code, (short, name) in COT_CONTRACTS.items():
            for tk, tname in COT_TRADERS.items():
                rows.append({"id": f"COT_{short}_{tk.upper()}_NET", "name_zh": f"{name}:{tname}净持仓",
                             "layer": "positioning",
                             "priority": "tier1" if tk in ("lev_money", "asset_mgr") else "aux",
                             "source": "cftc", "frequency": "weekly", "unit": "张", "transform": "level"})
        for i in range(0, len(rows), 200):
            self.supabase.table("market_series").upsert(rows[i:i + 200], on_conflict="id").execute()

    def _last_date(self, series_id: str):
        r = (self.supabase.table("market_series").select("last_date")
             .eq("id", series_id).limit(1).execute().data) or []
        return r[0]["last_date"] if r and r[0].get("last_date") else None

    def _write_series(self, series_id: str, points: list, lookback_days: int = 120) -> int:
        """points: [(date_str, value)]。只写回看窗口内的点(首次全量),吸收数据修订。"""
        last = self._last_date(series_id)
        if last:
            cutoff = (datetime.fromisoformat(last) - timedelta(days=lookback_days)).date().isoformat()
            points = [p for p in points if p[0] >= cutoff]
        else:
            points = [p for p in points if p[0] >= HISTORY_START]
        rows = [{"series_id": series_id, "obs_date": d, "value": v} for d, v in points if v is not None]
        for i in range(0, len(rows), 1000):
            self.supabase.table("market_observations").upsert(
                rows[i:i + 1000], on_conflict="series_id,obs_date").execute()
        if rows:
            latest = max(rows, key=lambda r: r["obs_date"])
            self.supabase.table("market_series").update({
                "last_date": latest["obs_date"], "last_value": latest["value"],
                "fetched_at": datetime.now(timezone.utc).isoformat(), "status": "ok", "error": None,
            }).eq("id", series_id).execute()
        return len(rows)

    def _mark_error(self, series_id: str, err: str) -> None:
        try:
            self.supabase.table("market_series").update({
                "status": "error", "error": err[:300],
                "fetched_at": datetime.now(timezone.utc).isoformat()}).eq("id", series_id).execute()
        except Exception:
            pass

    # ── FRED ──────────────────────────────────────────────────
    def collect_fred(self) -> dict:
        ok, failed, written = 0, [], 0
        for sid, *_ in FRED_SERIES:
            if self._stop.is_set():
                break
            try:
                r = _get("https://fred.stlouisfed.org/graph/fredgraph.csv", {"id": sid})
                reader = csv.reader(io.StringIO(r.text))
                header = next(reader)
                if len(header) < 2:
                    raise RuntimeError(f"unexpected header {header}")
                pts = [(row[0], _num(row[1])) for row in reader if len(row) >= 2]
                if not pts:
                    raise RuntimeError("empty series")
                written += self._write_series(sid, pts)
                ok += 1
            except Exception as e:
                failed.append(sid)
                self._mark_error(sid, str(e))
            time.sleep(1)  # 对 FRED 友好
        if failed and not ok:
            raise RuntimeError(f"all FRED series failed, e.g. {failed[:3]}")
        return {"series_ok": ok, "failed": failed, "rows": written}

    # ── CBOE ──────────────────────────────────────────────────
    def collect_cboe(self) -> dict:
        ok, failed, written = 0, [], 0
        for sid, _, _, _, _, _, url in CBOE_SERIES:
            try:
                r = _get(url, timeout=90)
                reader = csv.DictReader(io.StringIO(r.text))
                cols = [c.strip().upper() for c in (reader.fieldnames or [])]
                date_col = next((c for c in reader.fieldnames if c.strip().upper() == "DATE"), None)
                val_col = next((c for c in reader.fieldnames if c.strip().upper() in ("CLOSE", "SKEW")),
                               reader.fieldnames[-1] if reader.fieldnames else None)
                if not date_col or not val_col:
                    raise RuntimeError(f"unexpected columns {cols}")
                pts = []
                for row in reader:
                    raw = (row.get(date_col) or "").strip()
                    try:
                        d = datetime.strptime(raw, "%m/%d/%Y").date().isoformat()
                    except ValueError:
                        try:
                            d = datetime.fromisoformat(raw).date().isoformat()
                        except ValueError:
                            continue
                    pts.append((d, _num(row.get(val_col))))
                if not pts:
                    raise RuntimeError("empty csv")
                written += self._write_series(sid, pts, lookback_days=30)
                ok += 1
            except Exception as e:
                failed.append(sid)
                self._mark_error(sid, str(e))
        if failed and not ok:
            raise RuntimeError(f"CBOE unreachable: {failed}")
        return {"series_ok": ok, "failed": failed, "rows": written}

    # ── 美国财政部:国债拍卖 ─────────────────────────────────
    def collect_treasury(self) -> dict:
        since = (date.today() - timedelta(days=400)).isoformat()
        latest = (self.supabase.table("treasury_auctions").select("auction_date")
                  .order("auction_date", desc=True).limit(1).execute().data) or []
        if latest:
            since = (datetime.fromisoformat(latest[0]["auction_date"]) - timedelta(days=45)).date().isoformat()
        base = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query"
        rows, page = [], 1
        while True:
            r = _get(base, {"filter": f"auction_date:gte:{since}", "sort": "-auction_date",
                            "page[size]": "500", "page[number]": str(page)}).json()
            for d in r.get("data") or []:
                rows.append({
                    "cusip": d.get("cusip"), "auction_date": d.get("auction_date"),
                    "security_type": d.get("security_type"), "security_term": d.get("security_term"),
                    "original_security_term": d.get("original_security_term"),
                    "reopening": (d.get("reopening") == "Yes"),
                    "announcement_date": d.get("announcemt_date") if d.get("announcemt_date") not in (None, "null") else None,
                    "issue_date": d.get("issue_date") if d.get("issue_date") not in (None, "null") else None,
                    "maturity_date": d.get("maturity_date") if d.get("maturity_date") not in (None, "null") else None,
                    "offering_amt": _num(d.get("offering_amt")),
                    "high_yield": _num(d.get("high_yield")),
                    "high_discount_rate": _num(d.get("high_discnt_rate")),
                    "bid_to_cover": _num(d.get("bid_to_cover_ratio")),
                    "total_accepted": _num(d.get("total_accepted")),
                    "primary_dealer_accepted": _num(d.get("primary_dealer_accepted")),
                    "direct_bidder_accepted": _num(d.get("direct_bidder_accepted")),
                    "indirect_bidder_accepted": _num(d.get("indirect_bidder_accepted")),
                    "soma_holdings": _num(d.get("soma_holdings")),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                })
            total_pages = int(((r.get("meta") or {}).get("total-pages")) or 1)
            if page >= total_pages or page >= 20:
                break
            page += 1
        rows = [x for x in rows if x["cusip"] and x["auction_date"]]
        # 同一 cusip+日期可能重复出现(修订),保留最后一条
        dedup = {(x["cusip"], x["auction_date"]): x for x in rows}
        rows = list(dedup.values())
        for i in range(0, len(rows), 500):
            self.supabase.table("treasury_auctions").upsert(
                rows[i:i + 500], on_conflict="cusip,auction_date").execute()
        upcoming = sum(1 for x in rows if x["auction_date"] >= date.today().isoformat())
        return {"rows": len(rows), "upcoming": upcoming, "since": since}

    # ── CFTC:金融期货持仓 ───────────────────────────────────
    def collect_cftc(self) -> dict:
        latest = (self.supabase.table("cot_positions").select("report_date")
                  .order("report_date", desc=True).limit(1).execute().data) or []
        since = "2018-01-01"
        if latest:
            since = (datetime.fromisoformat(latest[0]["report_date"]) - timedelta(days=21)).date().isoformat()
        codes = ",".join(f"'{c}'" for c in COT_CONTRACTS)
        r = _get("https://publicreporting.cftc.gov/resource/gpe5-46if.json", {
            "$where": f"cftc_contract_market_code in({codes}) AND report_date_as_yyyy_mm_dd >= '{since}T00:00:00'",
            "$order": "report_date_as_yyyy_mm_dd ASC", "$limit": "50000"}, timeout=120).json()
        if isinstance(r, dict) and r.get("error"):
            raise RuntimeError(str(r)[:200])
        rows, series_pts = [], {}
        for d in r:
            code = d.get("cftc_contract_market_code")
            if code not in COT_CONTRACTS:
                continue
            rd = (d.get("report_date_as_yyyy_mm_dd") or "")[:10]
            nets = {}
            for key in d:
                if "_positions_long" in key:
                    prefix = key.split("_positions_long")[0]
                    short_key = key.replace("_positions_long", "_positions_short")
                    lv, sv = _num(d.get(key)), _num(d.get(short_key))
                    if lv is not None and sv is not None and prefix not in nets:
                        nets[prefix] = lv - sv
            row = {
                "contract_code": code, "report_date": rd,
                "market_name": d.get("market_and_exchange_names"),
                "open_interest": _num(d.get("open_interest_all")),
                "dealer_net": nets.get("dealer"), "asset_mgr_net": nets.get("asset_mgr"),
                "lev_money_net": nets.get("lev_money"), "other_rept_net": nets.get("other_rept"),
                "nonrept_net": nets.get("nonrept"),
                "raw": {k: v for k, v in d.items() if "positions" in k or k.startswith("change_")},
            }
            rows.append(row)
            short = COT_CONTRACTS[code][0]
            for tk in COT_TRADERS:
                v = row.get(f"{tk}_net")
                if v is not None:
                    series_pts.setdefault(f"COT_{short}_{tk.upper()}_NET", []).append((rd, v))
        for i in range(0, len(rows), 500):
            self.supabase.table("cot_positions").upsert(
                rows[i:i + 500], on_conflict="contract_code,report_date").execute()
        written = 0
        for sid, pts in series_pts.items():
            written += self._write_series(sid, pts, lookback_days=60)
        if not rows:
            raise RuntimeError(f"no COT rows since {since} (field names changed?)")
        return {"rows": len(rows), "series_rows": written, "since": since,
                "contracts": sorted({x["contract_code"] for x in rows})}


# ── 读取侧:快照(api 服务用) ───────────────────────────────

LAYER_ZH = {"hard_data": "第一层 硬数据", "rates": "第二层 利率与国债",
            "positioning": "第三层 仓位与波动", "credit_cross": "第四层 信用与跨市场"}


def _derive(transform: str, obs: list) -> dict:
    """obs 按日期升序 [(date, value)]。返回最新值、变化量与派生值(同比/环比)。"""
    if not obs:
        return {}
    d, v = obs[-1]
    out = {"date": d, "value": v}
    prev = obs[-2] if len(obs) >= 2 else None
    if prev:
        out["prev_date"], out["prev_value"] = prev
        out["change"] = v - prev[1]
    if transform == "mom_diff" and prev:
        out["derived"] = v - prev[1]
        out["derived_label"] = "环比增加"
    elif transform == "mom_pct" and prev and prev[1]:
        out["derived"] = (v / prev[1] - 1) * 100
        out["derived_label"] = "环比%"
    elif transform == "yoy_pct":
        target = (datetime.fromisoformat(d) - timedelta(days=365)).date().isoformat()
        base = [x for x in obs if x[0] <= target]
        if base and base[-1][1]:
            out["derived"] = (v / base[-1][1] - 1) * 100
            out["derived_label"] = "同比%"
    return out


def build_snapshot(supabase) -> dict:
    series = supabase.table("market_series").select("*").execute().data or []
    since = (date.today() - timedelta(days=800)).isoformat()  # 月度数据发布滞后,同比需要足够回看
    layers: dict = {}
    for s in sorted(series, key=lambda x: (x["layer"], x["priority"] != "tier1", x["id"])):
        obs = (supabase.table("market_observations").select("obs_date,value")
               .eq("series_id", s["id"]).gte("obs_date", since).order("obs_date").limit(2000).execute().data) or []
        pts = [(o["obs_date"], o["value"]) for o in obs]
        item = {"id": s["id"], "name": s["name_zh"], "priority": s["priority"], "frequency": s["frequency"],
                "unit": s["unit"], "source": s["source"], "status": s.get("status"),
                **_derive(s.get("transform") or "level", pts)}
        layers.setdefault(s["layer"], []).append(item)

    today = date.today().isoformat()
    upcoming = (supabase.table("treasury_auctions")
                .select("auction_date,security_type,security_term,offering_amt,reopening")
                .gte("auction_date", today).in_("security_type", ["Note", "Bond", "TIPS", "FRN"])
                .order("auction_date").limit(20).execute().data) or []
    recent = (supabase.table("treasury_auctions")
              .select("auction_date,security_type,security_term,offering_amt,high_yield,bid_to_cover,"
                      "indirect_bidder_accepted,total_accepted")
              .lt("auction_date", today).in_("security_type", ["Note", "Bond"])
              .not_.is_("high_yield", "null").order("auction_date", desc=True).limit(12).execute().data) or []
    for a in recent:
        if a.get("indirect_bidder_accepted") and a.get("total_accepted"):
            a["indirect_pct"] = round(a["indirect_bidder_accepted"] / a["total_accepted"] * 100, 1)
    coupon_30d = sum(a.get("offering_amt") or 0 for a in upcoming
                     if a["auction_date"] <= (date.today() + timedelta(days=30)).isoformat())

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "layers": [{"key": k, "name": LAYER_ZH.get(k, k), "series": layers.get(k, [])}
                   for k in ("hard_data", "rates", "positioning", "credit_cross")],
        "treasury": {"upcoming_coupon_auctions": upcoming, "coupon_supply_next_30d": coupon_30d,
                     "recent_coupon_results": recent},
        "not_available": [
            {"item": "CTA 仓位", "proxy": "VIX、CFTC 杠杆基金净持仓"},
            {"item": "Gamma 头寸", "proxy": "VIX 与 SKEW"},
            {"item": "盘口深度", "proxy": "无公开替代"},
            {"item": "Prime Broker 对冲基金仓位", "proxy": "CFTC 杠杆基金净持仓(周频)"},
            {"item": "杠杆 ETF 资金流", "proxy": "暂无稳定免费源"},
            {"item": "公司回购窗口", "proxy": "暂无稳定免费源(可按财报季规则估算)"},
            {"item": "CDS", "proxy": "高收益债 / 投资级信用利差"},
            {"item": "ISM / PMI", "proxy": "授权数据,未采集"},
        ],
    }
