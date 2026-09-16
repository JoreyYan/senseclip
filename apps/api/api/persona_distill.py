"""
人格思维框架蒸馏(服务端,通用版)

与 tools/distill 的本地脚本同一思路,但对任意人格可用、可增量:
  Phase B: 每个视频的叙事片段(标题+摘要)→ DeepSeek flash 提取「分析路径」JSON
           结果缓存在 app_settings.persona_distill_<key>,新视频入库后只提取新增部分
  Phase C: 汇总全部提取 → DeepSeek pro 合成《<博主>思维框架》Markdown
           写入 app_settings.<framework_key>,人格运行时作为系统提示核心

DeepSeek 审查拒答时由 DeepSeekClient 自动切 Claude 兜底。
"""

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

EXTRACT_PROMPT = """你在研究博主「{label}」的思维方式。博主简介:{desc}
以下是他一期{source_word}的分段标题和摘要。

标题: {title}

分段内容:
{segs}

请提取这期内容体现的「分析路径」,严格输出 JSON:
{{
  "topic": "本期核心议题(一句)",
  "entry_angle": "他切入问题的角度",
  "reasoning_chain": ["推理链步骤1", "步骤2"],
  "heuristics": ["本期用到的判断法则/经验规律,一句一条"],
  "evidence_style": "他如何使用证据和数据",
  "conclusion_pattern": "结论的落点方式(明确给判断/给操作建议/留开放问题)",
  "worldview_hints": ["体现的世界观或底层假设"],
  "expression": ["有辨识度的口头禅、说法或表达习惯(原话,没有就留空)"]
}}
只输出 JSON。"""

SYNTH_PROMPT = """你是认知建模专家。以下是对博主「{label}」({desc}){n} 期内容逐期提取的分析路径数据(JSON 数组)。

{data}

请合成一份《{label}思维框架》文档(Markdown)。它会作为 AI agent 的人格核心,让 agent 能以第一人称、
按他的思维方式回答**全新的问题**。要求归纳出**可操作的思维规则**,不要空泛描述,不要编造数据里没有的立场。结构:

# {label}思维框架

## 1. 世界观公理(他默认成立的底层假设,按置信度排序)
## 2. 分析入口选择规则(遇到 X 类问题,从 Y 切入;覆盖他最常碰到的几类问题)
## 3. 推理模式库(每种模式:名称、适用场景、步骤、示例)
## 4. 证据与数据的使用规则
## 5. 常用启发式清单(一行一条,合并去重,保留最高频的 20-30 条)
## 6. 表达风格(语气、口头禅、确定度分级的说法、给建议的方式)
## 7. 局限与偏差(他的盲区,agent 应意识到但不必修正)

直接输出文档。"""


def _cache_key(persona: str) -> str:
    return f"persona_distill_{persona}"


def _load_json_setting(supabase, key: str) -> dict:
    try:
        r = supabase.table("app_settings").select("value").eq("key", key).execute()
        if r.data and r.data[0].get("value"):
            return json.loads(r.data[0]["value"]) or {}
    except Exception as e:
        logger.warning(f"[distill] load {key} failed: {e}")
    return {}


def _save_setting(supabase, key: str, value: str) -> None:
    supabase.table("app_settings").upsert({"key": key, "value": value}).execute()


def distill_framework(supabase, persona: str, cfg: dict, stop_event=None, status: dict = None) -> dict:
    """增量提取 + 合成框架。返回 {"videos": n_extracted, "framework_chars": m}。"""
    from config import DEEPSEEK_API_KEY
    from utils.api_client import DeepSeekClient

    status = status if status is not None else {}
    client = DeepSeekClient(DEEPSEEK_API_KEY, fallback_on_balance=True)
    label = cfg.get("label") or persona
    desc = cfg.get("desc") or ""
    source_word = cfg.get("source_word") or "内容"

    vids = {r["id"]: (r.get("title") or r["id"]) for r in
            (supabase.table("videos").select("id,title").in_("channel", cfg["channels"]).execute().data or [])}
    if not vids:
        return {"videos": 0, "framework_chars": 0}

    cache = _load_json_setting(supabase, _cache_key(persona))
    extracted: dict = cache.get("videos") or {}
    todo = [v for v in vids if v not in extracted]
    status["phase"] = f"distill: extracting {len(todo)} new videos"
    logger.info(f"[distill] {persona}: {len(extracted)} cached, {len(todo)} to extract")

    def _extract(vid: str):
        try:
            return _extract_inner(vid)
        except Exception as e:
            logger.warning(f"[distill] extract {vid} failed: {str(e)[:100]}")
            return vid, None

    def _extract_inner(vid: str):
        segs = (supabase.table("narrative_segments").select("title,summary")
                .eq("video_id", vid).order("start_ms").limit(30).execute().data) or []
        if not segs:
            return vid, None
        seg_text = "\n".join(f"- {s.get('title') or ''}: {(s.get('summary') or '')[:400]}" for s in segs[:12])
        out = client.call(EXTRACT_PROMPT.format(label=label, desc=desc, source_word=source_word,
                                                title=vids[vid], segs=seg_text),
                          model="deepseek-v4-flash", max_tokens=8000)
        m = re.search(r"\{.*\}", out or "", re.S)
        if not m:
            return vid, None
        try:
            return vid, json.loads(m.group(0))
        except Exception:
            return vid, None

    for i in range(0, len(todo), 12):
        if stop_event is not None and stop_event.is_set():
            break
        chunk = todo[i:i + 12]
        with ThreadPoolExecutor(max_workers=4) as pool:
            for vid, data in pool.map(_extract, chunk):
                if data:
                    extracted[vid] = {
                        "topic": data.get("topic", ""), "entry": data.get("entry_angle", ""),
                        "heur": data.get("heuristics", []), "evid": data.get("evidence_style", ""),
                        "world": data.get("worldview_hints", []), "concl": data.get("conclusion_pattern", ""),
                        "expr": data.get("expression", []),
                    }
        cache["videos"] = extracted
        _save_setting(supabase, _cache_key(persona), json.dumps(cache, ensure_ascii=False))
        status["phase"] = f"distill: extracted {len(extracted)}/{len(vids)}"

    if not extracted:
        return {"videos": 0, "framework_chars": 0}

    status["phase"] = f"distill: synthesizing from {len(extracted)} videos"
    data_str = json.dumps(list(extracted.values()), ensure_ascii=False)
    if len(data_str) > 90000:
        data_str = data_str[:90000] + "...]"
    framework = client.call(SYNTH_PROMPT.format(label=label, desc=desc, n=len(extracted), data=data_str),
                            model="deepseek-v4-pro", max_tokens=16000)
    if not framework or len(framework) < 500:
        raise RuntimeError(f"framework synthesis returned {len(framework or '')} chars")
    fkey = cfg.get("framework_key") or f"{persona}_framework_v1"
    _save_setting(supabase, fkey, framework)
    logger.info(f"[distill] {persona}: framework saved to {fkey} ({len(framework)} chars, {len(extracted)} videos)")
    return {"videos": len(extracted), "framework_chars": len(framework)}
