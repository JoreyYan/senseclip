"""
人格周度市场解读(engine 常驻线程)

personas/*.yaml 里配置了 weekly_report 的人格,每周固定时间自动生成一份市场解读:

  1. 数据:由程序算好每个指标的最新值、对比期、变化量和「方向」(上行/下行、净空头扩大/收窄、曲线变平/变陡),
     日期一律带星期;另附已公布的国债拍卖与按惯例推算的初请/非农发布日。模型不自己判断方向和日期。
  2. 观点:按四层主题检索该人格观点库,带原视频片段引用 [N]
  3. 延续:附上上一期周报的结论
  4. 成文:人格系统提示 + 思维框架 + 数据 + 观点 → DeepSeek pro
  5. 核对:把初稿和原始数据再交给模型逐条核对数字、方向、日期,删掉数据里没有的预期/新闻,输出修正稿

结果存 weekly_reports,前端 /reports 展示。
"""

import logging
import re
import threading
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

WEEKDAY_ZH = "一二三四五六日"

LAYER_QUERIES = [
    "短期方向 仓位 CTA 资金流 波动率 VIX 减仓 加仓",
    "美债收益率 长端利率 国债供给 拍卖 期限溢价 美联储",
    "非农 就业 通胀 PCE CPI 经济增长 消费",
    "信用利差 系统性风险 油价 美元 对冲",
    "资产配置 仓位中枢 现金 美股 债券 黄金 建议",
]

RATE_UNITS = ("%", "百分点")

RULES = """硬性规则(违反任何一条都算错):
1. 数字只能来自「数据」部分,原样使用;不得出现数据里没有的数字。
2. 不得提及市场预期、一致预期、分析师预测、新闻事件、地缘事件名称——数据里没有这些。
3. 变化方向以数据里写明的方向词为准(上行/下行、增加/减少、净空头扩大/收窄、曲线变平/变陡),不得自行推断相反方向。
4. 日期与星期只能用数据和日历里给出的;没有给出日期的数据(如 CPI、PCE 的下次发布)不得写具体日期。
5. 收益率、利差的变化用 bp 表述(1bp = 0.01 个百分点);不得做年化、折年换算,不得说"持仓 × 价格"之类数据里没有的推导。
6. 全文中文,不夹英文缩写评级(如 LOW/HIGH);专有名词(VIX、CFTC、PCE 等)可保留。
7. 不写"作为 AI"、不写免责声明、不写"我这里只有数据"之类出戏的话。"""

REPORT_PROMPT = """{header}{framework}

=== 你过往讲过的相关观点(句尾用 [N] 引用,编号只能用这里的) ===
{viewpoints}

=== 你上一期周报的结论(保持连贯;判断变了就直说为什么变) ===
{previous}

=== 日历 ===
{calendar}

=== 数据(截至 {asof}) ===
{data}

=== 任务 ===
今天是 {today_label}。你在录每周的「市场概述」:回顾过去一周,展望接下来一周。
以第一人称、用你平时录视频的口吻写,Markdown,1500~2500 字。按你的四层框架组织,先下结论再展开:

## 本周结论
三五句话:短期方向、利率与配置、经济大趋势、系统性风险各一句,以及整体仓位倾向。
## 短期方向:仓位与波动(第三层)
## 利率与国债:配置的中枢(第二层)
## 经济硬数据:大趋势(第一层)
## 信用与跨市场:有没有系统性风险(第四层)
## 接下来一周看什么
只列日历里有的事件,写清日期、星期和你的判断阈值。
## 仓位与操作思路
给普通投资者的仓位倾向和风险提示,不给具体个股买卖指令。

每个判断都要落在具体数字上,并说清相对上期是变好还是变坏。
拿不到读数的指标(CTA、Gamma、盘口深度等)不要假装有数,说明用 VIX、SKEW、CFTC 持仓替代。

{rules}
"""

AUDIT_PROMPT = """你是严格的财经编辑。下面是一篇以博主口吻写的周度市场解读初稿,以及它唯一允许使用的数据和日历。
逐句核对,修正所有问题后输出**完整的修正稿**(只输出正文 Markdown,不要任何说明):

- 每个数字必须能在数据/日历里找到且一致;找不到的数字连同所在的论断一起删掉或改为定性描述
- 每个变化方向必须与数据里的方向词一致(特别注意:利差收窄=曲线变平;净空头减少=空头收窄)
- 日期与星期必须与日历/数据一致;日历里没有的事件不得写日期
- 删掉市场预期、一致预期、新闻/地缘事件、年化换算、数据里没有的推导
- 保留原文的口吻、结构、观点和 [N] 引用标记,不要改写成官腔,不要加免责声明

=== 日历 ===
{calendar}

=== 数据 ===
{data}

{rules}

=== 初稿 ===
{draft}
"""


def _wd(d) -> str:
    if isinstance(d, str):
        d = datetime.fromisoformat(d[:10]).date()
    return f"{d.month}月{d.day}日(周{WEEKDAY_ZH[d.weekday()]})"


def _period(d: str, freq: str) -> str:
    dt = datetime.fromisoformat(d[:10]).date()
    if freq == "monthly":
        return f"{dt.year}年{dt.month}月"
    if freq == "quarterly":
        return f"{dt.year}年第{(dt.month - 1) // 3 + 1}季度"
    if freq == "weekly":
        return f"截至{_wd(dt)}的一周"
    return _wd(dt)


def _num(v: float, unit: str = "") -> str:
    """人读数字:人数/金额转万、亿。"""
    if v is None:
        return "—"
    if unit in ("千人", "千个"):
        v, unit = v * 1000, unit[1:]
    if unit in ("人", "个", "张", "美元", "百万美元"):
        base = v * 1e6 if unit == "百万美元" else v
        u = "美元" if unit in ("美元", "百万美元") else unit
        if abs(base) >= 1e8:
            return f"{base / 1e8:,.1f}亿{u}"
        if abs(base) >= 1e4:
            return f"{base / 1e4:,.1f}万{u}"
        return f"{base:,.0f}{u}"
    if unit == "%":
        return f"{v:.2f}%"
    if unit == "百分点":
        return f"{v:.2f}个百分点"
    return f"{v:,.2f}{unit}"


def _updown(delta: float, up="上行", down="下行", flat="持平") -> str:
    if delta is None or abs(delta) < 1e-9:
        return flat
    return up if delta > 0 else down


class WeeklyReporter:
    def __init__(self, supabase, load_personas, persona_header, persona_framework, embed_text):
        self.supabase = supabase
        self.load_personas = load_personas
        self.persona_header = persona_header
        self.persona_framework = persona_framework
        self.embed_text = embed_text
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self.status: dict = {"running": False, "phase": "idle", "last_check": "", "last_result": None}

    # ── 调度 ──────────────────────────────────────────────────
    def start(self) -> dict:
        if self._thread and self._thread.is_alive():
            return {"status": "already_running"}
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.status["running"] = True
        return {"status": "started"}

    def _loop(self) -> None:
        if self._stop.wait(300):  # 等市场数据采集器先跑完首轮
            return
        while not self._stop.is_set():
            try:
                self.check_due()
            except Exception as e:
                logger.error(f"[weekly] check failed: {e}")
            if self._stop.wait(1800):
                break
        self.status["running"] = False

    @staticmethod
    def _tz(cfg_wr: dict):
        try:
            return ZoneInfo(cfg_wr.get("tz") or "Asia/Shanghai")
        except Exception:
            return timezone(timedelta(hours=8))

    @classmethod
    def _week_start(cls, cfg_wr: dict) -> tuple:
        """返回 (本周一日期, 本周计划发布时间是否已到)。"""
        tz = cls._tz(cfg_wr)
        now = datetime.now(tz)
        monday = (now - timedelta(days=now.weekday())).date()
        due = datetime(monday.year, monday.month, monday.day, int(cfg_wr.get("hour", 9)), tzinfo=tz) \
            + timedelta(days=int(cfg_wr.get("weekday", 0)))
        return monday, now >= due

    def check_due(self) -> None:
        self.status["last_check"] = datetime.now(timezone.utc).isoformat()
        for key, cfg in self.load_personas().items():
            wr = cfg.get("weekly_report")
            if not wr or cfg.get("hidden"):
                continue
            monday, due = self._week_start(wr if isinstance(wr, dict) else {})
            if not due:
                continue
            exists = (self.supabase.table("weekly_reports").select("id,status")
                      .eq("persona", key).eq("week_start", monday.isoformat()).execute().data) or []
            if exists and exists[0]["status"] == "done":
                continue
            self.generate(key, cfg, monday)

    # ── 生成 ──────────────────────────────────────────────────
    def generate(self, persona: str, cfg: dict, week_start: date = None, force: bool = False) -> dict:
        if not self._lock.acquire(blocking=False):
            return {"status": "busy"}
        wr = cfg.get("weekly_report") if isinstance(cfg.get("weekly_report"), dict) else {}
        try:
            if week_start is None:
                week_start, _ = self._week_start(wr)
            ws = week_start.isoformat()
            self.status["phase"] = f"generating {persona} {ws}"
            existing = (self.supabase.table("weekly_reports").select("id,status")
                        .eq("persona", persona).eq("week_start", ws).execute().data) or []
            if existing and existing[0]["status"] == "done" and not force:
                return {"status": "exists", "id": existing[0]["id"]}
            row = {"persona": persona, "week_start": ws, "status": "running",
                   "title": f"{cfg['label']} · {week_start.month}月{week_start.day}日当周市场解读",
                   "updated_at": datetime.now(timezone.utc).isoformat()}
            rid = (self.supabase.table("weekly_reports").upsert(row, on_conflict="persona,week_start")
                   .execute().data or [{}])[0].get("id")

            today = datetime.now(self._tz(wr)).date()
            data_text, data_json = self._week_data()
            calendar_text = self._calendar(today, data_json)
            vp_text, citations = self._viewpoints(persona)
            prev = (self.supabase.table("weekly_reports").select("week_start,content")
                    .eq("persona", persona).eq("status", "done").lt("week_start", ws)
                    .order("week_start", desc=True).limit(1).execute().data) or []
            previous = (f"({prev[0]['week_start']} 当周)\n" + _conclusion(prev[0]["content"])) if prev else "(这是第一期)"

            from config import DEEPSEEK_API_KEY
            from utils.api_client import DeepSeekClient
            client = DeepSeekClient(DEEPSEEK_API_KEY, fallback_on_balance=True)

            self.status["phase"] = f"drafting {persona} {ws}"
            draft = client.call(REPORT_PROMPT.format(
                header=self.persona_header(cfg) + "\n(本次是写周报,不调用任何工具,材料都在下面。)\n",
                framework=self.persona_framework(persona, cfg),
                viewpoints=vp_text or "(暂无)", previous=previous, calendar=calendar_text,
                asof=_wd(today), data=data_text, today_label=_wd(today), rules=RULES,
            ), model="deepseek-v4-pro", max_tokens=16000) or ""
            if len(draft) < 600:
                raise RuntimeError(f"draft too short ({len(draft)} chars)")

            self.status["phase"] = f"auditing {persona} {ws}"
            content, audited_ok = draft, False
            try:
                audited = client.call(AUDIT_PROMPT.format(calendar=calendar_text, data=data_text,
                                                          rules=RULES, draft=draft),
                                      model="deepseek-v4-pro", max_tokens=16000) or ""
                # 核对稿必须保持结构完整,否则保留初稿
                if len(audited) >= len(draft) * 0.6 and "本周结论" in audited:
                    content, audited_ok = audited, True
                else:
                    logger.warning(f"[weekly] audit output rejected ({len(audited)} chars)")
            except Exception as e:
                logger.warning(f"[weekly] audit failed, keeping draft: {str(e)[:120]}")
            content = re.sub(r"^```(?:markdown)?\s*|\s*```\s*$", "", content.strip())

            used = {int(x) for x in re.findall(r"\[(\d+)\]", content)}
            cites = [c for c in citations if c["ref_num"] in used]
            self.supabase.table("weekly_reports").update({
                "status": "done", "content": content, "citations": cites, "data_snapshot": data_json,
                "model": "deepseek-v4-pro" + ("+audit" if audited_ok else ""), "error": None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("persona", persona).eq("week_start", ws).execute()
            res = {"status": "done", "id": rid, "chars": len(content), "draft_chars": len(draft),
                   "citations": len(cites), "audited": audited_ok}
            logger.info(f"[weekly] {persona} {ws}: {res}")
            self.status["last_result"] = {"persona": persona, "week_start": ws, **res}
            return res
        except Exception as e:
            logger.error(f"[weekly] {persona} failed: {e}")
            try:
                self.supabase.table("weekly_reports").update({
                    "status": "error", "error": str(e)[:300],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }).eq("persona", persona).eq("week_start", (week_start or date.today()).isoformat()).execute()
            except Exception:
                pass
            self.status["last_result"] = {"persona": persona, "status": "error", "error": str(e)[:200]}
            return {"status": "error", "error": str(e)[:300]}
        finally:
            self.status["phase"] = "idle"
            self._lock.release()

    # ── 数据:程序算好数值、对比期、变化与方向 ────────────────
    def _week_data(self) -> tuple:
        series = self.supabase.table("market_series").select("*").execute().data or []
        today = date.today()
        since = (today - timedelta(days=800)).isoformat()
        lines, out = {}, {}
        layer_names = {"hard_data": "第一层 硬数据", "rates": "第二层 利率与国债",
                       "positioning": "第三层 仓位与波动", "credit_cross": "第四层 信用与跨市场"}
        for s in sorted(series, key=lambda x: (x["layer"], x["priority"] != "tier1", x["id"])):
            if s["id"] == "VIXCLS":
                continue
            obs = (self.supabase.table("market_observations").select("obs_date,value")
                   .eq("series_id", s["id"]).gte("obs_date", since).order("obs_date")
                   .limit(3000).execute().data) or []
            if not obs:
                continue
            pts = [(o["obs_date"], o["value"]) for o in obs]
            desc, item = _describe(s, pts)
            if desc:
                lines.setdefault(s["layer"], []).append(desc)
                out[s["id"]] = item

        # 国债拍卖:过去 8 天结果 + 已公布待拍
        week_ago = (today - timedelta(days=8)).isoformat()
        recent = (self.supabase.table("treasury_auctions")
                  .select("auction_date,security_type,security_term,offering_amt,high_yield,bid_to_cover,"
                          "indirect_bidder_accepted,total_accepted")
                  .gte("auction_date", week_ago).lt("auction_date", today.isoformat())
                  .in_("security_type", ["Note", "Bond", "TIPS"]).order("auction_date").execute().data) or []
        upcoming = (self.supabase.table("treasury_auctions")
                    .select("auction_date,security_type,security_term,offering_amt")
                    .gte("auction_date", today.isoformat()).in_("security_type", ["Note", "Bond", "TIPS", "FRN"])
                    .order("auction_date").limit(15).execute().data) or []
        tl = []
        for a in recent:
            ind = (a["indirect_bidder_accepted"] / a["total_accepted"] * 100) \
                if a.get("indirect_bidder_accepted") and a.get("total_accepted") else None
            a["indirect_pct"] = round(ind, 1) if ind else None
            tl.append(f"已完成拍卖 {_wd(a['auction_date'])} {_term(a)}:规模 {_num(a.get('offering_amt'), '美元')},"
                      f"中标利率 {a.get('high_yield') if a.get('high_yield') is not None else '—'}%,"
                      f"认购倍数 {a.get('bid_to_cover') if a.get('bid_to_cover') is not None else '—'}"
                      + (f",间接投标(含海外)占比 {ind:.1f}%" if ind else ""))
        if not recent:
            tl.append("过去一周没有附息国债拍卖")
        lines.setdefault("rates", []).extend(tl)
        out["treasury"] = {"recent": recent, "upcoming": upcoming}

        lines.setdefault("positioning", []).append(
            "拿不到直接读数:CTA 仓位、Gamma 头寸、盘口深度、Prime Broker 仓位、杠杆 ETF 资金流、回购窗口;"
            "可用替代:VIX、SKEW、CFTC 杠杆基金/资产管理人净持仓")
        lines.setdefault("credit_cross", []).append("CDS 拿不到,用高收益债/投资级信用利差替代")

        text = "\n\n".join(f"【{layer_names[k]}】\n" + "\n".join(f"- {x}" for x in lines[k])
                           for k in ("positioning", "rates", "hard_data", "credit_cross") if k in lines)
        return text, out

    def _calendar(self, today: date, data_json: dict) -> str:
        items = [f"今天:{_wd(today)}"]
        # 初请:每周四;非农:通常每月第一个周五(按惯例推算,以官方日历为准)
        thu = today + timedelta(days=(3 - today.weekday()) % 7)
        for d in (thu, thu + timedelta(days=7)):
            items.append(f"{_wd(d)}:初请失业金(每周四,按惯例推算)")
        nfp = _first_friday(today.year, today.month)
        if nfp < today:
            ny, nm = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
            nfp = _first_friday(ny, nm)
        items.append(f"{_wd(nfp)}:非农就业报告(通常每月第一个周五,按惯例推算,以官方日历为准)")
        upcoming = (data_json.get("treasury") or {}).get("upcoming") or []
        for a in upcoming:
            items.append(f"{_wd(a['auction_date'])}:国债拍卖 {_term(a)},规模 {_num(a.get('offering_amt'), '美元')}(已由财政部公布)")
        if not upcoming:
            items.append("财政部尚未公布更多附息国债拍卖(通常提前约一周公布)")
        items.append("CPI、PCE、零售销售、JOLTS 的下次发布日期:未采集,不写具体日期")
        return "\n".join(f"- {x}" for x in items)

    # ── 观点检索 ──────────────────────────────────────────────
    def _viewpoints(self, persona: str) -> tuple:
        seen, lines, citations = set(), [], []
        for q in LAYER_QUERIES:
            try:
                qv = self.embed_text(q)
                rows = self.supabase.rpc("match_viewpoints", {
                    "p_persona": persona, "query_embedding": qv, "match_count": 6}).execute().data or []
            except Exception as e:
                logger.warning(f"[weekly] viewpoint search failed: {str(e)[:80]}")
                continue
            for r in rows:
                vkey = (r.get("topic"), r.get("stance"))
                if vkey in seen or (r.get("similarity") or 0) < 0.3:
                    continue
                seen.add(vkey)
                aid = (r.get("atom_ids") or [None])[0]
                tag = ""
                if aid:
                    a = (self.supabase.table("atoms").select("id,merged_text,video_id,start_ms,end_ms")
                         .eq("id", aid).limit(1).execute().data) or []
                    if a:
                        citations.append({"ref_num": len(citations) + 1, "atom_id": aid,
                                          "video_id": a[0].get("video_id") or "",
                                          "text": (a[0].get("merged_text") or "")[:400],
                                          "start_ms": a[0].get("start_ms") or 0, "end_ms": a[0].get("end_ms")})
                        tag = f" [{citations[-1]['ref_num']}]"
                lines.append(f"- {r.get('topic')} | {r.get('stance')} | 理由:{r.get('reasoning') or ''}{tag}")
        return "\n".join(lines[:30]), citations


# ── 单个序列的描述(纯函数,便于测试) ─────────────────────────

def _describe(s: dict, pts: list) -> tuple:
    sid, name, unit = s["id"], s["name_zh"], s.get("unit") or ""
    freq, tf = s.get("frequency"), s.get("transform") or "level"
    d, v = pts[-1]
    item = {"name": name, "date": d, "value": v, "unit": unit}
    is_rate = unit in RATE_UNITS

    def at_or_before(ref_date: str, days: int):
        target = (datetime.fromisoformat(ref_date) - timedelta(days=days)).date().isoformat()
        prior = [p for p in pts if p[0] <= target]
        return prior[-1] if prior else None

    def yoy_at(i: int):
        dd, vv = pts[i]
        base = at_or_before(dd, 365)
        return (vv / base[1] - 1) * 100 if base and base[1] else None

    if freq == "daily":
        w, m = at_or_before(d, 7), at_or_before(d, 28)
        desc = f"{name}:{_num(v, unit)}({_wd(d)})"
        for label, ref, key in (("一周前", w, "chg_1w"), ("四周前", m, "chg_4w")):
            if not ref:
                continue
            delta = v - ref[1]
            item[key] = delta
            if is_rate:
                desc += f";较{label}({_wd(ref[0])} {_num(ref[1], unit)}){delta * 100:+.0f}bp,{_updown(delta)}"
            else:
                pct = f"({delta / ref[1] * 100:+.1f}%)" if ref[1] else ""
                desc += f";较{label}({_wd(ref[0])} {_num(ref[1], unit)}){delta:+,.2f}{pct},{_updown(delta, '上涨', '下跌')}"
        if sid == "T10Y2Y" and "chg_1w" in item:
            desc += ";" + ("利差走阔,曲线变陡" if item["chg_1w"] > 0 else
                           "利差收窄,曲线变平" if item["chg_1w"] < 0 else "曲线形态不变")
        return desc, item

    if len(pts) < 2:
        return f"{name}:{_num(v, unit)}({_period(d, freq)})", item
    pd_, pv = pts[-2]

    if sid.startswith("COT_"):
        delta = v - pv
        item.update({"prev": pv, "change": delta})
        side = "净多头" if v > 0 else "净空头"
        if (v > 0) != (pv > 0):
            trend = f"由{'净多头' if pv > 0 else '净空头'}翻转为{side}"
        elif v < 0:
            trend = f"净空头{'扩大' if delta < 0 else '收窄'} {abs(delta):,.0f} 张"
        else:
            trend = f"净多头{'增加' if delta > 0 else '减少'} {abs(delta):,.0f} 张"
        return f"{name}:{v:,.0f} 张({side},报告日 {_wd(d)});上期 {pv:,.0f} 张({_wd(pd_)});{trend}", item

    if tf == "mom_diff":
        cur = v - pv
        prev_diff = (pv - pts[-3][1]) if len(pts) >= 3 else None
        item.update({"mom_diff": cur, "prev_mom_diff": prev_diff})
        desc = f"{name}:{_period(d, freq)}环比{'增加' if cur >= 0 else '减少'} {_num(abs(cur), unit)}"
        if prev_diff is not None:
            desc += f";上月({_period(pd_, freq)})环比{'增加' if prev_diff >= 0 else '减少'} {_num(abs(prev_diff), unit)}"
            desc += f";增量{'扩大' if cur > prev_diff else '缩小'}"
        return desc, item

    if tf == "yoy_pct":
        cur, prev_ = yoy_at(len(pts) - 1), yoy_at(len(pts) - 2)
        if cur is None:
            return f"{name}:{_period(d, freq)}指数 {v:,.2f}", item
        item.update({"yoy": cur, "prev_yoy": prev_})
        desc = f"{name}:{_period(d, freq)}同比 {cur:.2f}%"
        if prev_ is not None:
            diff = cur - prev_
            desc += f";上月同比 {prev_:.2f}%;{_updown(diff, '升温', '降温')} {abs(diff):.2f} 个百分点"
        return desc, item

    if tf == "mom_pct" and pv:
        cur = (v / pv - 1) * 100
        prev_ = (pv / pts[-3][1] - 1) * 100 if len(pts) >= 3 and pts[-3][1] else None
        item.update({"mom_pct": cur, "prev_mom_pct": prev_})
        desc = f"{name}:{_period(d, freq)}环比 {cur:+.2f}%"
        if prev_ is not None:
            desc += f";上月环比 {prev_:+.2f}%"
        return desc, item

    delta = v - pv
    item.update({"prev": pv, "change": delta})
    if is_rate:
        chg = f"{delta * 100:+.0f}bp" if unit == "百分点" else f"{delta:+.2f} 个百分点"
    else:
        chg = f"{'增加' if delta >= 0 else '减少'} {_num(abs(delta), unit)}"
    return (f"{name}:{_num(v, unit)}({_period(d, freq)});上期 {_num(pv, unit)}({_period(pd_, freq)});"
            f"{chg},{_updown(delta)}"), item


def _term(a: dict) -> str:
    t = {"Note": "中期国债", "Bond": "长期国债", "TIPS": "通胀保值国债", "FRN": "浮息国债"}.get(
        a.get("security_type"), a.get("security_type") or "")
    term = (a.get("security_term") or "").replace("-Year", "年期").replace("-Month", "个月").replace(" ", "")
    return f"{term}{t}"


def _first_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    return d + timedelta(days=(4 - d.weekday()) % 7)


def _conclusion(content: str) -> str:
    """取上一期「本周结论」段落,没有就取开头。"""
    c = content or ""
    i = c.find("本周结论")
    if i >= 0:
        seg = c[i:]
        j = seg.find("\n## ", 5)
        return seg[: j if j > 0 else 900][:900]
    return c[:700]
