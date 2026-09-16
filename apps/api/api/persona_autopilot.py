"""
人格自动驾驶(engine 服务常驻线程)

对 personas/*.yaml 里标了 autopilot: true 的人格,每隔 TICK_MINUTES 检查一次,按进度自动推进:

  1. 建观点库   频道有新处理完的视频 → PersonaBuilder 增量抽取观点与金句
  2. 蒸馏框架   处理完的视频 ≥ MIN_VIDEOS_DISTILL,且比上次蒸馏时增长一半以上 → 重新蒸馏思维框架
  3. 公开上线   有框架 + 观点 ≥ MIN_VIEWPOINTS + 视频覆盖达到计划量的 PUBLISH_COVERAGE
               → app_settings.persona_visibility_v1[key] = "public",前端自动出现

同时把提名榜里对应频道的提名标记为「制作中」/「已上线」。
状态存 app_settings.persona_autopilot_v1,可用 GET /api/admin/persona/autopilot 查看。
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

STATE_KEY = "persona_autopilot_v1"
VISIBILITY_KEY = "persona_visibility_v1"
TICK_MINUTES = int(os.environ.get("PERSONA_AUTOPILOT_MINUTES", "30"))
MIN_VIDEOS_DISTILL = int(os.environ.get("PERSONA_MIN_VIDEOS_DISTILL", "15"))
MIN_VIEWPOINTS = int(os.environ.get("PERSONA_MIN_VIEWPOINTS", "120"))
PUBLISH_COVERAGE = float(os.environ.get("PERSONA_PUBLISH_COVERAGE", "0.5"))


class PersonaAutopilot:
    def __init__(self, supabase, builder, backfill_worker, load_personas):
        self.supabase = supabase
        self.builder = builder
        self.backfill = backfill_worker
        self.load_personas = load_personas      # 无缓存的注册表加载函数
        self._stop = threading.Event()
        self._thread = None
        self.status: dict = {"running": False, "phase": "idle", "last_tick": "", "personas": {}}

    # ── 状态 ──────────────────────────────────────────────────
    def _get(self, key: str) -> dict:
        try:
            r = self.supabase.table("app_settings").select("value").eq("key", key).execute()
            if r.data and r.data[0].get("value"):
                return json.loads(r.data[0]["value"]) or {}
        except Exception as e:
            logger.warning(f"[autopilot] load {key} failed: {e}")
        return {}

    def _put(self, key: str, value: dict) -> None:
        try:
            self.supabase.table("app_settings").upsert(
                {"key": key, "value": json.dumps(value, ensure_ascii=False)}).execute()
        except Exception as e:
            logger.warning(f"[autopilot] save {key} failed: {e}")

    # ── 控制 ──────────────────────────────────────────────────
    def start(self) -> dict:
        if self._thread and self._thread.is_alive():
            return {"status": "already_running", **self.status}
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.status["running"] = True
        return {"status": "started"}

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        # 启动后稍等,让 backfill 先完成首轮扫描(拿到各频道计划视频数)
        if self._stop.wait(120):
            return
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:
                logger.error(f"[autopilot] tick failed: {e}")
                self.status["last_error"] = str(e)[:200]
            self.status["phase"] = f"idle (next check in {TICK_MINUTES}min)"
            if self._stop.wait(TICK_MINUTES * 60):
                break
        self.status["running"] = False

    # ── 每轮检查 ──────────────────────────────────────────────
    def tick(self) -> None:
        self.status["last_tick"] = datetime.now(timezone.utc).isoformat()
        registry = self.load_personas()
        state = self._get(STATE_KEY)
        visibility = self._get(VISIBILITY_KEY)
        counts = (self.backfill.status.get("channel_counts") or {}) if self.backfill else {}

        for key, cfg in registry.items():
            if not cfg.get("autopilot") or self._stop.is_set():
                continue
            ps = state.setdefault(key, {})
            info = self._progress(key, cfg, counts)
            ps.update({"videos": info["videos"], "planned": info["planned"],
                       "viewpoints": info["viewpoints"], "checked_at": self.status["last_tick"]})
            self.status["personas"][key] = dict(ps)

            if info["videos"] and not ps.get("nominations_marked"):
                self._mark_nominations(cfg, "building")
                ps["nominations_marked"] = "building"

            # 1) 观点库:有未抽取的新视频就增量跑(builder 全局单线程,忙则下轮)
            if info["unbuilt"] and self.builder and not self.builder.is_running():
                self.status["phase"] = f"building viewpoints: {key}"
                logger.info(f"[autopilot] {key}: {info['unbuilt']} new videos → viewpoint build")
                self.builder.start(key, cfg["channels"], cfg.get("label") or "")
                ps["last_build_started"] = self.status["last_tick"]

            # 2) 思维框架
            last_n = int(ps.get("distilled_videos") or 0)
            if info["videos"] >= MIN_VIDEOS_DISTILL and (
                    last_n == 0 or (info["videos"] >= last_n * 1.5 and info["videos"] - last_n >= 10)):
                self.status["phase"] = f"distilling framework: {key}"
                try:
                    from api.persona_distill import distill_framework
                    res = distill_framework(self.supabase, key, cfg, self._stop, self.status)
                    if res.get("framework_chars"):
                        ps["distilled_videos"] = res["videos"]
                        ps["distilled_at"] = datetime.now(timezone.utc).isoformat()
                except Exception as e:
                    logger.warning(f"[autopilot] {key} distill failed: {str(e)[:150]}")
                    ps["last_error"] = str(e)[:200]

            # 3) 公开
            if visibility.get(key) != "public" and cfg.get("hidden"):
                has_framework = bool(self._framework_text(cfg))
                enough_cover = info["planned"] == 0 and info["videos"] >= 40 or \
                    info["planned"] > 0 and info["videos"] >= max(MIN_VIDEOS_DISTILL, info["planned"] * PUBLISH_COVERAGE)
                if has_framework and info["viewpoints"] >= MIN_VIEWPOINTS and enough_cover:
                    visibility[key] = "public"
                    self._put(VISIBILITY_KEY, visibility)
                    ps["published_at"] = datetime.now(timezone.utc).isoformat()
                    self._mark_nominations(cfg, "live")
                    ps["nominations_marked"] = "live"
                    logger.info(f"[autopilot] {key} published: {info}")

            self._put(STATE_KEY, state)

    # ── 辅助 ──────────────────────────────────────────────────
    def _progress(self, key: str, cfg: dict, counts: dict) -> dict:
        vids = [r["id"] for r in (self.supabase.table("videos").select("id")
                .in_("channel", cfg["channels"]).execute().data or [])]
        build_state = (self.builder.load_state().get(key) or {}) if self.builder else {}
        done = set(build_state.get("done_videos") or [])
        vp = self.supabase.table("persona_viewpoints").select("id", count="exact") \
            .eq("persona", key).limit(1).execute()
        planned = sum(int(counts.get(ch) or 0) for ch in cfg["channels"])
        return {"videos": len(vids), "unbuilt": len([v for v in vids if v not in done]),
                "viewpoints": int(vp.count or 0), "planned": planned}

    def _framework_text(self, cfg: dict) -> str:
        fkey = cfg.get("framework_key")
        if not fkey:
            return ""
        try:
            r = self.supabase.table("app_settings").select("value").eq("key", fkey).execute()
            return (r.data[0].get("value") or "") if r.data else ""
        except Exception:
            return ""

    def _mark_nominations(self, cfg: dict, status: str) -> None:
        handles = [c.lower() for c in cfg.get("channels") or [] if not str(c).startswith("x_")]
        xs = [str(c)[2:].lower() for c in cfg.get("channels") or [] if str(c).startswith("x_")]
        try:
            if handles:
                self.supabase.table("persona_nominations").update({"status": status}) \
                    .eq("platform", "youtube").in_("handle", handles).neq("status", "live").execute()
            if xs:
                self.supabase.table("persona_nominations").update({"status": status}) \
                    .eq("platform", "x").in_("handle", xs).neq("status", "live").execute()
        except Exception as e:
            logger.warning(f"[autopilot] mark nominations failed: {e}")
