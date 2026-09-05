"""
X (Twitter) 推文定时抓取 worker — 引擎侧常驻线程

- 通过 twitterapi.io 拉取指定账号的推文(免费档限速 1 req/5s)
- 推文按月聚合成伪视频行 (id = x_<username>_<YYYYMM>, channel = x_<username>)
- 每条推文一个 atom (id = x_<tweet_id>),直接向量化入 atom_embeddings
- 状态持久化 app_settings(x_poller_v1),重启自动恢复;增量抓取靠 since_id
"""

import json
import logging
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

logger = logging.getLogger(__name__)

STATE_KEY = "x_poller_v1"
POLL_HOURS = 4            # 巡航抓取间隔
QPS_SLEEP = 6             # 免费档 1req/5s,留余量
MAX_PAGES_FIRST = 40      # 首次回填最多翻页数(~800 条)
MAX_PAGES_CRUISE = 10     # 巡航时最多翻页数
API_BASE = "https://api.twitterapi.io/twitter"


def _id_num(tid: str) -> int:
    try:
        return int(tid)
    except Exception:
        return 0


def _parse_created(created: str) -> datetime:
    """'Sun Aug 30 06:19:41 +0000 2026' → datetime"""
    try:
        return parsedate_to_datetime(created)
    except Exception:
        return datetime.now(timezone.utc)


class XPoller:
    def __init__(self, supabase, api_key: str):
        self.supabase = supabase
        self.api_key = api_key
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.status: dict = {
            "running": False, "phase": "idle", "accounts": "",
            "fetched_session": 0, "new_session": 0,
            "last_poll": "", "last_error": "",
        }

    # ── 状态持久化 ────────────────────────────────────────────
    def _save_state(self, enabled: bool, accounts: list, since: dict) -> None:
        try:
            self.supabase.table("app_settings").upsert({
                "key": STATE_KEY,
                "value": json.dumps({"enabled": enabled, "accounts": accounts,
                                     "since": since}),
            }).execute()
        except Exception as e:
            logger.warning(f"[xpoller] save state failed: {e}")

    def load_state(self) -> dict:
        try:
            r = self.supabase.table("app_settings").select("value").eq("key", STATE_KEY).execute()
            if r.data:
                return json.loads(r.data[0]["value"]) or {}
        except Exception as e:
            logger.warning(f"[xpoller] load state failed: {e}")
        return {}

    # ── 控制 ──────────────────────────────────────────────────
    def start(self, username: str = "") -> dict:
        if not self.api_key:
            return {"status": "no_api_key"}
        state = self.load_state()
        accounts = state.get("accounts") or []
        u = (username or "").lstrip("@").strip()
        if u and u.lower() not in [a.lower() for a in accounts]:
            accounts.append(u)
        if not accounts:
            return {"status": "no_accounts"}
        self._save_state(True, accounts, state.get("since") or {})
        self.status["accounts"] = ", ".join(accounts)
        if self._thread and self._thread.is_alive():
            return {"status": "already_running(account added)", **self.status}
        self._stop.clear()
        self.status.update({"running": True, "fetched_session": 0,
                            "new_session": 0, "last_error": ""})
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(f"[xpoller] started, accounts: {self.status['accounts']}")
        return {"status": "started", **self.status}

    def stop(self) -> dict:
        self._stop.set()
        state = self.load_state()
        self._save_state(False, state.get("accounts") or [], state.get("since") or {})
        self.status.update({"running": False, "phase": "stopping"})
        return {"status": "stopping"}

    # ── twitterapi.io 抓取 ───────────────────────────────────
    def _fetch_new_tweets(self, username: str, since_id: str) -> list:
        """拉最新推文直到遇到 since_id 或翻页上限。返回新→旧排序的推文列表。"""
        headers = {"X-API-Key": self.api_key}
        max_pages = MAX_PAGES_CRUISE if since_id else MAX_PAGES_FIRST
        out, cursor = [], ""
        for page in range(max_pages):
            params = {"userName": username}
            if cursor:
                params["cursor"] = cursor
            try:
                r = requests.get(f"{API_BASE}/user/last_tweets",
                                 headers=headers, params=params, timeout=30)
                if r.status_code == 429:
                    time.sleep(15)
                    r = requests.get(f"{API_BASE}/user/last_tweets",
                                     headers=headers, params=params, timeout=30)
                r.raise_for_status()
                d = r.json()
            except Exception as e:
                logger.warning(f"[xpoller] fetch page {page} failed: {str(e)[:100]}")
                break
            data = d.get("data") or {}
            tweets = data.get("tweets") or []
            hit_known = False
            for t in tweets:
                tid = str(t.get("id") or "")
                if since_id and _id_num(tid) <= _id_num(since_id):
                    hit_known = True
                    break
                out.append(t)
            if hit_known or not tweets:
                break
            cursor = d.get("next_cursor") or data.get("next_cursor") or ""
            has_next = d.get("has_next_page", bool(cursor))
            if not cursor or not has_next:
                break
            if self._stop.wait(QPS_SLEEP):
                break
        return out

    # ── 入库 ──────────────────────────────────────────────────
    def _ingest_tweets(self, username: str, tweets: list) -> int:
        """推文 → 伪视频(按月) + atoms + embeddings。返回新入库条数。"""
        channel = f"x_{username.lower()}"
        rows_by_month: dict = {}
        atom_rows, texts = [], []
        for t in tweets:
            tid = str(t.get("id") or "")
            text = (t.get("text") or "").strip()
            if not tid or not text:
                continue
            if text.startswith("RT @"):   # 纯转推不入库
                continue
            dt = _parse_created(t.get("createdAt") or "")
            month = dt.strftime("%Y%m")
            vid = f"x_{username.lower()}_{month}"
            rows_by_month.setdefault(vid, {
                "id": vid,
                "title": f"@{username} X推文 {dt.strftime('%Y-%m')}",
                "duration_ms": 0, "duration_seconds": 0,
                "status": "completed", "processing_stage": "completed",
                "channel": channel,
            })
            merged = f"[{dt.strftime('%Y-%m-%d')}] {text}"
            likes = t.get("likeCount") or 0
            views = t.get("viewCount") or 0
            if views:
                merged += f"\n(赞 {likes} / 阅读 {views})"
            atom_rows.append({
                "id": f"x_{tid}", "video_id": vid,
                "start_ms": 0, "end_ms": 0, "duration_ms": 0,
                "merged_text": merged[:4000],
                "type": "tweet", "completeness": "完整",
                "source_utterance_ids": [],
            })
            texts.append(merged[:4000])
        if not atom_rows:
            return 0

        # 伪视频行 upsert
        try:
            self.supabase.table("videos").upsert(
                list(rows_by_month.values())).execute()
        except Exception as e:
            logger.warning(f"[xpoller] video upsert failed: {e}")

        # atoms upsert(分批)
        for i in range(0, len(atom_rows), 100):
            self.supabase.table("atoms").upsert(atom_rows[i:i + 100]).execute()

        # 向量化 + embeddings upsert
        try:
            import sys
            from pathlib import Path as _P
            sys.path.insert(0, str(_P(__file__).parent.parent))
            from vectorizers import create_vectorizer
            vec = create_vectorizer()
            for i in range(0, len(atom_rows), 32):
                batch = atom_rows[i:i + 32]
                embs = vec.vectorize_batch([texts[i + j] for j in range(len(batch))])
                emb_rows = [{"atom_id": a["id"], "video_id": a["video_id"],
                             "embedding": e} for a, e in zip(batch, embs)]
                self.supabase.table("atom_embeddings").upsert(emb_rows).execute()
                if self._stop.is_set():
                    break
        except Exception as e:
            logger.error(f"[xpoller] embedding failed: {e}")
            self.status["last_error"] = f"embed: {str(e)[:120]}"

        return len(atom_rows)

    def _repair_missing_embeddings(self) -> None:
        """自愈:补齐缺失向量的推文原子(历史 bug 或中途失败留下的缺口)。"""
        try:
            atoms = self.supabase.table("atoms").select("id,video_id,merged_text") \
                .like("id", "x_%").limit(5000).execute().data or []
            have = self.supabase.table("atom_embeddings").select("atom_id") \
                .like("atom_id", "x_%").limit(10000).execute().data or []
            have_ids = {r["atom_id"] for r in have}
            todo = [a for a in atoms if a["id"] not in have_ids]
            if not todo:
                return
            logger.info(f"[xpoller] repairing {len(todo)} missing embeddings")
            import sys
            from pathlib import Path as _P
            sys.path.insert(0, str(_P(__file__).parent.parent))
            from vectorizers import create_vectorizer
            vec = create_vectorizer()
            for i in range(0, len(todo), 32):
                batch = todo[i:i + 32]
                embs = vec.vectorize_batch([(a.get("merged_text") or "")[:6000] for a in batch])
                rows = [{"atom_id": a["id"], "video_id": a["video_id"], "embedding": e}
                        for a, e in zip(batch, embs)]
                self.supabase.table("atom_embeddings").upsert(rows).execute()
                if self._stop.is_set():
                    break
            logger.info(f"[xpoller] repair done: {len(todo)} embeddings")
        except Exception as e:
            logger.warning(f"[xpoller] repair embeddings failed: {e}")
            self.status["last_error"] = f"repair: {str(e)[:120]}"

    # ── 主循环 ────────────────────────────────────────────────
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                state = self.load_state()
                accounts = state.get("accounts") or []
                since = state.get("since") or {}
                self.status.update({"running": True, "phase": "polling",
                                    "accounts": ", ".join(accounts)})
                self._repair_missing_embeddings()
                for username in accounts:
                    if self._stop.is_set():
                        break
                    tweets = self._fetch_new_tweets(username, since.get(username, ""))
                    self.status["fetched_session"] += len(tweets)
                    if tweets:
                        n = self._ingest_tweets(username, tweets)
                        self.status["new_session"] += n
                        newest = max((str(t.get("id") or "") for t in tweets),
                                     key=_id_num)
                        if _id_num(newest) > _id_num(since.get(username, "")):
                            since[username] = newest
                        logger.info(f"[xpoller] @{username}: +{n} tweets ingested")
                    time.sleep(QPS_SLEEP)
                self._save_state(True, accounts, since)
                self.status["last_poll"] = datetime.now(timezone.utc).isoformat()
                self.status["phase"] = f"idle (next poll in {POLL_HOURS}h)"
                if self._stop.wait(POLL_HOURS * 3600):
                    break
            except Exception as e:
                self.status["last_error"] = str(e)[:200]
                logger.error(f"[xpoller] loop error: {e}")
                if self._stop.wait(600):
                    break
        self.status.update({"running": False, "phase": "stopped"})
        logger.info("[xpoller] stopped")
