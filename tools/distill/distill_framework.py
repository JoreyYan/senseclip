import os
# -*- coding: utf-8 -*-
"""
鲁社长思维框架蒸馏 v1

Phase A: 经 Railway apply-sql 拉全部 narrative_segments + 视频标题
Phase B: 每个视频 → DeepSeek 提取「分析路径」(并发)
Phase C: 聚合全部提取结果 → DeepSeek-pro 合成思维框架文档
产出: framework-v1.md (人格插件内容) + per_video.jsonl (中间结果,可增量)
"""
import sys, os, json, time, requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

API = os.environ.get("SENSECLIP_API_URL", "http://localhost:8000")
ADMIN_KEY = os.environ["SUPABASE_KEY"]
DS_KEY = os.environ["DEEPSEEK_API_KEY"]
OUT_DIR = Path(r"D:\code\youtube\lushezhang-harness\distill")
PER_VIDEO = OUT_DIR / "per_video.jsonl"
FRAMEWORK = OUT_DIR / "framework-v1.md"

def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

def sql(q, retries=5):
    for i in range(retries):
        try:
            r = requests.post(f"{API}/api/admin/apply-sql", json={"sql": q},
                              headers={"X-Admin-Key": ADMIN_KEY}, timeout=120)
            if r.ok:
                return r.json().get("rows") or []
        except Exception as e:
            log(f"  sql retry {i+1}: {str(e)[:50]}")
        time.sleep(5)
    return []

def deepseek(prompt, model="deepseek-v4-flash", max_tokens=4000, retries=4):
    for i in range(retries):
        try:
            r = requests.post("https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {DS_KEY}"},
                json={"model": model, "max_tokens": max_tokens,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=300)
            if r.ok:
                return r.json()["choices"][0]["message"]["content"]
            log(f"  ds {r.status_code}: {r.text[:80]}")
        except Exception as e:
            log(f"  ds retry {i+1}: {str(e)[:50]}")
        time.sleep(10)
    return None

# ── Phase A: 拉数据 ────────────────────────────────────────────
log("Phase A: fetching segments...")
videos = {}
for row in sql("SELECT id, title FROM videos"):
    videos[row[0]] = row[1] or row[0]
log(f"  videos: {len(videos)}")

segments = []
offset = 0
while True:
    rows = sql(f"SELECT video_id, title, summary FROM narrative_segments ORDER BY id LIMIT 100 OFFSET {offset}")
    if not rows:
        break
    segments.extend(rows)
    offset += 100
    if len(rows) < 100:
        break
log(f"  segments: {len(segments)}")

by_video = {}
for vid, title, summary in segments:
    by_video.setdefault(vid, []).append((title or "", summary or ""))
log(f"  videos with segments: {len(by_video)}")

# ── Phase B: 逐视频提取 ────────────────────────────────────────
done_vids = set()
if PER_VIDEO.exists():
    for line in PER_VIDEO.read_text(encoding="utf-8").splitlines():
        try:
            done_vids.add(json.loads(line)["video_id"])
        except Exception:
            pass
log(f"Phase B: {len(done_vids)} already extracted, {len(by_video) - len(done_vids)} to go")

EXTRACT_PROMPT = """你在研究一位政经评论博主(鲁社长)的思维方式。以下是他一期视频的分段标题和摘要。

视频标题: {title}

分段内容:
{segs}

请提取这期视频体现的「分析路径」,严格输出 JSON:
{{
  "topic": "本期核心议题(一句)",
  "entry_angle": "他切入问题的角度(如:从人事任免切入/从资金来源倒查/从历史类比展开)",
  "reasoning_chain": ["推理链步骤1", "步骤2", "..."],
  "heuristics": ["本期用到的判断法则,如'查一个人先查他的秘书履历'"],
  "evidence_style": "他如何使用证据(排除法/交叉印证/时间线比对/圈子共现)",
  "conclusion_pattern": "结论的落点方式(明示/暗示/开放式设问)",
  "worldview_hints": ["体现的世界观假设,如'地方资本必须找京城靠山'"]
}}
只输出 JSON。"""

def extract_one(vid):
    segs = by_video[vid]
    seg_text = "\n".join(f"- {t}: {s[:400]}" for t, s in segs[:10])
    prompt = EXTRACT_PROMPT.format(title=videos.get(vid, vid), segs=seg_text)
    out = deepseek(prompt)
    if not out:
        return None
    try:
        start = out.find("{")
        end = out.rfind("}") + 1
        data = json.loads(out[start:end])
        data["video_id"] = vid
        data["video_title"] = videos.get(vid, vid)
        return data
    except Exception:
        return None

todo = [v for v in by_video if v not in done_vids]
with open(PER_VIDEO, "a", encoding="utf-8") as f:
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(extract_one, v): v for v in todo}
        n = 0
        for fut in as_completed(futures):
            n += 1
            res = fut.result()
            if res:
                f.write(json.dumps(res, ensure_ascii=False) + "\n")
                f.flush()
            if n % 10 == 0:
                log(f"  extracted {n}/{len(todo)}")
log("Phase B done")

# ── Phase C: 合成框架 ──────────────────────────────────────────
log("Phase C: synthesizing framework...")
extractions = []
for line in PER_VIDEO.read_text(encoding="utf-8").splitlines():
    try:
        extractions.append(json.loads(line))
    except Exception:
        pass
log(f"  {len(extractions)} extractions loaded")

# 压缩视图给合成器
compact = []
for e in extractions:
    compact.append({
        "topic": e.get("topic", ""),
        "entry": e.get("entry_angle", ""),
        "heur": e.get("heuristics", []),
        "evid": e.get("evidence_style", ""),
        "world": e.get("worldview_hints", []),
        "concl": e.get("conclusion_pattern", ""),
    })

SYNTH_PROMPT = """你是认知建模专家。以下是对政经评论博主「鲁社长」{n} 期视频逐期提取的分析路径数据(JSON 数组)。

{data}

请合成一份《鲁社长思维框架》文档(Markdown),将来会作为 AI agent 的人格核心,让 agent 能对**全新的问题**按他的思维方式分析。要求归纳出**可操作的思维规则**,不要空泛描述。结构:

# 鲁社长思维框架

## 1. 世界观公理(他默认成立的底层假设,按置信度排序)
## 2. 分析入口选择规则(遇到 X 类问题,从 Y 切入 —— 覆盖:人事变动/企业资本/政策发布/突发事件/人物发迹史)
## 3. 推理模式库(每种模式:名称、适用场景、步骤、示例)
## 4. 证据规则(什么算证据、如何加权、排除法怎么用、圈子共现怎么解读)
## 5. 常用启发式清单(一行一条,合并去重,保留最高频的 20-30 条)
## 6. 结论表达风格(确定度分级的表达方式、设问的用法、风险规避话术)
## 7. 局限与偏差(他的思维盲区,agent 应意识到但不必修正)

直接输出文档。"""

data_str = json.dumps(compact, ensure_ascii=False)
if len(data_str) > 80000:
    data_str = data_str[:80000] + "...]"

framework = deepseek(SYNTH_PROMPT.format(n=len(extractions), data=data_str),
                     model="deepseek-v4-pro", max_tokens=8000)
if framework:
    FRAMEWORK.write_text(framework, encoding="utf-8")
    log(f"Framework written: {FRAMEWORK} ({len(framework)} chars)")
else:
    log("Framework synthesis FAILED")
