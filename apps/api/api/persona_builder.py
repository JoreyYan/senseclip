"""
人格观点库构建 worker(Second-Me L1 思路,无训练)

- 遍历博主全部原子 → DeepSeek flash 批量抽取:
  观点条目 {topic, stance, reasoning, confidence, quote, atom_ids}
  风格金句 {quote, context, atom_id}
- bge-m3 向量化后写入 persona_viewpoints / persona_quotes
- 断点续跑:进度存 app_settings(persona_build_v1),按 offset 翻页
"""

import json
import logging
import re
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

STATE_KEY = "persona_build_v1"
BATCH_ATOMS = 25          # 每次 LLM 调用处理的原子数
PAGE = 500                # 每次从 DB 取的原子页大小

EXTRACT_PROMPT = """你是内容分析师。以下是博主「{label}」的视频/推文片段(带编号)。
请抽取两类内容,输出严格 JSON(不要 markdown 代码块):

{{"viewpoints": [
  {{"topic": "人物名或主题词(如'蔡奇'/'房地产'/'稳定币')",
    "stance": "博主的明确立场/判断,一句话",
    "reasoning": "他的推理逻辑,一句话",
    "confidence": "高/中/低(按他表达的确定程度)",
    "quote": "最能代表该观点的原话片段(截取,<=80字)",
    "refs": [片段编号]}}],
 "quotes": [
  {{"quote": "有强烈个人风格的金句原话(<=60字)",
    "context": "话题场景,几个字", "ref": 片段编号}}]}}

规则:只抽有明确立场的观点(闲聊/过渡内容跳过);金句要能一眼认出是这位博主说的;
没有可抽取内容时输出 {{"viewpoints": [], "quotes": []}}。

=== 片段 ===
{atoms}"""


class PersonaBuilder:
    def __init__(self, supabase):
        self.supabase = supabase
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.status: dict = {
            "running": False, "phase": "idle", "persona": "",
            "processed": 0, "total": 0,
            "viewpoints": 0, "quotes": 0, "last_error": "",
        }

    # ── 状态 ──────────────────────────────────────────────────
    def _save_state(self, state: dict) -> None:
        try:
            self.supabase.table("app_settings").upsert({
                "key": STATE_KEY, "value": json.dumps(state)}).execute()
        except Exception as e:
            logger.warning(f"[persona] save state failed: {e}")

    def load_state(self) -> dict:
        try:
            r = self.supabase.table("app_settings").select("value").eq("key", STATE_KEY).execute()
            if r.data:
                return json.loads(r.data[0]["value"]) or {}
        except Exception:
            pass
        return {}

    # ── 控制 ──────────────────────────────────────────────────
    def start(self, persona: str, channels: list) -> dict:
        if self._thread and self._thread.is_alive():
            return {"status": "already_running", **self.status}
        state = self.load_state()
        pstate = state.get(persona) or {"offset": 0}
        state[persona] = pstate
        state["_active"] = {"persona": persona, "channels": channels}
        self._save_state(state)
        self._stop.clear()
        self.status.update({"running": True, "persona": persona, "phase": "starting",
                            "last_error": "", "viewpoints": 0, "quotes": 0})
        self._thread = threading.Thread(
            target=self._run, args=(persona, channels), daemon=True)
        self._thread.start()
        return {"status": "started", **self.status}

    def stop(self) -> dict:
        self._stop.set()
        return {"status": "stopping"}

    # ── 主流程 ────────────────────────────────────────────────
    def _run(self, persona: str, channels: list) -> None:
        try:
            from config import DEEPSEEK_API_KEY
            import sys
            from pathlib import Path as _P
            sys.path.insert(0, str(_P(__file__).parent.parent))
            from vectorizers import create_vectorizer
            # DeepSeekClient 自带内容风控 → Claude Haiku 兜底:
            # 鲁社长语料大量涉政,裸 DeepSeek 客户端会把最有价值的批次全拒掉
            from utils.api_client import DeepSeekClient

            client = DeepSeekClient(DEEPSEEK_API_KEY, fallback_on_balance=True)
            vec = create_vectorizer()

            # 该博主名下全部视频 id
            vids = [r["id"] for r in (self.supabase.table("videos").select("id")
                    .in_("channel", channels).execute().data or [])]
            if not vids:
                self.status.update({"running": False, "phase": "no_videos"})
                return

            # 原子总数
            cnt = self.supabase.table("atoms").select("id", count="exact") \
                .in_("video_id", vids).execute()
            total = cnt.count or 0
            self.status["total"] = total

            state = self.load_state()
            offset = (state.get(persona) or {}).get("offset", 0)
            self.status["processed"] = offset
            label = "鲁社长" if persona == "lu" else "孙宇晨(孙哥)"
            logger.info(f"[persona] build {persona}: {total} atoms, resume at {offset}")

            while offset < total and not self._stop.is_set():
                self.status["phase"] = f"extracting ({offset}/{total})"
                page = self.supabase.table("atoms") \
                    .select("id,merged_text").in_("video_id", vids) \
                    .order("id").range(offset, offset + PAGE - 1).execute().data or []
                if not page:
                    break
                for i in range(0, len(page), BATCH_ATOMS):
                    if self._stop.is_set():
                        break
                    batch = page[i:i + BATCH_ATOMS]
                    try:
                        self._process_batch(client, vec, persona, label, batch)
                    except Exception as e:
                        self.status["last_error"] = str(e)[:150]
                        logger.warning(f"[persona] batch failed: {str(e)[:120]}")
                        if "Insufficient Balance" in str(e):
                            self.status["phase"] = "paused (no balance)"
                            if self._stop.wait(1800):
                                break
                    offset += len(batch)
                    self.status["processed"] = offset
                    state = self.load_state()
                    state.setdefault(persona, {})["offset"] = offset
                    self._save_state(state)
            self.status.update({"running": False,
                                "phase": "done" if offset >= total else "stopped"})
            logger.info(f"[persona] build {persona} finished at {offset}/{total}")
        except Exception as e:
            self.status.update({"running": False, "phase": "error",
                                "last_error": str(e)[:200]})
            logger.error(f"[persona] build failed: {e}")

    def _process_batch(self, client, vec, persona: str, label: str, batch: list) -> None:
        atoms_text = "\n\n".join(
            f"[{j}] ({a['id']}) {(a.get('merged_text') or '')[:600]}"
            for j, a in enumerate(batch))
        text = client.call(
            EXTRACT_PROMPT.format(label=label, atoms=atoms_text),
            model="deepseek-v4-flash", max_tokens=4000) or "{}"
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return
        try:
            data = json.loads(m.group(0))
        except Exception:
            return

        id_by_idx = {j: a["id"] for j, a in enumerate(batch)}

        vp_rows = []
        for v in (data.get("viewpoints") or [])[:20]:
            topic = (v.get("topic") or "").strip()
            stance = (v.get("stance") or "").strip()
            if not topic or not stance:
                continue
            refs = [id_by_idx[r] for r in (v.get("refs") or [])
                    if isinstance(r, int) and r in id_by_idx]
            vp_rows.append({
                "persona": persona, "topic": topic[:80], "stance": stance[:300],
                "reasoning": (v.get("reasoning") or "")[:300],
                "confidence": (v.get("confidence") or "")[:8],
                "quote": (v.get("quote") or "")[:200],
                "atom_ids": refs or [batch[0]["id"]],
            })
        qt_rows = []
        for s in (data.get("quotes") or [])[:10]:
            quote = (s.get("quote") or "").strip()
            if len(quote) < 6:
                continue
            ref = s.get("ref")
            qt_rows.append({
                "persona": persona, "quote": quote[:200],
                "context": (s.get("context") or "")[:60],
                "atom_id": id_by_idx.get(ref) if isinstance(ref, int) else None,
            })

        if vp_rows:
            embs = vec.vectorize_batch(
                [f"{r['topic']}: {r['stance']}" for r in vp_rows])
            for r, e in zip(vp_rows, embs):
                r["embedding"] = e
            self.supabase.table("persona_viewpoints").insert(vp_rows).execute()
            self.status["viewpoints"] += len(vp_rows)
        if qt_rows:
            embs = vec.vectorize_batch([r["quote"] for r in qt_rows])
            for r, e in zip(qt_rows, embs):
                r["embedding"] = e
            self.supabase.table("persona_quotes").insert(qt_rows).execute()
            self.status["quotes"] += len(qt_rows)
