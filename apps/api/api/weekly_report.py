"""
人格周度市场解读(engine 常驻线程)

personas/*.yaml 里配置了 weekly_report 的人格,每周固定时间自动生成一份市场解读:

  1. 数据:从 market_series / market_observations 算出本周变化(日频序列周变化与四周变化、周频最新两期、
     月频最新一期及同比/环比),加上上周国债拍卖结果、已公布的下周拍卖、CFTC 仓位周变化
  2. 观点:按四层主题检索该人格观点库,带原视频片段引用 [N]
  3. 延续:附上上一期周报的结论,保持观点连贯、承认判断变化
  4. 成文:人格系统提示 + 思维框架 + 数据 + 观点 → DeepSeek pro(审查/失败自动 Claude 兜底)

结果存 weekly_reports,前端 /reports 展示。数字只允许使用提供的数据,不得编造。
"""

import json
import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

LAYER_QUERIES = [
    ("positioning", "短期方向 仓位 CTA 资金流 波动率 VIX 减仓 加仓"),
    ("rates", "美债收益率 长端利率 国债供给 拍卖 期限溢价 美联储"),
    ("hard_data", "非农 就业 通胀 PCE CPI 经济增长 消费"),
    ("credit_cross", "信用利差 系统性风险 油价 美元 对冲"),
    ("allocation", "资产配置 仓位中枢 现金 美股 债券 黄金 建议"),
]

REPORT_PROMPT = """{header}{framework}

=== 你过往讲过的相关观点(可在句尾用 [N] 引用) ===
{viewpoints}

=== 你上一期周报的结论(保持连贯;判断变了就直说为什么变) ===
{previous}

=== 本周数据(截至 {asof},只能使用这里的数字,不要编造任何数据) ===
{data}

=== 任务 ===
你在录每周的「市场概述」。以第一人称、用你平时录视频的口吻,写本周({week_label})的市场解读,Markdown 格式,1500~2500 字。
按你的四层框架组织,先下结论再展开:

## 本周结论
三五句话:短期方向、利率与配置判断、经济大趋势、系统性风险各一句,以及你现在的整体仓位倾向。

## 短期方向:仓位与波动(第三层)
## 利率与国债:配置的中枢(第二层)
## 经济硬数据:大趋势(第一层)
## 信用与跨市场:有没有系统性风险(第四层)
## 下周重点看什么
列出下周要盯的数据和拍卖(只列上面数据里有的,或你框架里固定的周四初请、月初非农等),说清楚各自的判断阈值。

## 仓位与操作思路
给普通投资者的仓位倾向和风险提示,不要给具体个股买卖指令。

要求:
- 每个判断都要落在具体数字上,并说明这个数字相对上周/上月是变好还是变坏
- 数据拿不到的指标(CTA、Gamma、盘口深度等)不要假装有读数,用 VIX、SKEW、CFTC 持仓代替并说明是替代
- 引用你过往观点时句尾加 [N],编号只能用上面观点列表里的
- 不要出现"作为 AI"之类的话,不要写免责声明(页面会统一标注)
"""


def _fmt(v, unit=""):
    if v is None:
        return "—"
    if abs(v) >= 1e8:
        return f"{v / 1e8:,.0f}亿{unit}"
    if abs(v) >= 1e5:
        return f"{v:,.0f}{unit}"
    if abs(v) >= 100:
        return f"{v:,.1f}{unit}"
    return f"{v:.2f}{unit}"


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
    def _week_start(cfg_wr: dict) -> tuple:
        """返回 (本周一日期, 本周计划发布时间是否已到)。按人格配置的时区计算。"""
        try:
            tz = ZoneInfo(cfg_wr.get("tz") or "Asia/Shanghai")
        except Exception:
            tz = timezone(timedelta(hours=8))
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
        try:
            if week_start is None:
                week_start, _ = self._week_start(cfg.get("weekly_report") or {})
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

            data_text, data_json = self._week_data()
            vp_text, citations = self._viewpoints(persona)
            prev = (self.supabase.table("weekly_reports").select("week_start,content")
                    .eq("persona", persona).eq("status", "done").lt("week_start", ws)
                    .order("week_start", desc=True).limit(1).execute().data) or []
            previous = (f"({prev[0]['week_start']} 当周)\n" + _conclusion(prev[0]["content"])) if prev else "(这是第一期)"

            prompt = REPORT_PROMPT.format(
                header=self.persona_header(cfg) + "\n(本次是写周报,不调用任何工具,材料都在下面。)\n",
                framework=self.persona_framework(persona, cfg),
                viewpoints=vp_text or "(暂无)", previous=previous,
                asof=datetime.now(timezone.utc).strftime("%Y-%m-%d"), data=data_text,
                week_label=f"{week_start.isoformat()} 起的一周")

            from config import DEEPSEEK_API_KEY
            from utils.api_client import DeepSeekClient
            client = DeepSeekClient(DEEPSEEK_API_KEY, fallback_on_balance=True)
            content = client.call(prompt, model="deepseek-v4-pro", max_tokens=16000) or ""
            if len(content) < 600:
                raise RuntimeError(f"report too short ({len(content)} chars)")

            import re as _re
            used = {int(x) for x in _re.findall(r"\[(\d+)\]", content)}
            cites = [c for c in citations if c["ref_num"] in used]
            self.supabase.table("weekly_reports").update({
                "status": "done", "content": content, "citations": cites, "data_snapshot": data_json,
                "model": "deepseek-v4-pro", "error": None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("persona", persona).eq("week_start", ws).execute()
            res = {"status": "done", "id": rid, "chars": len(content), "citations": len(cites)}
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

    # ── 本周数据 ──────────────────────────────────────────────
    def _week_data(self) -> tuple:
        series = self.supabase.table("market_series").select("*").execute().data or []
        today = date.today()
        since = (today - timedelta(days=800)).isoformat()
        lines, out = {}, {}
        layer_names = {"hard_data": "第一层 硬数据", "rates": "第二层 利率与国债",
                       "positioning": "第三层 仓位与波动", "credit_cross": "第四层 信用与跨市场"}
        for s in sorted(series, key=lambda x: (x["layer"], x["priority"] != "tier1", x["id"])):
            if s["id"] in ("VIXCLS",):
                continue
            obs = (self.supabase.table("market_observations").select("obs_date,value")
                   .eq("series_id", s["id"]).gte("obs_date", since).order("obs_date")
                   .limit(3000).execute().data) or []
            if not obs:
                continue
            pts = [(o["obs_date"], o["value"]) for o in obs]
            d, v = pts[-1]
            item = {"name": s["name_zh"], "date": d, "value": v, "unit": s.get("unit")}

            def at_or_before(days):
                target = (datetime.fromisoformat(d) - timedelta(days=days)).date().isoformat()
                prior = [p for p in pts if p[0] <= target]
                return prior[-1] if prior else None

            desc = f"{s['name_zh']}:{_fmt(v)}{s.get('unit') or ''}({d})"
            freq, tf = s.get("frequency"), s.get("transform")
            if freq == "daily":
                w, m = at_or_before(7), at_or_before(28)
                if w:
                    item["chg_1w"] = v - w[1]
                    desc += f",周变化 {v - w[1]:+.2f}"
                if m:
                    item["chg_4w"] = v - m[1]
                    desc += f",四周变化 {v - m[1]:+.2f}"
            elif len(pts) >= 2:
                pd_, pv = pts[-2]
                if tf == "mom_diff":
                    item["mom_diff"] = v - pv
                    prev_diff = (pv - pts[-3][1]) if len(pts) >= 3 else None
                    desc = f"{s['name_zh']}:环比增加 {_fmt(v - pv)}{s.get('unit') or ''}({d}" + \
                           (f",前值 {_fmt(prev_diff)})" if prev_diff is not None else ")")
                elif tf == "mom_pct" and pv:
                    item["mom_pct"] = (v / pv - 1) * 100
                    desc += f",环比 {(v / pv - 1) * 100:+.2f}%"
                elif tf == "yoy_pct":
                    base = at_or_before(365)
                    base_prev = [p for p in pts if p[0] <= (datetime.fromisoformat(pd_) - timedelta(days=365)).date().isoformat()]
                    if base and base[1]:
                        yoy = (v / base[1] - 1) * 100
                        item["yoy"] = yoy
                        desc = f"{s['name_zh']}:同比 {yoy:.2f}%({d}"
                        if base_prev and base_prev[-1][1]:
                            desc += f",前值 {(pv / base_prev[-1][1] - 1) * 100:.2f}%"
                        desc += ")"
                else:
                    item["prev"] = pv
                    desc += f",前值 {_fmt(pv)}({pd_})"
            lines.setdefault(s["layer"], []).append(desc)
            out[s["id"]] = item

        # 国债:上周结果 + 已公布的下周拍卖
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
            ind = (a["indirect_bidder_accepted"] / a["total_accepted"] * 100) if a.get("indirect_bidder_accepted") and a.get("total_accepted") else None
            tl.append(f"已拍 {a['auction_date']} {a['security_term']} {a['security_type']}:规模 {_fmt(a.get('offering_amt'))}美元,"
                      f"中标 {a.get('high_yield') or '—'}%,认购倍数 {a.get('bid_to_cover') or '—'}"
                      + (f",间接投标(海外等)占比 {ind:.1f}%" if ind else ""))
        for a in upcoming:
            tl.append(f"已公布待拍 {a['auction_date']} {a['security_term']} {a['security_type']}:规模 {_fmt(a.get('offering_amt'))}美元")
        if not tl:
            tl.append("本周与下周无附息国债拍卖记录(下周拍卖通常提前约一周公布)")
        lines.setdefault("rates", []).extend(tl)
        out["treasury"] = {"recent": recent, "upcoming": upcoming}

        lines.setdefault("positioning", []).append(
            "拿不到直接读数:CTA 仓位、Gamma 头寸、盘口深度、Prime Broker 仓位、杠杆 ETF 资金流、回购窗口;"
            "用 VIX、SKEW、CFTC 杠杆基金/资产管理人净持仓替代")
        lines.setdefault("credit_cross", []).append("CDS 拿不到,用高收益债/投资级信用利差替代")

        text = "\n\n".join(f"【{layer_names[k]}】\n" + "\n".join(f"- {x}" for x in lines[k])
                           for k in ("positioning", "rates", "hard_data", "credit_cross") if k in lines)
        return text, out

    # ── 观点检索 ──────────────────────────────────────────────
    def _viewpoints(self, persona: str) -> tuple:
        seen, lines, citations = set(), [], []
        for _, q in LAYER_QUERIES:
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


def _conclusion(content: str) -> str:
    """取上一期「本周结论」段落,没有就取开头。"""
    c = content or ""
    i = c.find("本周结论")
    if i >= 0:
        seg = c[i:]
        j = seg.find("\n## ", 5)
        return seg[: j if j > 0 else 900][:900]
    return c[:700]
