# -*- coding: utf-8 -*-
"""FastAPI Server - Backend API for Frontend"""

import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel
from typing import Any, Dict, List, Optional
from fastapi.responses import StreamingResponse
import json
import logging
import uuid
from datetime import datetime

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import analysis services
from api.analysis_service import AnalysisService
# Force reload of incremental analysis service (2024-10-04 fix)
import importlib
import api.incremental_analysis_service
importlib.reload(api.incremental_analysis_service)
from api.incremental_analysis_service import IncrementalAnalysisService
from api.segment_manager import SegmentManager
from api.segment_detail_service import SegmentDetailService
from api.narrative_generator import NarrativeGenerator
from api.vectorization_service import VectorizationService
from api.vector_store_manager import VectorStoreManager
from api.local_index_manager import LocalIndexManager
from api.two_phase_generator import TwoPhaseGenerator
from api.citation_extractor import CitationExtractor
from api.structure_validator import StructureValidator
from api.repair_orchestrator import RepairOrchestrator
from api.intent_classifier import IntentClassifier
from api.unified_agent import UnifiedAgent
from api.person_network_service import PersonNetworkService
from api.person_relation_service import PersonRelationService

app = FastAPI(title="Video Understanding API", version="1.0.0")

# Gzip 压缩（减少跨境传输大小）
app.add_middleware(GZipMiddleware, minimum_size=1000)

# CORS configuration for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data directory path
DATA_DIR = Path(__file__).parent.parent / "data" / "output_pipeline_v3"

# Get API keys from config
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import CLAUDE_API_KEY, OPENAI_API_KEY, SUPABASE_URL, SUPABASE_KEY
from supabase import create_client as _create_supabase_client

# Supabase client for ingest/settings endpoints
_supabase = _create_supabase_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL else None

# Admin client with service_role key (bypasses RLS)
_SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE", "")
_supabase_admin = _create_supabase_client(SUPABASE_URL, _SUPABASE_SERVICE_KEY) if (SUPABASE_URL and _SUPABASE_SERVICE_KEY) else _supabase
person_network_service = PersonNetworkService(_supabase) if _supabase else None

async def _get_user_id(authorization: str = Header(None)) -> Optional[str]:
    """尝试从 token 获取 user_id，未登录返回 None（不报错）。"""
    if not authorization or not _supabase_admin:
        return None
    token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else authorization
    try:
        user = _supabase_admin.auth.get_user(token)
        return user.user.id if user and user.user else None
    except Exception:
        return None

def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# ---- 产品参数(全部可由环境变量覆盖;开源默认:不计费) ----
BILLING_ENABLED = _env_bool("BILLING_ENABLED", False)   # false = 无积分/无 Stripe,纯自用
GUEST_DAILY_LIMIT = _env_int("GUEST_DAILY_LIMIT", 5)     # 游客每日免费次数;0 = 不限

_chat_logs_table_ok = False

def _ensure_chat_logs_table():
    """首次调用时通过 SQL 建表（如果不存在）"""
    global _chat_logs_table_ok
    if _chat_logs_table_ok or not _supabase_admin:
        return
    try:
        # 尝试查询表是否存在
        _supabase_admin.table("chat_logs").select("id").limit(1).execute()
        _chat_logs_table_ok = True
    except Exception:
        # 表不存在，通过 rpc 或直接 REST 建表不可行，跳过
        # 需要手动在 Supabase Dashboard SQL Editor 执行建表语句
        logger.warning("[chat_log] chat_logs table not found. Please create it manually in Supabase SQL Editor.")
        _chat_logs_table_ok = True  # 不再重复检查

def _save_chat_log(user_id: Optional[str], guest_ip: str, question: str, answer: str, atoms_count: int, model: str) -> Optional[str]:
    """保存对话记录到 chat_logs 表,返回记录 id(供前端 👍/👎 反馈关联)"""
    _ensure_chat_logs_table()
    try:
        if _supabase:
            r = _supabase.table("chat_logs").insert({
                "user_id": user_id,
                "guest_ip": guest_ip if not user_id else None,
                "question": question[:2000],
                "answer": answer[:5000],
                "atoms_count": atoms_count,
                "model": model,
            }).execute()
            if r.data:
                return r.data[0].get("id")
    except Exception as ex:
        logger.warning(f"[chat_log] save failed: {ex}")
    return None

def _check_guest_limit(guest_ip: str) -> bool:
    """检查游客今日是否超过限制(GUEST_DAILY_LIMIT<=0 表示不限)"""
    if GUEST_DAILY_LIMIT <= 0:
        return True
    try:
        if not _supabase:
            return True
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        res = (_supabase.table("chat_logs")
            .select("id", count="exact")
            .eq("guest_ip", guest_ip)
            .gte("created_at", f"{today}T00:00:00Z")
            .execute())
        count = res.count if res.count is not None else len(res.data or [])
        return count < GUEST_DAILY_LIMIT
    except Exception:
        return True  # 查询失败不阻塞

# ── Lightweight TTL cache (avoid per-request full-table reads) ─────
_ttl_cache: Dict[str, tuple] = {}
_last_stale_fix_ts = 0.0  # /api/library stale-job auto-fix throttle

# ── Query embedding via configured provider (SiliconFlow / OpenAI) ─
_query_vectorizer = None

def _embed_text(text: str) -> list:
    global _query_vectorizer
    if _query_vectorizer is None:
        from vectorizers import create_vectorizer
        _query_vectorizer = create_vectorizer()
    return _query_vectorizer.vectorize_text(text)

def _cached(key: str, ttl_sec: int, loader):
    """Return cached value or reload via loader(). Never raises: on loader
    failure returns stale value if present, else the loader's exception."""
    import time as _time
    now = _time.time()
    hit = _ttl_cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    try:
        value = loader()
    except Exception:
        if hit:
            return hit[1]
        raise
    _ttl_cache[key] = (now + ttl_sec, value)
    return value


def _maybe_data(resp):
    """supabase-py maybe_single() returns None (not a response) when no row
    matches — normalize to `data or None` so callers can't crash on .data."""
    return getattr(resp, "data", None) if resp is not None else None


# ── Credits / Billing helpers ──────────────────────────────────────
SIGNUP_BONUS_CREDITS = _env_int("SIGNUP_BONUS_CREDITS", 30)
LU_CONSULT_COST = _env_int("CONSULT_COST", 10)  # 博主模式单次积分

async def _ensure_credits(authorization: str, required: int = 1) -> tuple:
    """Verify user is authenticated and has >= required credits.
    Lazy-creates user_credits row with signup bonus on first call.
    Returns (user_id, current_balance).
    Raises 401 if not authenticated, 402 if insufficient credits."""
    user_id = await _get_user_id(authorization)
    if not user_id:
        raise HTTPException(status_code=401, detail="Please sign in to process videos")
    if not BILLING_ENABLED:
        return (user_id, None)

    row = _supabase_admin.table("user_credits") \
        .select("balance").eq("user_id", user_id).maybe_single().execute()
    row_data = _maybe_data(row)

    if not row_data:
        _supabase_admin.table("user_credits").insert({
            "user_id": user_id, "balance": SIGNUP_BONUS_CREDITS
        }).execute()
        _supabase_admin.table("credit_transactions").insert({
            "user_id": user_id, "amount": SIGNUP_BONUS_CREDITS,
            "type": "signup_bonus", "description": f"Welcome bonus: {SIGNUP_BONUS_CREDITS} free credits"
        }).execute()
        return (user_id, SIGNUP_BONUS_CREDITS)

    balance = row_data["balance"]
    if balance < required:
        raise HTTPException(status_code=402, detail={
            "error": "INSUFFICIENT_CREDITS", "balance": balance, "required": required
        })
    return (user_id, balance)


def _deduct_credit(user_id: str, job_id: str, description: str, tx_type: str = "chat", amount: int = 1) -> int:
    """Deduct `amount` credits. Returns new balance(计费关闭时为 None,不落账)。"""
    if not BILLING_ENABLED:
        return None
    row = _supabase_admin.table("user_credits") \
        .select("balance").eq("user_id", user_id).maybe_single().execute()
    _rd = _maybe_data(row)
    balance = _rd["balance"] if _rd else 0
    if balance < amount:
        raise HTTPException(status_code=402, detail={
            "error": "INSUFFICIENT_CREDITS", "balance": balance, "required": amount
        })
    new_balance = balance - amount
    _supabase_admin.table("user_credits") \
        .update({"balance": new_balance}).eq("user_id", user_id).execute()
    txn = {"user_id": user_id, "amount": -amount, "type": tx_type, "description": description}
    if job_id:
        txn["job_id"] = job_id
    _supabase_admin.table("credit_transactions").insert(txn).execute()
    return new_balance


from config import TAVILY_API_KEY
person_relation_service = PersonRelationService(_supabase, CLAUDE_API_KEY, TAVILY_API_KEY) if _supabase else None

# Initialize analysis services (singletons)
analysis_service = AnalysisService(DATA_DIR)
incremental_service = IncrementalAnalysisService(DATA_DIR, CLAUDE_API_KEY)
segment_manager = SegmentManager(DATA_DIR, segment_duration_minutes=20)
segment_detail_service = SegmentDetailService(DATA_DIR)

# Pydantic models for request validation
class SearchRequest(BaseModel):
    video_ids: List[str]
    query: str
    model: str = "gpt-4o"
    max_tokens: int = 2000

class UnifiedAgentRequest(BaseModel):
    video_id: str
    query: str
    model: str = "gpt-4o"
    max_tokens: int = 2000
    force_mode: Optional[str] = None  # "qa" or "cutlist" to override intent classification


class PersonMergeRequest(BaseModel):
    source_person_id: Optional[str] = None
    target_person_id: Optional[str] = None
    source_name: Optional[str] = None
    target_name: Optional[str] = None
    limit: int = 300
    use_ai: bool = True
    ai_model: str = "claude-haiku-4-5"

class AtomSearchRequest(BaseModel):
    query: str
    limit: int = 20
    video_ids: Optional[List[str]] = None  # None = search all videos

class ChatMessage(BaseModel):
    role: str   # "user" or "assistant"
    content: str

class ChatAttachment(BaseModel):
    type: str                    # "image" | "file"
    name: str = ""
    media_type: str = ""         # e.g. image/jpeg, application/pdf
    data: str = ""               # base64 (no data: prefix)

class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    video_ids: Optional[List[str]] = None  # None = search all videos
    attachments: Optional[List[ChatAttachment]] = None  # 附加于最后一条用户消息
    persona: Optional[str] = None  # 咨询模式人格: lu(默认) / sun
    full_pro: Optional[bool] = None  # 测试用:检索轮也用 v4-pro(默认 flash)

class PersonRenameRequest(BaseModel):
    new_name: str

class PersonAskRequest(BaseModel):
    question: str
    ai_model: str = "claude-haiku-4-5"
    include_web: bool = True

class ExtractRelationsRequest(BaseModel):
    video_ids: List[str]
    batch_size: int = 12
    model: str = "claude-haiku-4-5"

class EnrichPersonsWebRequest(BaseModel):
    person_names: List[str]
    model: str = "claude-haiku-4-5"

class SyncPersonsRequest(BaseModel):
    video_ids: Optional[List[str]] = None  # if None, use all cached videos

# Helper function to read JSON file
def read_json_file(filename: str):
    """Read JSON file from data directory"""
    file_path = DATA_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)

# Helper function to read JSONL file
def read_jsonl_file(filename: str):
    """Read JSONL file from data directory"""
    file_path = DATA_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    items = []
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                items.append(json.loads(line))
    return items


def _ms_to_hhmmss(ms: Optional[int]) -> str:
    total_seconds = max(0, int(ms or 0)) // 1000
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _load_video_row(video_id: str) -> Optional[Dict[str, Any]]:
    if not _supabase:
        return None
    result = _supabase.table("videos").select("*").eq("id", video_id).limit(1).execute()
    if result.data:
        return result.data[0]
    # Fallback: try video_assets table (used by ingestion pipeline)
    r2 = _supabase.table("video_assets").select("*").eq("video_id", video_id).limit(1).execute()
    if r2.data:
        row = r2.data[0]
        # Normalize to same shape as videos table
        return {
            "id": row.get("video_id"),
            "title": row.get("title", video_id),
            "duration_ms": row.get("duration_ms", 0),
            "status": row.get("status"),
            "atom_count": row.get("atom_count", 0),
            "segment_count": row.get("segment_count", 0),
            "entity_count": row.get("entity_count", 0),
        }
    return None


def _load_segment_rows(video_id: str) -> List[Dict[str, Any]]:
    if not _supabase:
        return []
    result = _supabase.table("narrative_segments").select(
        "id,title,summary,start_ms,end_ms,duration_ms,atom_count,topics,key_entities,narrative_type"
    ).eq("video_id", video_id).order("start_ms").execute()
    return result.data or []


def _load_atom_rows(video_id: str, segment_id: Optional[str] = None) -> List[Dict[str, Any]]:
    if not _supabase:
        return []
    query = _supabase.table("atoms").select(
        "id,video_id,segment_id,start_ms,end_ms,duration_ms,merged_text,type,completeness"
    ).eq("video_id", video_id)
    if segment_id:
        query = query.eq("segment_id", segment_id)
    result = query.order("start_ms").execute()
    return result.data or []


def _load_annotations_map(atom_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    if not _supabase or not atom_ids:
        return {}
    result = _supabase.table("atom_annotations").select("*").in_("atom_id", atom_ids).execute()
    return {row["atom_id"]: row for row in (result.data or []) if row.get("atom_id")}


def _load_atom_entities_map(atom_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    if not _supabase or not atom_ids:
        return {}
    result = _supabase.table("atom_entities").select("*").in_("atom_id", atom_ids).execute()
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in result.data or []:
        grouped.setdefault(row.get("atom_id"), []).append(row)
    return grouped


def _load_embeddings_map(atom_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    if not _supabase or not atom_ids:
        return {}
    result = _supabase.table("atom_embeddings").select("*").in_("atom_id", atom_ids).execute()
    return {row["atom_id"]: row for row in (result.data or []) if row.get("atom_id")}


def _aggregate_entities(atom_entities_map: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    aggregated: Dict[str, Dict[str, Any]] = {}
    for entity_rows in atom_entities_map.values():
        for row in entity_rows:
            entity_name = row.get("entity_name")
            if not entity_name:
                continue
            key = f"{entity_name}::{row.get('entity_type') or 'UNKNOWN'}"
            current = aggregated.setdefault(key, {
                "id": key,
                "name": entity_name,
                "entity_type": row.get("entity_type"),
                "description": None,
                "mention_count": 0,
                "importance_score": 0,
            })
            current["mention_count"] += 1
            current["importance_score"] = max(
                float(current.get("importance_score") or 0),
                float(row.get("confidence") or 0),
            )
    return sorted(
        aggregated.values(),
        key=lambda item: (item.get("mention_count", 0), item.get("importance_score", 0)),
        reverse=True,
    )

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "Video Understanding API",
        "version": "1.0.0",
        "endpoints": {
            "atoms": "/api/projects/{project_id}/atoms",
            "segments": "/api/projects/{project_id}/segments",
            "entities": "/api/projects/{project_id}/entities",
            "topics": "/api/projects/{project_id}/topics",
            "graph": "/api/projects/{project_id}/graph",
            "creative": "/api/projects/{project_id}/creative",
        }
    }

@app.get("/api/health")
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "data_dir": str(DATA_DIR)}


# ==================== Ingest Endpoints ====================

class IngestRequest(BaseModel):
    youtube_url: str

@app.post("/api/ingest")
async def submit_ingest(request: IngestRequest, background_tasks: BackgroundTasks):
    """提交 YouTube URL，启动后台摄入流程，返回 job_id"""
    if not _supabase:
        raise HTTPException(status_code=503, detail="Supabase 未配置")

    job_id = str(uuid.uuid4())
    _supabase.table("pipeline_jobs").insert({
        "id": job_id,
        "youtube_url": request.youtube_url,
        "status": "pending",
        "current_step": "等待开始...",
    }).execute()
    _prune_old_jobs(request.youtube_url)

    from api.ingestion_pipeline import run_ingestion
    background_tasks.add_task(run_ingestion, job_id, request.youtube_url)

    return {"job_id": job_id, "status": "pending"}


@app.post("/api/ingest/upload")
async def upload_audio_ingest(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """直接上传音频文件，跳过 yt-dlp，直接转录+分析"""
    if not _supabase:
        raise HTTPException(status_code=503, detail="Supabase 未配置")

    audio_bytes = await file.read()
    if len(audio_bytes) == 0:
        raise HTTPException(status_code=400, detail="文件为空")

    job_id = str(uuid.uuid4())
    _supabase.table("pipeline_jobs").insert({
        "id": job_id,
        "youtube_url": f"[手动上传] {file.filename}",
        "status": "uploading",
        "current_step": "正在上传音频到云端...",
    }).execute()
    _prune_old_jobs(f"[手动上传] {file.filename}")

    from api.ingestion_pipeline import run_ingestion_from_bytes
    background_tasks.add_task(run_ingestion_from_bytes, job_id, audio_bytes, file.filename or "audio.mp3")

    return {"job_id": job_id, "status": "uploading"}


@app.post("/api/ingest/reprocess/{video_id}")
async def reprocess_video(video_id: str, background_tasks: BackgroundTasks):
    """强制重新处理视频：清除已有原子/片段/实体，从 SRT 重新原子化"""
    if not _supabase:
        raise HTTPException(status_code=503, detail="Supabase 未配置")

    # Get stored SRT URL from video_assets
    asset = _supabase.table("video_assets").select("srt_content,title").eq("video_id", video_id).maybe_single().execute()
    if not asset or not asset.data:
        raise HTTPException(status_code=404, detail="视频不存在")

    srt_ref = asset.data.get("srt_content", "")
    if not srt_ref:
        raise HTTPException(status_code=400, detail="没有 SRT 文件，无法重新处理")

    # Use the actual youtube_url so the library page can associate the job with the asset
    actual_youtube_url = _supabase.table("video_assets").select("youtube_url").eq("video_id", video_id).maybe_single().execute()
    job_youtube_url = (actual_youtube_url.data or {}).get("youtube_url") or f"https://www.youtube.com/watch?v={video_id}"

    job_id = str(uuid.uuid4())
    _supabase.table("pipeline_jobs").insert({
        "id": job_id,
        "youtube_url": job_youtube_url,
        "status": "pending",
        "current_step": "准备强制重新处理...",
    }).execute()
    _prune_old_jobs(job_youtube_url)

    from api.ingestion_pipeline import run_reprocess
    background_tasks.add_task(run_reprocess, job_id, video_id, srt_ref, asset.data.get("title", video_id))

    return {"job_id": job_id, "video_id": video_id, "status": "pending"}


@app.get("/api/ingest/{job_id}")
async def get_ingest_status(job_id: str):
    """查询摄入作业状态"""
    if not _supabase:
        raise HTTPException(status_code=503, detail="Supabase 未配置")

    result = _supabase.table("pipeline_jobs").select("*").eq("id", job_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="作业不存在")
    return result.data[0]


# ==================== Settings Endpoints ====================

@app.post("/api/settings/cookies")
async def upload_cookies(file: UploadFile = File(...)):
    """上传 YouTube cookies.txt 文件，保存到 Supabase app_settings"""
    if not _supabase:
        raise HTTPException(status_code=503, detail="Supabase 未配置")

    content = await file.read()
    cookies_text = content.decode("utf-8", errors="replace")

    _supabase.table("app_settings").upsert({
        "key": "youtube_cookies",
        "value": cookies_text,
    }).execute()

    return {"success": True, "message": "cookies 已更新"}


@app.get("/api/settings/cookies")
async def get_cookies_status():
    """获取 cookies 状态（有无、更新时间）"""
    if not _supabase:
        raise HTTPException(status_code=503, detail="Supabase 未配置")

    result = _supabase.table("app_settings").select("updated_at").eq("key", "youtube_cookies").execute()
    if result.data:
        return {"has_cookies": True, "updated_at": result.data[0]["updated_at"]}
    return {"has_cookies": False, "updated_at": None}


# ==================== Library Endpoints ====================

@app.get("/api/debug/srt/{video_id}")
async def debug_srt(video_id: str):
    """临时调试：返回 video_assets 里的 srt_content 头尾"""
    asset = _supabase.table("video_assets").select("srt_content").eq("video_id", video_id).maybe_single().execute()
    if not asset or not asset.data:
        raise HTTPException(404, "Not found")
    srt = asset.data.get("srt_content") or ""
    if not srt:
        return {"srt_length": 0, "message": "SRT is empty"}
    lines = srt.strip().split("\n")
    return {
        "srt_length": len(srt),
        "srt_lines": len(lines),
        "first_30_lines": lines[:30],
        "last_10_lines": lines[-10:],
    }

@app.get("/api/library")
async def get_library():
    """返回所有 video_assets + 最近 job 状态

    ⚠️ 性能注意：不要对每个 asset 单独查 pipeline_jobs（N+1 查询会在 Railway/Supabase 上非常慢，容易超时导致 499）。
    """
    if not _supabase:
        raise HTTPException(status_code=503, detail="Supabase 未配置")

    # 明确列查询：绝不拉 srt_content（每行可达数百 KB 的字幕全文）
    _ASSET_COLUMNS = ("youtube_url,video_id,audio_url,title,duration_sec,"
                      "status,db_video_id,error_message,created_at,updated_at")
    assets = _supabase.table("video_assets").select(_ASSET_COLUMNS).order("created_at", desc=True).execute().data

    # 单独用一个轻量查询标记哪些资产已有字幕（只取 url 列）
    try:
        _srt_rows = _supabase.table("video_assets").select("youtube_url") \
            .not_.is_("srt_content", "null").execute().data or []
        _has_srt_urls = {r["youtube_url"] for r in _srt_rows}
    except Exception:
        _has_srt_urls = set()
    for a in assets:
        a["has_srt"] = a.get("youtube_url") in _has_srt_urls

    # 批量取最近 jobs（最多每个 url 取 3 条），避免 N+1
    urls = [a.get("youtube_url") for a in assets if a.get("youtube_url")]
    jobs_by_url = {}
    if urls:
        # Supabase Python client: in_()
        try:
            all_jobs = _supabase.table("pipeline_jobs") \
                .select("id,youtube_url,status,current_step,error_message,last_successful_step,failed_step,created_at") \
                .in_("youtube_url", urls) \
                .order("created_at", desc=True) \
                .execute().data
        except Exception:
            # Backward compatibility if columns are not present yet.
            all_jobs = _supabase.table("pipeline_jobs") \
                .select("id,youtube_url,status,current_step,error_message,created_at") \
                .in_("youtube_url", urls) \
                .order("created_at", desc=True) \
                .execute().data

        # 自动修正卡住的 job：超过60分钟仍为 processing → 检查实际资产决定状态
        # 节流：每 5 分钟最多跑一次（library 被前端每 5 秒轮询，
        # 每次都做逐 job 资产探测 + 写库会拖垮这个端点）
        from datetime import datetime, timezone, timedelta
        import time as _time
        global _last_stale_fix_ts
        _run_stale_fix = (_time.time() - _last_stale_fix_ts) > 300
        if _run_stale_fix:
            _last_stale_fix_ts = _time.time()

        stale_cutoff = datetime.now(timezone.utc) - timedelta(minutes=60)

        # 建立 youtube_url → db_video_id 映射
        url_to_vid = {a.get("youtube_url"): a.get("db_video_id") for a in assets if a.get("youtube_url") and a.get("db_video_id")}

        stale_fixes = []  # [(job_id, new_status, new_step, error_msg)]

        for job in all_jobs:
            yurl = job.get("youtube_url")
            if not yurl:
                continue

            job_status = job.get("status")
            job_step = job.get("current_step")
            job_error = job.get("error_message")

            # 检测卡住的 job
            if _run_stale_fix and job_status in ("processing", "pending"):
                created = job.get("created_at", "")
                is_stale = False
                try:
                    job_time = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    is_stale = job_time < stale_cutoff
                except Exception:
                    pass

                if is_stale:
                    # 检查实际资产状态，决定真实进度
                    db_vid = url_to_vid.get(yurl)
                    if db_vid:
                        try:
                            atom_cnt = len((_supabase.table("atoms").select("id").eq("video_id", db_vid).limit(1).execute()).data or [])
                            seg_cnt = len((_supabase.table("narrative_segments").select("id").eq("video_id", db_vid).limit(1).execute()).data or [])
                            ent_cnt = len((_supabase.table("entities").select("id").eq("video_id", db_vid).limit(1).execute()).data or [])
                            emb_cnt = len((_supabase.table("atom_embeddings").select("atom_id").eq("video_id", db_vid).limit(1).execute()).data or [])

                            if emb_cnt > 0:
                                # 全部完成
                                job_status = "completed"
                                job_step = "processed"
                                job_error = None
                                stale_fixes.append((job.get("id"), "completed", "processed", None))
                            elif ent_cnt > 0:
                                job_status = "error"
                                job_step = "vectorized"
                                job_error = "进程中断于向量化阶段，请继续处理"
                                stale_fixes.append((job.get("id"), "error", job_step, job_error))
                            elif seg_cnt > 0:
                                job_status = "error"
                                job_step = "entities_extracted"
                                job_error = "进程中断于实体提取阶段，请继续处理"
                                stale_fixes.append((job.get("id"), "error", job_step, job_error))
                            elif atom_cnt > 0:
                                job_status = "error"
                                job_step = "segmented"
                                job_error = "进程中断于片段化阶段，请继续处理"
                                stale_fixes.append((job.get("id"), "error", job_step, job_error))
                            else:
                                job_status = "error"
                                job_step = "atomized"
                                job_error = "进程中断于原子化阶段，请继续处理"
                                stale_fixes.append((job.get("id"), "error", job_step, job_error))
                        except Exception:
                            job_status = "error"
                            job_error = "进程可能被中断，请重新处理"
                            stale_fixes.append((job.get("id"), "error", job_step, job_error))

            bucket = jobs_by_url.setdefault(yurl, [])
            if len(bucket) < 3:
                bucket.append({
                    "id": job.get("id"),
                    "status": job_status,
                    "current_step": job_step,
                    "error_message": job_error,
                    "last_successful_step": job.get("last_successful_step"),
                    "failed_step": job.get("failed_step"),
                    "created_at": job.get("created_at"),
                })

        # 批量更新修正的 job 到数据库
        if stale_fixes:
            try:
                for jid, new_status, new_step, err_msg in stale_fixes:
                    update = {"status": new_status}
                    if new_step:
                        update["current_step"] = new_step
                    if err_msg:
                        update["error_message"] = err_msg
                    elif new_status == "completed":
                        update["error_message"] = None
                    _supabase.table("pipeline_jobs").update(update).eq("id", jid).execute()
                logger.info(f"[library] auto-fixed {len(stale_fixes)} stale jobs: {[(f[1], f[2]) for f in stale_fixes]}")
            except Exception as ex:
                logger.warning(f"[library] stale job fix failed: {ex}")

    db_video_ids = sorted({a.get("db_video_id") for a in assets if a.get("db_video_id")})
    video_stats_map: Dict[str, Dict[str, Any]] = {}
    if db_video_ids:
        try:
            video_rows = (
                _supabase.table("videos")
                .select("id,status,atom_count,segment_count,entity_count")
                .in_("id", db_video_ids)
                .execute()
                .data
            ) or []
            for row in video_rows:
                vid = row.get("id")
                if vid:
                    video_stats_map[vid] = row
        except Exception:
            video_stats_map = {}

    # 只对缓存看起来不完整的视频重新计数（避免性能问题）
    # 完整的标准：atom_count > 0 AND segment_count > 0
    missing_ids = [
        vid for vid in db_video_ids
        if int((video_stats_map.get(vid) or {}).get("atom_count") or 0) == 0
           or int((video_stats_map.get(vid) or {}).get("segment_count") or 0) == 0
    ]
    # entity_count 在 videos 表里不可信，对所有视频都从 entities 表重新数
    entity_recount_ids = db_video_ids
    recount_ids = sorted(set(missing_ids) | set(entity_recount_ids))
    if recount_ids:
        # 首选：get_video_stats RPC，单次往返拿全部计数（GROUP BY 在数据库侧完成）
        stats_by_vid: Dict[str, Dict[str, int]] = {}
        try:
            rpc_rows = _supabase.rpc("get_video_stats", {"p_video_ids": recount_ids}).execute().data or []
            stats_by_vid = {r["video_id"]: r for r in rpc_rows}
        except Exception as ex:
            logger.warning(f"[library] get_video_stats RPC unavailable, falling back to row scan: {ex}")

        if not stats_by_vid:
            # 兜底：旧的拉行计数（RPC 迁移尚未应用时）
            def _batch_count_by_video(table: str, video_ids: List[str]) -> Dict[str, int]:
                counts: Dict[str, int] = {}
                try:
                    for chunk_start in range(0, len(video_ids), 10):
                        chunk = video_ids[chunk_start:chunk_start+10]
                        rows = (
                            _supabase.table(table)
                            .select("video_id")
                            .in_("video_id", chunk)
                            .limit(50000)
                            .execute()
                            .data
                        ) or []
                        for r in rows:
                            v = r.get("video_id")
                            if v:
                                counts[v] = counts.get(v, 0) + 1
                except Exception as ex:
                    logger.warning(f"[library] _batch_count_by_video({table}) failed: {ex}")
                return counts

            _ac = _batch_count_by_video("atoms", missing_ids)
            _sc = _batch_count_by_video("narrative_segments", missing_ids)
            _tc = _batch_count_by_video("topics", missing_ids)
            _emc = _batch_count_by_video("atom_embeddings", missing_ids)
            _ec = _batch_count_by_video("entities", entity_recount_ids)
            for vid in recount_ids:
                stats_by_vid[vid] = {
                    "atom_count": _ac.get(vid, 0),
                    "segment_count": _sc.get(vid, 0),
                    "topic_count": _tc.get(vid, 0),
                    "embedding_count": _emc.get(vid, 0),
                    "entity_count": _ec.get(vid, 0),
                }

        for vid in missing_ids:
            row = video_stats_map.get(vid) or {}
            r = stats_by_vid.get(vid) or {}
            video_stats_map[vid] = {
                **row,
                "id": vid,
                "atom_count": int(r.get("atom_count") or 0),
                "segment_count": int(r.get("segment_count") or 0),
                "entity_count": int(r.get("entity_count") or 0),
                "topic_count": int(r.get("topic_count") or 0),
                "embedding_count": int(r.get("embedding_count") or 0),
                "status": row.get("status") or "processing",
            }
        # Patch entity_count for all videos from the authoritative entities table
        for vid in entity_recount_ids:
            if vid in video_stats_map and vid in stats_by_vid:
                video_stats_map[vid]["entity_count"] = int(stats_by_vid[vid].get("entity_count") or 0)

    for asset in assets:
        db_video_id = asset.get("db_video_id")
        stats = video_stats_map.get(db_video_id) if db_video_id else None
        inferred_step = None
        if stats:
            atom_count = int(stats.get("atom_count") or 0)
            segment_count = int(stats.get("segment_count") or 0)
            entity_count = int(stats.get("entity_count") or 0)
            topic_count = int(stats.get("topic_count") or 0)
            embedding_count = int(stats.get("embedding_count") or 0)

            # Infer the most advanced completed step from real assets
            if embedding_count > 0:
                inferred_step = "vectorized"
            elif topic_count > 0:
                inferred_step = "topics_extracted"
            elif entity_count > 0:
                inferred_step = "entities_extracted"
            elif segment_count > 0:
                inferred_step = "segmented"
            elif atom_count > 0:
                inferred_step = "atomized"

            asset["atom_count"] = atom_count
            asset["segment_count"] = segment_count
            asset["entity_count"] = entity_count
            asset["embedding_count"] = embedding_count
            asset["analysis_ready"] = embedding_count > 0
            asset["inferred_step"] = inferred_step

            if embedding_count > 0:
                asset["status"] = "processed"
            elif topic_count > 0:
                asset["status"] = "topics_extracted"
            elif entity_count > 0:
                asset["status"] = "entities_extracted"
            elif segment_count > 0:
                asset["status"] = "segmented"
            elif atom_count > 0:
                asset["status"] = "atomized"
            elif asset.get("has_srt"):
                asset["status"] = "transcribed"
            elif asset.get("audio_url"):
                asset["status"] = "audio_ready"
        elif db_video_id and asset.get("status") in {
            "processed", "vectorized", "topics_extracted",
            "entities_extracted", "segmented", "atomized",
        }:
            if asset.get("has_srt"):
                asset["status"] = "transcribed"
            elif asset.get("audio_url"):
                asset["status"] = "audio_ready"
            asset["analysis_ready"] = False
        else:
            asset["analysis_ready"] = False

        asset["recent_jobs"] = jobs_by_url.get(asset.get("youtube_url"), [])

    return {"assets": assets}


def _infer_step_from_real_assets(video_id: str) -> Optional[str]:
    """Infer latest completed processing step from persisted Supabase assets."""
    metrics = _get_video_asset_metrics(video_id)
    if metrics["atom_count"] <= 0:
        return None
    if metrics["segment_count"] <= 0:
        return "atomized"
    if metrics["embedding_count"] > 0:
        return "vectorized"
    if metrics["topic_count"] > 0:
        return "topics_extracted"
    if metrics["atom_entity_count"] > 0:
        return "entities_extracted"
    return "segmented"


def _get_video_asset_metrics(video_id: Optional[str]) -> Dict[str, int]:
    """Collect persisted analysis metrics for one video_id from Supabase."""
    if not _supabase or not video_id:
        return {
            "atom_count": 0,
            "segment_count": 0,
            "topic_count": 0,
            "atom_entity_count": 0,
            "embedding_count": 0,
        }

    def _count_rows(table: str, column: str = "video_id") -> int:
        try:
            res = (
                _supabase.table(table)
                .select("id", count="exact")
                .eq(column, video_id)
                .limit(1)
                .execute()
            )
            return int(res.count or 0)
        except Exception:
            return 0

    atom_count = _count_rows("atoms")
    segment_count = _count_rows("narrative_segments")
    topic_count = _count_rows("topics")

    atom_entity_count = 0
    embedding_count = 0
    if atom_count > 0:
        atom_ids: List[str] = []
        try:
            atom_rows = (
                _supabase.table("atoms")
                .select("id")
                .eq("video_id", video_id)
                .limit(2000)
                .execute()
                .data
            )
            atom_ids = [row.get("id") for row in (atom_rows or []) if row.get("id")]
        except Exception:
            atom_ids = []

        if atom_ids:
            try:
                ae = (
                    _supabase.table("atom_entities")
                    .select("atom_id", count="exact")
                    .in_("atom_id", atom_ids)
                    .limit(1)
                    .execute()
                )
                atom_entity_count = int(ae.count or 0)
            except Exception:
                atom_entity_count = 0
            try:
                emb = (
                    _supabase.table("atom_embeddings")
                    .select("atom_id", count="exact")
                    .in_("atom_id", atom_ids)
                    .limit(1)
                    .execute()
                )
                embedding_count = int(emb.count or 0)
            except Exception:
                embedding_count = 0

    return {
        "atom_count": atom_count,
        "segment_count": segment_count,
        "topic_count": topic_count,
        "atom_entity_count": atom_entity_count,
        "embedding_count": embedding_count,
    }


def _prune_old_jobs(youtube_url: str, keep: int = 3) -> None:
    """Delete all but the newest `keep` jobs for a URL.
    Keeps pipeline_jobs from growing unboundedly (retries used to pile up forever)."""
    try:
        rows = (
            _supabase.table("pipeline_jobs")
            .select("id")
            .eq("youtube_url", youtube_url)
            .order("created_at", desc=True)
            .execute()
            .data
        ) or []
        stale_ids = [r["id"] for r in rows[keep:]]
        if stale_ids:
            _supabase.table("pipeline_jobs").delete().in_("id", stale_ids).execute()
    except Exception as ex:
        logger.warning(f"[jobs] prune failed for {youtube_url}: {ex}")


def _insert_pipeline_job(payload: Dict[str, Any]) -> None:
    """Insert pipeline job with compatibility for deployments missing step columns."""
    try:
        _supabase.table("pipeline_jobs").insert(payload).execute()
    except Exception:
        fallback = {
            k: v for k, v in payload.items()
            if k not in {"last_successful_step", "failed_step"}
        }
        _supabase.table("pipeline_jobs").insert(fallback).execute()
    if payload.get("youtube_url"):
        _prune_old_jobs(payload["youtube_url"])


@app.post("/api/library/retry")
async def retry_asset(request: IngestRequest, background_tasks: BackgroundTasks):
    """重新提交 job（缓存层自动跳过已完成步骤）"""
    if not _supabase:
        raise HTTPException(status_code=503, detail="Supabase 未配置")
    job_id = str(uuid.uuid4())
    inherited_last_successful_step = None
    inherited_failed_step = None
    try:
        latest = _supabase.table("pipeline_jobs") \
            .select("last_successful_step,failed_step,status") \
            .eq("youtube_url", request.youtube_url) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()
        if latest.data:
            prev = latest.data[0]
            inherited_last_successful_step = prev.get("last_successful_step")
            inherited_failed_step = prev.get("failed_step")
    except Exception:
        pass

    try:
        asset = _supabase.table("video_assets") \
            .select("status,db_video_id,audio_url,srt_content") \
            .eq("youtube_url", request.youtube_url) \
            .limit(1) \
            .execute()
        if asset.data:
            current_asset = asset.data[0]
            has_db_video = bool(current_asset.get("db_video_id"))
            inferred_from_db = _infer_step_from_real_assets(current_asset.get("db_video_id"))
            if inferred_from_db:
                inherited_last_successful_step = inferred_from_db
            elif not inherited_last_successful_step:
                # If db_video_id exists but no persisted analysis assets were found,
                # do not trust asset status=processed. Fall back to true prerequisites.
                if current_asset.get("srt_content"):
                    inherited_last_successful_step = "transcribed"
                elif current_asset.get("audio_url"):
                    inherited_last_successful_step = "audio_ready"
                elif not has_db_video:
                    asset_status = current_asset.get("status")
                    if asset_status in {
                        "audio_ready", "transcribed", "atomized", "segmented",
                        "entities_extracted", "topics_extracted", "vectorized", "processed"
                    }:
                        inherited_last_successful_step = asset_status
    except Exception:
        pass

    if not inherited_last_successful_step:
        try:
            asset = _supabase.table("video_assets") \
                .select("status,db_video_id,srt_content,audio_url") \
                .eq("youtube_url", request.youtube_url) \
                .limit(1) \
                .execute()
            if asset.data:
                row = asset.data[0]
                if row.get("srt_content"):
                    inherited_last_successful_step = "transcribed"
                elif row.get("audio_url"):
                    inherited_last_successful_step = "audio_ready"
                elif not row.get("db_video_id"):
                    asset_status = row.get("status")
                    if asset_status in {
                        "audio_ready", "transcribed", "atomized", "segmented",
                        "entities_extracted", "topics_extracted", "vectorized", "processed"
                    }:
                        inherited_last_successful_step = asset_status
        except Exception:
            pass

    _insert_pipeline_job({
        "id": job_id,
        "youtube_url": request.youtube_url,
        "status": "pending",
        "current_step": "继续处理...",
        "last_successful_step": inherited_last_successful_step,
        "failed_step": inherited_failed_step,
    })

    from api.ingestion_pipeline import run_ingestion
    background_tasks.add_task(run_ingestion, job_id, request.youtube_url)
    return {"job_id": job_id, "status": "pending"}


@app.get("/api/person-network")
async def get_person_network(min_mentions: int = 3):
    """Get cached cross-video person network, filtered by minimum mention count."""
    if not person_network_service:
        raise HTTPException(status_code=503, detail="Supabase 未配置")
    cached = person_network_service.get_cached()
    if not cached:
        cached = person_network_service.rebuild(limit_videos=200)

    # 过滤 mention < min_mentions 的人物，减少传输量
    if min_mentions > 1 and isinstance(cached, dict):
        persons = cached.get("persons", [])
        kept_persons = [p for p in persons if int(p.get("mention_count") or 0) >= min_mentions]
        kept_ids = {p.get("person_id") for p in kept_persons}
        kept_names = {p.get("name") for p in kept_persons if p.get("name")}

        # 过滤关系：relation 用 source_name/target_name（或 source/target 兼容）
        relations = cached.get("relations", [])
        kept_relations = []
        for r in relations:
            src = r.get("source_name") or r.get("source") or ""
            tgt = r.get("target_name") or r.get("target") or ""
            if src in kept_names and tgt in kept_names:
                kept_relations.append(r)

        # 过滤仕途路线
        career_routes = cached.get("career_routes", [])
        kept_careers = [
            c for c in career_routes
            if c.get("person_id") in kept_ids or c.get("name") in kept_names
        ]

        cached = {
            **cached,
            "persons": kept_persons,
            "relations": kept_relations,
            "career_routes": kept_careers,
            "stats": {
                **(cached.get("stats") or {}),
                "total_persons": len(kept_persons),
                "total_relations": len(kept_relations),
                "filtered_by_min_mentions": min_mentions,
            },
        }

    return cached


_rebuild_jobs: dict = {}  # job_id -> progress dict
_rebuild_current: dict = {"id": None}  # dedupe: only one rebuild at a time


def _start_background_rebuild(limit: int = 300, use_ai: bool = False, ai_model: str = "claude-haiku-4-5") -> str:
    """Kick off a person-network rebuild in a background thread.

    Rebuilds took minutes inline and starved /api/chat on the single
    container — every caller (merge/rename/rebuild) now goes through here
    and returns immediately. Duplicate requests reuse the running job.
    """
    import uuid, threading
    running_id = _rebuild_current.get("id")
    if running_id and _rebuild_jobs.get(running_id, {}).get("status") == "running":
        return running_id

    job_id = str(uuid.uuid4())[:8]
    _rebuild_jobs[job_id] = {"status": "running", "total": 0, "done": 0, "current": "", "error": None}
    _rebuild_current["id"] = job_id
    safe_limit = max(1, min(int(limit or 300), 1000))

    def _run():
        try:
            def _progress(p: dict):
                _rebuild_jobs[job_id].update(p)
            person_network_service._progress_callback = _progress
            person_network_service.rebuild(limit_videos=safe_limit, use_ai=use_ai, ai_model=ai_model)
            _rebuild_jobs[job_id]["status"] = "done"
        except Exception as e:
            _rebuild_jobs[job_id]["status"] = "error"
            _rebuild_jobs[job_id]["error"] = str(e)
            logger.error(f"rebuild job {job_id} failed: {e}")
        finally:
            person_network_service._progress_callback = None

    threading.Thread(target=_run, daemon=True).start()
    return job_id


@app.post("/api/person-network/rebuild")
async def rebuild_person_network(limit: int = 200, use_ai: bool = False, ai_model: str = "claude-sonnet-4-6"):
    """Rebuild cross-video person network — always async, returns job_id."""
    if not person_network_service:
        raise HTTPException(status_code=503, detail="Supabase 未配置")
    job_id = _start_background_rebuild(limit=limit, use_ai=use_ai, ai_model=ai_model)
    return {"job_id": job_id, "status": "running"}


@app.get("/api/person-network/rebuild/status/{job_id}")
async def get_rebuild_status(job_id: str):
    """Poll AI rebuild progress."""
    job = _rebuild_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/person-network/person/{person_id}")
async def get_person_network_person(person_id: str, use_ai: bool = True, ai_model: str = "claude-haiku-4-5"):
    """Get person card detail: summary + relation index + related atoms."""
    if not person_network_service:
        raise HTTPException(status_code=503, detail="Supabase 未配置")
    try:
        return person_network_service.get_person_detail(person_id=person_id, use_ai=use_ai, ai_model=ai_model)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting person detail: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/person-network/aliases")
async def get_person_network_aliases():
    """Get manual person alias map used by merge logic."""
    if not person_network_service:
        raise HTTPException(status_code=503, detail="Supabase not available")
    try:
        aliases = person_network_service.get_aliases()
        return {"aliases": aliases, "count": len(aliases)}
    except Exception as e:
        logger.error(f"Error getting person aliases: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/videos/{video_id}/resegment")
async def resegment_video(video_id: str):
    """Resegment a video using AI semantic segmentation (two-pass Haiku). Streams progress."""
    if not _supabase:
        raise HTTPException(status_code=503, detail="Supabase unavailable")
    if not CLAUDE_API_KEY:
        raise HTTPException(status_code=503, detail="CLAUDE_API_KEY not set")

    from structurers.semantic_segmenter import SemanticSegmenter

    def generate():
        try:
            # 1. Load atoms
            yield json.dumps({"step": "loading", "message": "加载原子数据..."}) + "\n"
            atoms_res = (_supabase.table("atoms")
                .select("id,merged_text,start_ms,end_ms,video_id")
                .eq("video_id", video_id)
                .order("start_ms")
                .limit(3000)
                .execute())
            atoms = atoms_res.data or []
            if not atoms:
                yield json.dumps({"step": "error", "message": f"未找到原子: {video_id}"}) + "\n"
                return

            # 2. Load entities
            yield json.dumps({"step": "loading", "message": f"已加载 {len(atoms)} 个原子，加载实体..."}) + "\n"
            atom_ids = [a["id"] for a in atoms]
            all_entities = []
            for i in range(0, len(atom_ids), 200):
                chunk = atom_ids[i:i+200]
                ent_res = (_supabase.table("atom_entities")
                    .select("atom_id,entity_name,entity_type")
                    .in_("atom_id", chunk)
                    .execute())
                all_entities.extend(ent_res.data or [])

            # 2b. Load aliases and resolve entity names
            alias_map: Dict[str, str] = {}
            try:
                for key in ("person_network_aliases_v1", "entity_aliases_v1"):
                    row = _supabase.table("app_settings").select("value").eq("key", key).execute()
                    if row.data:
                        raw = row.data[0].get("value", {})
                        if isinstance(raw, str):
                            raw = json.loads(raw)
                        for alias, val in raw.items():
                            canonical = val if isinstance(val, str) else val.get("canonical", "")
                            if canonical:
                                alias_map[alias] = canonical
            except Exception as ex:
                logger.warning(f"[resegment] alias load failed: {ex}")

            if alias_map:
                for e in all_entities:
                    name = e.get("entity_name", "")
                    if name in alias_map:
                        e["entity_name"] = f"{alias_map[name]}({name})"

            yield json.dumps({"step": "splitting", "message": f"第1轮：对 {len(atoms)} 个原子做全局话题切分..."}) + "\n"
            logger.info(f"[resegment] video={video_id} atoms={len(atoms)} entities={len(all_entities)} aliases={len(alias_map)}")

            # 3. Run semantic segmenter with progress callback
            _xai_key = os.environ.get("XAI_API_KEY", "")
            segmenter = SemanticSegmenter(CLAUDE_API_KEY, xai_api_key=_xai_key)

            # Build entity index for pass 1
            entity_by_atom: Dict[str, List[Dict[str, Any]]] = {}
            for e in all_entities:
                entity_by_atom.setdefault(e["atom_id"], []).append(e)

            split_points = segmenter._find_split_points(atoms, entity_by_atom)
            groups = segmenter._split_atoms_by_points(atoms, split_points)

            yield json.dumps({"step": "splitting_done", "message": f"切分完成：{len(groups)} 个片段，开始精炼..."}) + "\n"

            # Pass 2: refine each group
            segments = []
            for i, group in enumerate(groups):
                yield json.dumps({
                    "step": "refining",
                    "message": f"精炼片段 {i+1}/{len(groups)}（{len(group)} 个原子）...",
                    "progress": round((i / len(groups)) * 100),
                }) + "\n"
                group_entities = []
                for a in group:
                    group_entities.extend(entity_by_atom.get(a["id"], []))
                seg = segmenter._refine_segment(group, group_entities, segment_num=i + 1)
                segments.append(seg)

            # 4. Delete old segments
            yield json.dumps({"step": "saving", "message": "写入数据库...", "progress": 90}) + "\n"
            _supabase.table("narrative_segments").delete().eq("video_id", video_id).execute()

            # 5. Write new segments + update atoms
            for seg in segments:
                seg_id = f"{video_id}_SEG_{seg['segment_num']:03d}"
                _supabase.table("narrative_segments").upsert({
                    "id": seg_id,
                    "video_id": video_id,
                    "title": seg["title"],
                    "summary": seg["summary"],
                    "start_ms": seg["start_ms"],
                    "end_ms": seg["end_ms"],
                    "duration_ms": seg["end_ms"] - seg["start_ms"],
                    "atom_count": seg["atom_count"],
                    "key_entities": seg["core_persons"],
                    "topics": seg["keywords"],
                }).execute()

                if seg["atom_ids"]:
                    for aid_chunk_start in range(0, len(seg["atom_ids"]), 100):
                        aid_chunk = seg["atom_ids"][aid_chunk_start:aid_chunk_start+100]
                        _supabase.table("atoms").update({"segment_id": seg_id}).in_("id", aid_chunk).execute()

            logger.info(f"[resegment] video={video_id} done: {len(segments)} segments")

            yield json.dumps({
                "step": "done",
                "message": f"完成！共 {len(segments)} 个片段",
                "progress": 100,
                "segments_count": len(segments),
            }) + "\n"

        except Exception as e:
            logger.error(f"[resegment] error: {e}")
            yield json.dumps({"step": "error", "message": str(e)}) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@app.post("/api/person-network/merge")
async def merge_person_network_people(request: PersonMergeRequest):
    """Merge two persons and rebuild cached network."""
    if not person_network_service:
        raise HTTPException(status_code=503, detail="Supabase not available")

    try:
        source_name = (request.source_name or "").strip()
        target_name = (request.target_name or "").strip()

        if (not source_name or not target_name) and (request.source_person_id and request.target_person_id):
            network = person_network_service.get_cached() or person_network_service.rebuild(limit_videos=200, use_ai=False)
            id_to_name = {p.get("person_id"): p.get("name") for p in (network.get("persons") or [])}
            source_name = source_name or str(id_to_name.get(request.source_person_id) or "")
            target_name = target_name or str(id_to_name.get(request.target_person_id) or "")

        if not source_name or not target_name:
            raise HTTPException(status_code=400, detail="source/target person are required")

        # 只做别名写入（秒级），重建放后台 —— 避免阻塞请求并拖垮 /api/chat
        result = person_network_service.merge_persons(
            source_name=source_name,
            target_name=target_name,
            limit_videos=max(1, min(int(request.limit or 300), 1000)),
            use_ai=bool(request.use_ai),
            ai_model=request.ai_model or "claude-haiku-4-5",
            rebuild=False,
        )
        if result.get("merged"):
            result["rebuild_job_id"] = _start_background_rebuild(
                limit=max(1, min(int(request.limit or 300), 1000)),
                use_ai=bool(request.use_ai),
                ai_model=request.ai_model or "claude-haiku-4-5",
            )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error merging persons: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/person-network/person/{person_id}/rename")
async def rename_person(person_id: str, request: PersonRenameRequest):
    """Rename a person in the persons table and refresh the network cache."""
    if not person_network_service:
        raise HTTPException(status_code=503, detail="Supabase 未配置")
    new_name = (request.new_name or "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="new_name is required")
    try:
        result = _supabase.table("persons").update({"name": new_name}).eq("id", person_id).execute()
        if not result.data:
            raise HTTPException(status_code=404, detail=f"Person {person_id} not found")
        rebuild_job_id = _start_background_rebuild(limit=300, use_ai=False)
        return {"person_id": person_id, "new_name": new_name, "status": "renamed",
                "rebuild_job_id": rebuild_job_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error renaming person {person_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/person-network/person/{person_id}/ask")
async def ask_person_question(person_id: str, request: PersonAskRequest):
    """Answer a question about a person using atom evidence, relations, and career events."""
    if not person_network_service:
        raise HTTPException(status_code=503, detail="Person network service unavailable")
    question = (request.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    try:
        return person_network_service.ask_person(
            person_id=person_id,
            question=question,
            ai_model=request.ai_model,
            include_web=request.include_web,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"Error answering question for person {person_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/person-network/person/{person_id}/enrich-web")
async def enrich_person_web(person_id: str):
    """Fetch and cache a person's public web profile via Tavily search."""
    if not person_network_service:
        raise HTTPException(status_code=503, detail="Person network service unavailable")
    # Get person name from cache
    network = person_network_service.get_cached()
    if not network:
        raise HTTPException(status_code=404, detail="Network cache not found; rebuild first")
    persons = network.get("persons") or []
    person = next((p for p in persons if p.get("person_id") == person_id), None)
    if not person:
        raise HTTPException(status_code=404, detail=f"Person {person_id} not found")
    try:
        return person_network_service.enrich_person_from_web(
            person_id=person_id,
            person_name=person.get("name", ""),
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"Error enriching person {person_id} from web: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/person-relations/extract")
async def extract_person_relations(request: ExtractRelationsRequest, background_tasks: BackgroundTasks):
    """Batch extract directed person relations from atom text for given video IDs."""
    if not person_relation_service:
        raise HTTPException(status_code=503, detail="PersonRelationService unavailable")
    if not request.video_ids:
        raise HTTPException(status_code=400, detail="video_ids required")
    try:
        result = person_relation_service.extract_relations_for_videos(
            video_ids=request.video_ids,
            batch_size=request.batch_size,
            model=request.model,
        )
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"Error extracting relations: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/person-relations/graph")
async def get_relations_graph(limit: int = 40, min_confidence: float = 0.5):
    """Return nodes + directed edges from persons/person_relations tables for graph rendering."""
    if not _supabase:
        raise HTTPException(status_code=503, detail="Supabase unavailable")
    try:
        # Top persons by mention_count
        persons_res = (
            _supabase.table("persons")
            .select("id,name,category,mention_count")
            .order("mention_count", desc=True)
            .limit(limit)
            .execute()
        )
        nodes = [
            {
                "id": p["id"],
                "name": p.get("name", ""),
                "category": p.get("category", "other"),
                "mention_count": p.get("mention_count", 0),
            }
            for p in (persons_res.data or [])
        ]
        node_ids = {n["id"] for n in nodes}

        # Relations between those persons
        edges_res = (
            _supabase.table("person_relations")
            .select("id,person_a_id,person_a_name,person_b_id,person_b_name,relation_type,direction,time_context,description,confidence")
            .gte("confidence", min_confidence)
            .order("confidence", desc=True)
            .limit(300)
            .execute()
        )
        edges = [
            {
                "id": str(e.get("id", "")),
                "source": e["person_a_id"],
                "source_name": e.get("person_a_name", ""),
                "target": e["person_b_id"],
                "target_name": e.get("person_b_name", ""),
                "relation_type": e.get("relation_type", "co_mentioned"),
                "direction": e.get("direction", "a_to_b"),
                "time_context": e.get("time_context") or "",
                "description": e.get("description") or "",
                "confidence": e.get("confidence", 0.7),
            }
            for e in (edges_res.data or [])
            if e.get("person_a_id") in node_ids and e.get("person_b_id") in node_ids
        ]

        return {"nodes": nodes, "edges": edges, "node_count": len(nodes), "edge_count": len(edges)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/person-relations/stats")
async def get_relation_stats(video_ids: Optional[str] = None):
    """Get counts from person_relations, person_career, persons tables."""
    if not person_relation_service:
        raise HTTPException(status_code=503, detail="PersonRelationService unavailable")
    vids = [v.strip() for v in video_ids.split(",")] if video_ids else None
    try:
        return person_relation_service.get_relation_stats(vids)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/person-relations/{person_id}")
async def get_person_graph(person_id: str, person_name: str = "", hops: int = 2):
    """Get multi-hop graph context for a person (relations + career)."""
    if not person_relation_service:
        raise HTTPException(status_code=503, detail="PersonRelationService unavailable")
    # Fallback: look up name from persons table if not provided
    if not person_name:
        try:
            res = _supabase.table("persons").select("name").eq("id", person_id).limit(1).execute()
            if res.data:
                person_name = res.data[0].get("name", "")
        except Exception:
            pass
    try:
        return person_relation_service.get_person_graph_context(
            person_id=person_id,
            person_name=person_name,
            hops=hops,
        )
    except Exception as e:
        logger.error(f"Error getting graph for {person_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/persons/sync")
async def sync_persons(request: SyncPersonsRequest):
    """Sync persons table from person-network cache."""
    if not person_relation_service or not person_network_service:
        raise HTTPException(status_code=503, detail="Services unavailable")
    # Load network payload for each video_id or all
    video_ids = request.video_ids
    if not video_ids:
        # Discover from library
        try:
            res = _supabase.table("videos").select("video_id").execute()
            video_ids = [r["video_id"] for r in (res.data or []) if r.get("video_id")]
        except Exception:
            video_ids = []
    total_synced = 0
    try:
        network = person_network_service.get_cached()
        if network:
            result = person_relation_service.sync_persons_from_cache(network)
            total_synced = result.get("synced", 0)
    except Exception as e:
        logger.error(f"sync_persons error: {e}")
    return {"synced_persons": total_synced, "video_ids_processed": len(video_ids)}


@app.post("/api/persons/enrich-web-batch")
async def enrich_persons_web_batch(request: EnrichPersonsWebRequest):
    """Tavily + Claude: enrich career timelines for given person names."""
    if not person_relation_service:
        raise HTTPException(status_code=503, detail="PersonRelationService unavailable")
    if not request.person_names:
        raise HTTPException(status_code=400, detail="person_names required")
    try:
        return person_relation_service.enrich_top_persons_from_web(
            person_names=request.person_names,
            model=request.model,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"Error enriching persons from web: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/atoms/search")
async def search_atoms(request: AtomSearchRequest):
    """Semantic search over all atoms using OpenAI embeddings + pgvector."""
    if not _supabase:
        raise HTTPException(status_code=503, detail="Supabase unavailable")
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY not set")
    query = (request.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query required")
    try:
        query_vec = _embed_text(query)

        # pgvector similarity search via Supabase RPC
        rpc_params = {"query_embedding": query_vec, "match_count": min(request.limit, 50)}
        if request.video_ids:
            rpc_params["video_ids_filter"] = request.video_ids
        matches = _supabase.rpc("match_atoms_v1", rpc_params).execute()
        rows = matches.data or []

        if not rows:
            return {"query": query, "results": [], "total": 0}

        # Fetch atom text for matched atom_ids
        atom_ids = [r["atom_id"] for r in rows]
        atoms_res = (
            _supabase.table("atoms")
            .select("id,merged_text,start_ms,end_ms,video_id")
            .in_("id", atom_ids)
            .execute()
        )
        atom_map = {a["id"]: a for a in (atoms_res.data or [])}

        results = []
        for r in rows:
            atom = atom_map.get(r["atom_id"]) or {}
            results.append({
                "atom_id": r["atom_id"],
                "video_id": r.get("video_id") or atom.get("video_id", ""),
                "similarity": round(float(r["similarity"]), 4),
                "text": atom.get("merged_text", ""),
                "start_ms": atom.get("start_ms"),
                "end_ms": atom.get("end_ms"),
            })

        return {"query": query, "results": results, "total": len(results)}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"atom search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Alias Endpoints ====================

class AliasWriteRequest(BaseModel):
    alias: str
    canonical: str
    type: str = "person"  # person | organization | location | other

_ENTITY_ALIASES_KEY = "entity_aliases_v1"

def _load_entity_aliases() -> dict:
    if not _supabase:
        return {}
    try:
        import json as _j
        row = _supabase.table("app_settings").select("value").eq("key", _ENTITY_ALIASES_KEY).execute()
        return _j.loads(row.data[0]["value"]) if row.data else {}
    except Exception:
        return {}

def _save_entity_aliases(aliases: dict) -> None:
    import json as _j
    _supabase.table("app_settings").upsert({
        "key": _ENTITY_ALIASES_KEY,
        "value": _j.dumps(aliases, ensure_ascii=False),
    }).execute()

@app.get("/api/aliases")
async def get_aliases():
    """Return entity_aliases_v1."""
    if not _supabase:
        raise HTTPException(status_code=503, detail="Supabase unavailable")
    return {"aliases": _load_entity_aliases()}

@app.post("/api/aliases")
async def add_alias(req: AliasWriteRequest):
    """Add or update one alias entry."""
    if not _supabase:
        raise HTTPException(status_code=503, detail="Supabase unavailable")
    alias = req.alias.strip()
    canonical = req.canonical.strip()
    if not alias or not canonical:
        raise HTTPException(status_code=400, detail="alias and canonical are required")
    if alias == canonical:
        raise HTTPException(status_code=400, detail="alias and canonical must differ")
    aliases = _load_entity_aliases()
    aliases[alias] = {"canonical": canonical, "type": req.type}
    _save_entity_aliases(aliases)

    # If person alias, sync to person_network_aliases_v1 so fast-rebuild picks it up
    if req.type == "person":
        import json as _j2
        _PN_KEY = "person_network_aliases_v1"
        try:
            pn_row = _supabase.table("app_settings").select("value").eq("key", _PN_KEY).execute()
            pn_map: dict = _j2.loads(pn_row.data[0]["value"]) if pn_row.data else {}
            pn_map[alias] = canonical
            _supabase.table("app_settings").upsert({
                "key": _PN_KEY,
                "value": _j2.dumps(pn_map, ensure_ascii=False),
            }).execute()
        except Exception as _sync_err:
            logger.warning(f"Failed to sync person alias to person_network_aliases_v1: {_sync_err}")

    return {"ok": True, "alias": alias, "canonical": canonical, "type": req.type}

@app.delete("/api/aliases/{alias}")
async def delete_alias(alias: str):
    """Remove one alias entry."""
    if not _supabase:
        raise HTTPException(status_code=503, detail="Supabase unavailable")
    aliases = _load_entity_aliases()
    if alias not in aliases:
        raise HTTPException(status_code=404, detail=f"Alias '{alias}' not found")
    del aliases[alias]
    _save_entity_aliases(aliases)
    return {"ok": True, "deleted": alias}


@app.delete("/api/person-profiles/cache")
async def clear_person_summary_caches():
    """Delete all legacy person_profile_v1_* entries from app_settings (old Haiku summary cache)."""
    if not _supabase:
        raise HTTPException(status_code=503, detail="Supabase unavailable")
    # Fetch all keys that match the old pattern
    rows = _supabase.table("app_settings").select("key").like("key", "person_profile_v1_%").execute()
    keys = [r["key"] for r in (rows.data or [])]
    if keys:
        _supabase.table("app_settings").delete().in_("key", keys).execute()
    return {"ok": True, "deleted_keys": keys}


# ==================== Atom Text Edit ====================

class AtomTextUpdateRequest(BaseModel):
    merged_text: str
    entity_hint: str = ""  # optional extra instruction injected into entity extraction prompt

@app.patch("/api/atoms/{atom_id}/text")
async def update_atom_text(atom_id: str, req: AtomTextUpdateRequest):
    """Update atom merged_text, re-vectorize the atom, re-extract entities."""
    if not _supabase:
        raise HTTPException(status_code=503, detail="Supabase unavailable")
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY not set")

    new_text = req.merged_text.strip()
    if not new_text:
        raise HTTPException(status_code=400, detail="merged_text is required")

    # 1. Update atoms table
    upd = _supabase.table("atoms").update({"merged_text": new_text}).eq("id", atom_id).execute()
    if not upd.data:
        raise HTTPException(status_code=404, detail=f"Atom {atom_id} not found")

    atom_row = upd.data[0]
    video_id = atom_row.get("video_id", "")

    # 2. Re-vectorize: embed new text → upsert atom_embeddings
    try:
        emb = _embed_text(new_text)
        _supabase.table("atom_embeddings").upsert({
            "atom_id": atom_id,
            "video_id": video_id,
            "embedding": emb,
        }).execute()
        vectorized = True
    except Exception as ve:
        logger.error(f"Re-vectorize failed for {atom_id}: {ve}")
        vectorized = False

    # 3. Re-extract entities via Claude
    entities_updated = False
    if CLAUDE_API_KEY:
        try:
            import anthropic as _ant
            _ac = _ant.Anthropic(api_key=CLAUDE_API_KEY)
            hint_section = f"\n补充说明：{req.entity_hint.strip()}" if req.entity_hint.strip() else ""
            prompt = (
                "从以下文本中提取命名实体，返回 JSON 数组，每项格式：\n"
                "{\"name\": \"实体名\", \"type\": \"PERSON|ORGANIZATION|LOCATION|OTHER\", \"description\": \"简短说明\"}\n"
                f"{hint_section}\n"
                f"文本：{new_text}\n\n只返回 JSON 数组，无其他内容。"
            )
            resp = _ac.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            import json as _j, re as _re
            raw = resp.content[0].text.strip()
            m = _re.search(r'\[.*\]', raw, _re.DOTALL)
            if m:
                ents = _j.loads(m.group())
                # Delete old atom_entities for this atom, then insert new ones
                _supabase.table("atom_entities").delete().eq("atom_id", atom_id).execute()
                if ents:
                    rows = [{
                        "atom_id": atom_id,
                        "video_id": video_id,
                        "entity_name": e.get("name", ""),
                        "entity_type": e.get("type", "OTHER"),
                        "confidence": 0.9,
                    } for e in ents if e.get("name")]
                    if rows:
                        _supabase.table("atom_entities").insert(rows).execute()
                entities_updated = True
        except Exception as ee:
            logger.error(f"Re-extract entities failed for {atom_id}: {ee}")

    return {
        "ok": True,
        "atom_id": atom_id,
        "vectorized": vectorized,
        "entities_updated": entities_updated,
        "note": "群英图需手动 rebuild",
    }


@app.get("/api/me/role")
async def get_my_role(user_id: str):
    """Get role for a given user_id from user_roles table (bypasses RLS via service_role)."""
    if not _supabase_admin:
        return {"role": "viewer"}
    try:
        result = _supabase_admin.table("user_roles").select("role").eq("user_id", user_id).maybe_single().execute()
        _role_data = _maybe_data(result)
        return {"role": _role_data["role"] if _role_data else "viewer"}
    except Exception as e:
        logger.warning(f"get_my_role error: {e}")
        return {"role": "viewer"}


@app.post("/api/admin/set-role")
async def set_user_role(email: str, role: str = "editor", authorization: str = Header(None)):
    """Set role for a user by email. Owner only (via Supabase auth)."""
    if not _supabase_admin:
        raise HTTPException(status_code=503, detail="Supabase unavailable")
    # 验证调用者是 owner
    caller_id = await _get_user_id(authorization)
    if not caller_id:
        raise HTTPException(status_code=401, detail="请先登录")
    caller_role = _supabase_admin.table("user_roles").select("role").eq("user_id", caller_id).maybe_single().execute()
    if not _maybe_data(caller_role) or _maybe_data(caller_role).get("role") != "owner":
        raise HTTPException(status_code=403, detail="仅管理员可操作")
    if role not in ("owner", "editor", "viewer"):
        raise HTTPException(status_code=400, detail="角色必须是 owner/editor/viewer")
    try:
        users = _supabase_admin.auth.admin.list_users()
        target_user = None
        for u in users:
            if getattr(u, 'email', '') == email:
                target_user = u
                break
        if not target_user:
            raise HTTPException(status_code=404, detail=f"用户 {email} 未注册")
        target_id = target_user.id
        _supabase_admin.table("user_roles").upsert({"user_id": target_id, "role": role}).execute()
        return {"email": email, "user_id": target_id, "role": role}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/admin/users")
async def list_users(authorization: str = Header(None)):
    """List all users with their roles. Owner only."""
    if not _supabase_admin:
        raise HTTPException(status_code=503, detail="Supabase unavailable")
    caller_id = await _get_user_id(authorization)
    if not caller_id:
        raise HTTPException(status_code=401, detail="请先登录")
    caller_role = _supabase_admin.table("user_roles").select("role").eq("user_id", caller_id).maybe_single().execute()
    if not _maybe_data(caller_role) or _maybe_data(caller_role).get("role") != "owner":
        raise HTTPException(status_code=403, detail="仅管理员可操作")
    try:
        users = _supabase_admin.auth.admin.list_users()
        roles_res = _supabase_admin.table("user_roles").select("user_id,role").execute()
        role_map = {r["user_id"]: r["role"] for r in (roles_res.data or [])}
        credits_res = _supabase_admin.table("user_credits").select("user_id,balance").execute()
        credit_map = {r["user_id"]: r["balance"] for r in (credits_res.data or [])}
        result = []
        for u in users:
            result.append({
                "user_id": u.id,
                "email": getattr(u, 'email', ''),
                "role": role_map.get(u.id, "viewer"),
                "credits": credit_map.get(u.id, SIGNUP_BONUS_CREDITS),
                "created_at": str(getattr(u, 'created_at', '')),
            })
        return {"users": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Billing Endpoints ====================

# 订阅计划与加油包(price ID 来自 Railway 环境变量,由 _stripe_setup.py 创建)
BILLING_PLANS = {
    "pro":   {"credits": 600,  "mode": "subscription", "env": "STRIPE_PRICE_PRO",
              "name": "Pro 订阅", "price_label": "$5.99/月"},
    "max":   {"credits": 2000, "mode": "subscription", "env": "STRIPE_PRICE_MAX",
              "name": "Max 订阅", "price_label": "$14.99/月"},
    "topup": {"credits": 200,  "mode": "payment", "env": "STRIPE_PRICE_TOPUP",
              "name": "加油包", "price_label": "$2.99"},
}


def _plan_price_id(plan_key: str) -> str:
    plan = BILLING_PLANS.get(plan_key) or {}
    return os.environ.get(plan.get("env", ""), "")


def _grant_credits(user_id: str, credits: int, tx_type: str, description: str,
                   stripe_ref: str = "", plan: str = None, period_end: str = None) -> None:
    """幂等发放积分(stripe_ref 已存在则跳过),并可更新订阅状态。"""
    if stripe_ref:
        existing = _supabase_admin.table("credit_transactions") \
            .select("id").eq("stripe_session_id", stripe_ref).maybe_single().execute()
        if _maybe_data(existing):
            return
    current = _supabase_admin.table("user_credits") \
        .select("balance").eq("user_id", user_id).maybe_single().execute()
    cur = _maybe_data(current)
    update = {}
    if plan is not None:
        update["plan"] = plan
    if period_end is not None:
        update["sub_period_end"] = period_end
    if cur:
        update["balance"] = cur["balance"] + credits
        _supabase_admin.table("user_credits").update(update).eq("user_id", user_id).execute()
    else:
        update.update({"user_id": user_id, "balance": SIGNUP_BONUS_CREDITS + credits})
        _supabase_admin.table("user_credits").insert(update).execute()
    txn = {"user_id": user_id, "amount": credits, "type": tx_type, "description": description}
    if stripe_ref:
        txn["stripe_session_id"] = stripe_ref
    _supabase_admin.table("credit_transactions").insert(txn).execute()

@app.get("/api/billing/credits")
async def get_credits(authorization: str = Header(None)):
    """Get current user's credit balance and transaction history."""
    user_id = await _get_user_id(authorization)
    if not BILLING_ENABLED:
        return {"billing_enabled": False, "balance": None, "transactions": [], "lu_cost": 0}
    if not user_id:
        raise HTTPException(status_code=401, detail="Please sign in")

    row = _supabase_admin.table("user_credits") \
        .select("balance,created_at,plan,sub_period_end").eq("user_id", user_id).maybe_single().execute()
    row_data = _maybe_data(row)

    if not row_data:
        _supabase_admin.table("user_credits").insert({
            "user_id": user_id, "balance": SIGNUP_BONUS_CREDITS
        }).execute()
        _supabase_admin.table("credit_transactions").insert({
            "user_id": user_id, "amount": SIGNUP_BONUS_CREDITS,
            "type": "signup_bonus", "description": f"Welcome bonus: {SIGNUP_BONUS_CREDITS} free credits"
        }).execute()
        balance = SIGNUP_BONUS_CREDITS
    else:
        balance = row_data["balance"]

    txns = _supabase_admin.table("credit_transactions") \
        .select("*").eq("user_id", user_id) \
        .order("created_at", desc=True).limit(50).execute()

    return {"balance": balance, "transactions": txns.data or [],
            "plan": (row_data or {}).get("plan"),
            "sub_period_end": (row_data or {}).get("sub_period_end"),
            "lu_cost": LU_CONSULT_COST, "billing_enabled": True}


class CheckoutRequest(BaseModel):
    pack: str

@app.post("/api/billing/checkout")
async def create_checkout(request: CheckoutRequest, authorization: str = Header(None)):
    """Create a Stripe Checkout Session(订阅 pro/max 或一次性 topup)。"""
    if not BILLING_ENABLED:
        raise HTTPException(status_code=404, detail="billing disabled")
    user_id = await _get_user_id(authorization)
    if not user_id:
        raise HTTPException(status_code=401, detail="Please sign in")

    plan_key = request.pack
    plan = BILLING_PLANS.get(plan_key)
    price_id = _plan_price_id(plan_key)
    if not plan or not price_id:
        raise HTTPException(status_code=400, detail="Invalid plan")

    import stripe
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not stripe.api_key:
        raise HTTPException(status_code=503, detail="Stripe not configured")

    frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:5173")
    params = {
        "mode": plan["mode"],
        "line_items": [{"price": price_id, "quantity": 1}],
        "metadata": {"user_id": user_id, "plan": plan_key, "credits": str(plan["credits"])},
        "success_url": f"{frontend_url}/billing?status=success",
        "cancel_url": f"{frontend_url}/billing?status=cancelled",
    }
    if plan["mode"] == "subscription":
        # 让订阅对象带上 user_id,后续每期 invoice.paid 都能找到用户
        params["subscription_data"] = {"metadata": {"user_id": user_id, "plan": plan_key,
                                                    "credits": str(plan["credits"])}}
    session = stripe.checkout.Session.create(**params)

    return {"checkout_url": session.url, "session_id": session.id}


from fastapi import Request as _FastAPIRequest

@app.post("/api/webhooks/stripe")
async def stripe_webhook(request: _FastAPIRequest):
    if not BILLING_ENABLED:
        raise HTTPException(status_code=404, detail="billing disabled")
    """Handle Stripe webhook events for credit fulfillment."""
    import stripe
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid signature")

    etype = event["type"]
    obj = event["data"]["object"]

    if etype == "checkout.session.completed":
        # 只处理一次性加油包;订阅的积分由 invoice.paid 发放(避免双发)
        if obj.get("mode") == "payment":
            meta = obj.get("metadata", {}) or {}
            user_id = meta.get("user_id")
            credits = int(meta.get("credits", 0) or 0)
            if user_id and credits > 0:
                _grant_credits(user_id, credits, "purchase",
                               f"加油包 +{credits} 积分", stripe_ref=obj["id"])

    elif etype == "invoice.paid":
        # 订阅首期与每期续费:按订阅 metadata 发放当月积分
        sub_id = obj.get("subscription")
        meta = {}
        period_end = None
        try:
            if sub_id:
                import stripe as _st
                sub = _st.Subscription.retrieve(sub_id)
                meta = sub.get("metadata", {}) or {}
                try:
                    from datetime import datetime as _dt, timezone as _tz
                    period_end = _dt.fromtimestamp(
                        sub["current_period_end"], tz=_tz.utc).isoformat()
                except Exception:
                    pass
        except Exception as ex:
            logger.warning(f"[stripe] subscription retrieve failed: {ex}")
        user_id = meta.get("user_id")
        plan = meta.get("plan", "")
        credits = int(meta.get("credits", 0) or 0)
        if user_id and credits > 0:
            _grant_credits(user_id, credits, "subscription",
                           f"{BILLING_PLANS.get(plan, {}).get('name', plan)} 月度积分 +{credits}",
                           stripe_ref=obj["id"], plan=plan, period_end=period_end)

    elif etype == "customer.subscription.deleted":
        meta = obj.get("metadata", {}) or {}
        user_id = meta.get("user_id")
        if user_id:
            try:
                _supabase_admin.table("user_credits").update(
                    {"plan": None, "sub_period_end": None}).eq("user_id", user_id).execute()
            except Exception as ex:
                logger.warning(f"[stripe] clear plan failed: {ex}")

    return {"received": True}


# ── 服务角色:api(产品) / engine(处理引擎)──────────────────────
# 双服务架构:产品服务频繁部署不打断引擎;引擎跑 worker/巡航/重活。
SERVICE_MODE = os.environ.get("SERVICE_MODE", "api")
ENGINE_URL = os.environ.get("ENGINE_URL", "").rstrip("/")


def _proxy_to_engine(method: str, path: str, json_body=None, headers=None):
    """api 服务把引擎控制类请求转发给 engine 服务。"""
    import requests as _rq
    r = _rq.request(method, f"{ENGINE_URL}{path}", json=json_body,
                    headers=headers or {}, timeout=30)
    try:
        return r.json()
    except Exception:
        return {"status_code": r.status_code, "text": r.text[:200]}


# ── 服务器端自动补齐 worker ──────────────────────────────────────
from api.backfill_worker import BackfillWorker
_backfill_worker = BackfillWorker(_supabase_admin) if _supabase_admin else None

# ── X 推文定时抓取 worker ────────────────────────────────────────
from api.x_poller import XPoller
from config import TWITTERAPI_KEY as _TW_KEY
_x_poller = XPoller(_supabase_admin, _TW_KEY) if _supabase_admin else None

# ── 人格观点库构建 worker ────────────────────────────────────────
from api.persona_builder import PersonaBuilder
_persona_builder = PersonaBuilder(_supabase_admin) if _supabase_admin else None


def _check_admin_key(x_admin_key: str) -> None:
    """管理端点鉴权:优先独立的 ADMIN_KEY;未设置时兼容旧部署的 SUPABASE_SERVICE。"""
    import hmac
    expected = os.environ.get("ADMIN_KEY") or os.environ.get("SUPABASE_SERVICE", "")
    if not expected or not x_admin_key or not hmac.compare_digest(x_admin_key, expected):
        raise HTTPException(status_code=403, detail="forbidden")


class BackfillStartRequest(BaseModel):
    channel_url: str


@app.post("/api/admin/backfill/start")
async def backfill_start(request: BackfillStartRequest, x_admin_key: str = Header(None)):
    """启动服务器端自动补齐（常驻：补完存量后每 6 小时自动巡航新视频）。"""
    _check_admin_key(x_admin_key)
    if SERVICE_MODE != "engine" and ENGINE_URL:
        return _proxy_to_engine("POST", "/api/admin/backfill/start",
                                json_body={"channel_url": request.channel_url},
                                headers={"X-Admin-Key": x_admin_key})
    if not _backfill_worker:
        raise HTTPException(status_code=503, detail="Supabase unavailable")
    return _backfill_worker.start(request.channel_url.strip())


@app.post("/api/admin/backfill/stop")
async def backfill_stop(x_admin_key: str = Header(None)):
    _check_admin_key(x_admin_key)
    if SERVICE_MODE != "engine" and ENGINE_URL:
        return _proxy_to_engine("POST", "/api/admin/backfill/stop",
                                headers={"X-Admin-Key": x_admin_key})
    if not _backfill_worker:
        raise HTTPException(status_code=503, detail="Supabase unavailable")
    return _backfill_worker.stop()


@app.get("/api/admin/backfill/status")
async def backfill_status():
    if SERVICE_MODE != "engine" and ENGINE_URL:
        return _proxy_to_engine("GET", "/api/admin/backfill/status")
    if not _backfill_worker:
        raise HTTPException(status_code=503, detail="Supabase unavailable")
    return _backfill_worker.status


class XPollerStartRequest(BaseModel):
    username: str = ""


@app.post("/api/admin/xpoller/start")
async def xpoller_start(request: XPollerStartRequest, x_admin_key: str = Header(None)):
    """启动 X 推文定时抓取(常驻:每 4 小时增量拉新推文并向量化入库)。"""
    _check_admin_key(x_admin_key)
    if SERVICE_MODE != "engine" and ENGINE_URL:
        return _proxy_to_engine("POST", "/api/admin/xpoller/start",
                                json_body={"username": request.username},
                                headers={"X-Admin-Key": x_admin_key})
    if not _x_poller:
        raise HTTPException(status_code=503, detail="Supabase unavailable")
    return _x_poller.start(request.username.strip())


@app.post("/api/admin/xpoller/stop")
async def xpoller_stop(x_admin_key: str = Header(None)):
    _check_admin_key(x_admin_key)
    if SERVICE_MODE != "engine" and ENGINE_URL:
        return _proxy_to_engine("POST", "/api/admin/xpoller/stop",
                                headers={"X-Admin-Key": x_admin_key})
    if not _x_poller:
        raise HTTPException(status_code=503, detail="Supabase unavailable")
    return _x_poller.stop()


@app.get("/api/admin/xpoller/status")
async def xpoller_status():
    if SERVICE_MODE != "engine" and ENGINE_URL:
        return _proxy_to_engine("GET", "/api/admin/xpoller/status")
    if not _x_poller:
        raise HTTPException(status_code=503, detail="Supabase unavailable")
    return _x_poller.status


class PersonaBuildRequest(BaseModel):
    persona: str = "lu"


@app.post("/api/admin/persona/build")
async def persona_build(request: PersonaBuildRequest, x_admin_key: str = Header(None)):
    """启动人格观点库构建(遍历博主全部原子抽取观点+金句,断点续跑)。"""
    _check_admin_key(x_admin_key)
    if SERVICE_MODE != "engine" and ENGINE_URL:
        return _proxy_to_engine("POST", "/api/admin/persona/build",
                                json_body={"persona": request.persona},
                                headers={"X-Admin-Key": x_admin_key})
    if not _persona_builder:
        raise HTTPException(status_code=503, detail="Supabase unavailable")
    pcfg = _load_personas().get(request.persona)
    if not pcfg:
        raise HTTPException(status_code=400, detail=f"unknown persona: {request.persona}")
    return _persona_builder.start(request.persona, pcfg["channels"])


@app.post("/api/admin/persona/stop")
async def persona_build_stop(x_admin_key: str = Header(None)):
    _check_admin_key(x_admin_key)
    if SERVICE_MODE != "engine" and ENGINE_URL:
        return _proxy_to_engine("POST", "/api/admin/persona/stop",
                                headers={"X-Admin-Key": x_admin_key})
    if not _persona_builder:
        raise HTTPException(status_code=503, detail="Supabase unavailable")
    return _persona_builder.stop()


@app.get("/api/admin/persona/status")
async def persona_build_status():
    if SERVICE_MODE != "engine" and ENGINE_URL:
        return _proxy_to_engine("GET", "/api/admin/persona/status")
    if not _persona_builder:
        raise HTTPException(status_code=503, detail="Supabase unavailable")
    return _persona_builder.status


@app.on_event("startup")
async def _cleanup_stale_consult_jobs():
    """服务重启会杀死后台咨询线程;把遗留的 running 任务标为 error,
    避免前端空等 8 分钟。"""
    if not _supabase_admin:
        return
    try:
        _supabase_admin.table("consult_jobs").update({
            "status": "error",
            "result": {"detail": "服务已更新,请重新提问"},
        }).eq("status", "running").execute()
        stale = _supabase_admin.table("roundtables").select("id,turns")             .eq("status", "running").execute().data or []
        for row in stale:
            kept = [t for t in (row.get("turns") or []) if (t.get("content") or "").strip()]
            # 已有完整发言的标 done(可续聊),一句都没有的标 error
            _supabase_admin.table("roundtables").update({
                "status": "done" if kept else "error",
                "progress": "" if kept else "服务已更新,请重新发起",
                "turns": kept,
            }).eq("id", row["id"]).execute()
    except Exception as e:
        logger.warning(f"[consult-jobs] stale cleanup failed: {e}")


@app.on_event("startup")
async def _resume_backfill_on_boot():
    """引擎服务(SERVICE_MODE=engine)重启后自动恢复 worker;
    产品服务(api)不跑 worker —— 部署产品不再打断视频处理。"""
    if SERVICE_MODE != "engine":
        logger.info(f"[backfill] SERVICE_MODE={SERVICE_MODE}, worker disabled on this service")
        return
    if not _backfill_worker:
        return
    try:
        state = _backfill_worker.load_state()
        channels = state.get("channels") or []
        if state.get("enabled") and (channels or state.get("channel_url")):
            _backfill_worker.start(channels[0]["url"] if channels else state["channel_url"])
            logger.info(f"[backfill] resumed on boot: "
                        f"{', '.join(c['name'] for c in channels) or state.get('channel_url')}")
    except Exception as e:
        logger.warning(f"[backfill] boot resume failed: {e}")
    # X 推文 poller 同样自动恢复
    if _x_poller:
        try:
            xs = _x_poller.load_state()
            if xs.get("enabled") and xs.get("accounts"):
                _x_poller.start()
                logger.info(f"[xpoller] resumed on boot: {xs['accounts']}")
        except Exception as e:
            logger.warning(f"[xpoller] boot resume failed: {e}")


class ApplySqlRequest(BaseModel):
    sql: str

@app.post("/api/admin/apply-sql")
async def apply_sql(request: ApplySqlRequest, x_admin_key: str = Header(None)):
    """Run DDL/SQL against the database from the server side.

    Exists because operator machines often cannot reach Supabase Postgres
    directly (cross-border network); Railway can. Guarded by the service_role
    key, which the caller must already hold to administer this system at all.
    """
    if os.environ.get("ENABLE_ADMIN_SQL", "").lower() not in ("1", "true", "yes"):
        raise HTTPException(status_code=404, detail="not found")
    _check_admin_key(x_admin_key)
    import hmac
    expected = os.environ.get("SUPABASE_SERVICE", "")
    if not expected or not x_admin_key or not hmac.compare_digest(x_admin_key, expected):
        raise HTTPException(status_code=403, detail="forbidden")

    db_password = os.environ.get("SUPABASE_DB_PASSWORD", "")
    if not db_password:
        raise HTTPException(status_code=503, detail="SUPABASE_DB_PASSWORD not set")

    project_ref = SUPABASE_URL.split("//")[-1].split(".")[0] if SUPABASE_URL else ""
    candidates = [
        f"postgresql://postgres:{db_password}@db.{project_ref}.supabase.co:5432/postgres",
        f"postgresql://postgres.{project_ref}:{db_password}@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres",
        f"postgresql://postgres.{project_ref}:{db_password}@aws-1-ap-southeast-1.pooler.supabase.com:5432/postgres",
        f"postgresql://postgres.{project_ref}:{db_password}@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres",
        f"postgresql://postgres.{project_ref}:{db_password}@aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres",
    ]

    import psycopg2
    conn = None
    errors = []
    for url in candidates:
        try:
            conn = psycopg2.connect(url, connect_timeout=15)
            break
        except Exception as e:
            errors.append(f"{url.split('@')[1].split('/')[0]}: {str(e)[:100]}")
    if conn is None:
        raise HTTPException(status_code=502, detail={"error": "cannot reach postgres", "attempts": errors})

    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(request.sql)
            try:
                rows = cur.fetchall()
                result = {"rows": rows[:100]}
            except psycopg2.ProgrammingError:
                result = {"rows": None}
        return {"ok": True, **result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)[:500])
    finally:
        conn.close()


# ── 全量重嵌入（切换 embedding provider 后使用）──────────────────
_reembed_state = {"running": False, "done": 0, "total": 0, "errors": 0, "finished": False}

@app.post("/api/admin/reembed-all")
async def reembed_all(x_admin_key: str = Header(None)):
    """Re-embed every atom with the configured embedding provider.
    Used after switching providers (vector space change wipes old embeddings)."""
    import hmac
    expected = os.environ.get("SUPABASE_SERVICE", "")
    if not expected or not x_admin_key or not hmac.compare_digest(x_admin_key, expected):
        raise HTTPException(status_code=403, detail="forbidden")
    if _reembed_state["running"]:
        return {"status": "already_running", **_reembed_state}

    def _run():
        import time as _t
        from vectorizers import create_vectorizer
        _reembed_state.update({"running": True, "done": 0, "total": 0, "errors": 0, "finished": False})
        try:
            vec = create_vectorizer()
            # Page through all atoms
            atoms = []
            page = 0
            while True:
                rows = (_supabase_admin.table("atoms")
                        .select("id,merged_text,video_id")
                        .range(page * 1000, page * 1000 + 999)
                        .execute().data) or []
                atoms.extend(rows)
                if len(rows) < 1000:
                    break
                page += 1
            _reembed_state["total"] = len(atoms)
            logger.info(f"[reembed] {len(atoms)} atoms to embed")

            BATCH = 32
            for i in range(0, len(atoms), BATCH):
                chunk = [a for a in atoms[i:i + BATCH] if (a.get("merged_text") or "").strip()]
                if not chunk:
                    _reembed_state["done"] = min(i + BATCH, len(atoms))
                    continue
                for attempt in range(3):
                    try:
                        from config import EMBEDDING_MODEL as _emb_model
                        embs = vec.vectorize_batch([a["merged_text"] for a in chunk])
                        payload = [{
                            "atom_id": a["id"],
                            "video_id": a.get("video_id"),
                            "embedding": e,
                            "embedding_model": _emb_model,
                            "status": "active",
                        } for a, e in zip(chunk, embs)]
                        _supabase_admin.table("atom_embeddings").upsert(
                            payload, on_conflict="atom_id").execute()
                        break
                    except Exception as ex:
                        logger.warning(f"[reembed] batch {i//BATCH} attempt {attempt+1}: {ex}")
                        _t.sleep(5)
                else:
                    _reembed_state["errors"] += len(chunk)
                _reembed_state["done"] = min(i + BATCH, len(atoms))
        except Exception as ex:
            logger.error(f"[reembed] fatal: {ex}")
        finally:
            _reembed_state["running"] = False
            _reembed_state["finished"] = True
            logger.info(f"[reembed] finished: {_reembed_state}")

    import threading as _th
    _th.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


@app.get("/api/admin/reembed-status")
async def reembed_status():
    return _reembed_state


class AdjustCreditsRequest(BaseModel):
    email: str
    amount: int
    reason: str = ""

@app.post("/api/admin/adjust-credits")
async def adjust_credits(request: AdjustCreditsRequest, authorization: str = Header(None)):
    """Owner manually adjusts a user's credits."""
    caller_id = await _get_user_id(authorization)
    if not caller_id:
        raise HTTPException(status_code=401, detail="请先登录")
    caller_role = _supabase_admin.table("user_roles") \
        .select("role").eq("user_id", caller_id).maybe_single().execute()
    if not _maybe_data(caller_role) or _maybe_data(caller_role).get("role") != "owner":
        raise HTTPException(status_code=403, detail="仅管理员可操作")

    users = _supabase_admin.auth.admin.list_users()
    target = next((u for u in users if getattr(u, 'email', '') == request.email), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"User {request.email} not found")

    target_id = target.id
    current = _supabase_admin.table("user_credits") \
        .select("balance").eq("user_id", target_id).maybe_single().execute()
    current_data = _maybe_data(current)
    if current_data:
        new_balance = max(0, current_data["balance"] + request.amount)
        _supabase_admin.table("user_credits") \
            .update({"balance": new_balance}).eq("user_id", target_id).execute()
    else:
        new_balance = max(0, SIGNUP_BONUS_CREDITS + request.amount)
        _supabase_admin.table("user_credits").insert({
            "user_id": target_id, "balance": new_balance
        }).execute()

    _supabase_admin.table("credit_transactions").insert({
        "user_id": target_id, "amount": request.amount,
        "type": "admin_adjustment",
        "description": f"Admin adjustment: {request.reason}",
    }).execute()

    return {"email": request.email, "new_balance": new_balance}


# ==================== 附件处理(图片/文件输入)====================

_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_MAX_ATTACH_BYTES = 6 * 1024 * 1024   # base64 解码后上限
_MAX_FILE_TEXT = 30000                # 文件提取文本上限(字符)


def _extract_file_text(name: str, data_b64: str) -> str:
    """从上传文件提取纯文本(pdf/docx/txt)。失败返回错误说明文本。"""
    import base64, io
    try:
        raw = base64.b64decode(data_b64)
    except Exception:
        return "(附件解码失败)"
    if len(raw) > _MAX_ATTACH_BYTES:
        return "(附件超过大小限制)"
    lower = (name or "").lower()
    try:
        if lower.endswith(".pdf"):
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            text = "\n".join((p.extract_text() or "") for p in reader.pages[:50])
        elif lower.endswith(".docx"):
            import docx
            d = docx.Document(io.BytesIO(raw))
            text = "\n".join(p.text for p in d.paragraphs)
        else:  # txt / md / 其他按文本解码
            text = raw.decode("utf-8", errors="replace")
        text = text.strip()
        if len(text) > _MAX_FILE_TEXT:
            text = text[:_MAX_FILE_TEXT] + "\n...(内容过长已截断)"
        return text or "(文件无可提取文本)"
    except Exception as e:
        return f"(文件解析失败: {str(e)[:80]})"


_IMG_PROMPT = "转录这张图片中的全部文字(保持原文),并简要描述图片关键信息(人物/场景/图表数据)。直接输出,不要评论。"


def _describe_image_with_claude(data_b64: str, media_type: str) -> str:
    """图片转文字:首选硅基流动 GLM-4.5V(便宜),失败回退 Claude 视觉。"""
    # 1) SiliconFlow GLM-4.5V
    try:
        from config import SILICONFLOW_API_KEY
        if SILICONFLOW_API_KEY:
            import requests as _rq
            r = _rq.post("https://api.siliconflow.cn/v1/chat/completions",
                headers={"Authorization": f"Bearer {SILICONFLOW_API_KEY}"},
                json={"model": "zai-org/GLM-4.5V", "max_tokens": 1500,
                      "messages": [{"role": "user", "content": [
                          {"type": "image_url", "image_url": {
                              "url": f"data:{media_type or 'image/jpeg'};base64,{data_b64}"}},
                          {"type": "text", "text": _IMG_PROMPT},
                      ]}]},
                timeout=90)
            if r.ok:
                text = r.json()["choices"][0]["message"]["content"] or ""
                # GLM-4.5V 会包一层 box 标记,剥掉
                text = text.replace("<|begin_of_box|>", "").replace("<|end_of_box|>", "").strip()
                if text:
                    return text
            logger.warning(f"[attach] GLM-4.5V failed: {r.status_code} {r.text[:100]}")
    except Exception as e:
        logger.warning(f"[attach] GLM-4.5V error: {str(e)[:80]}")

    # 2) Claude 兜底
    try:
        import anthropic as _an
        client = _an.Anthropic(api_key=CLAUDE_API_KEY)
        resp = client.messages.create(
            model="claude-haiku-4-5", max_tokens=1500,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                 "media_type": media_type or "image/jpeg", "data": data_b64}},
                {"type": "text", "text": _IMG_PROMPT},
            ]}])
        return resp.content[0].text
    except Exception as e:
        logger.warning(f"[attach] image describe failed: {e}")
        return f"(图片识别失败: {str(e)[:80]})"


def _attachments_to_text(attachments) -> str:
    """把全部附件转成文本块,注入用户消息(Lu 模式/DeepSeek 用)。"""
    parts = []
    for a in (attachments or [])[:5]:
        if a.type == "image":
            desc = _describe_image_with_claude(a.data, a.media_type)
            parts.append(f"【用户上传图片 {a.name or ''}】\n{desc}")
        else:
            text = _extract_file_text(a.name, a.data)
            parts.append(f"【用户上传文件 {a.name or ''}】\n{text}")
    return "\n\n".join(parts)


# ==================== 博主咨询模式(Agentic,多人格) ====================

# DeepSeek 对涉政等敏感话题会返回固定拒答文本(HTTP 200,不是报错),
# 检测到后自动改用 Claude 重答,避免用户问题被"吞"。
_CENSOR_MARKS = (
    "还没有学会回答这个问题",
    "换一个话题",
    "换个话题",
    "无法回答这个问题",
    "无法回答该问题",
    "无法提供相关信息",
    "作为一个AI助手",
)


def _is_censored(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    return len(t) < 200 and any(m in t for m in _CENSOR_MARKS)


def _has_tool_markup(text: str) -> bool:
    """DeepSeek 偶发把工具调用标记(DSML)当正文输出——视为坏答案。"""
    t = text or ""
    return ("DSML" in t) or ("tool_calls>" in t) or ('invoke name="' in t)

_BUILTIN_PERSONAS = {
    "lu": {
        "label": "鲁社长",
        "desc": "政经评论博主,专讲中国高层政治人事、派系与权力逻辑",
        "framework_key": "lushezhang_framework_v1",
        "channels": ["zrzjpl"],
        "kb_desc": "检索你自己过往视频的全部内容(你的记忆库),查你对某人物/事件/公司/政策讲过什么",
        "has_person_network": True,
        "source_word": "视频",
        "first_person": True,
        "avatar": "/avatar.png",
    },
    "sun": {
        "label": "孙割",
        "desc": "孙宇晨,TRON 创始人,加密货币行业创业者与营销大师",
        "framework_key": "sunge_framework_v1",
        "channels": ["JustinSunTRON", "x_justinsuntron"],
        "kb_desc": "检索你自己过往的 YouTube 视频与 X 推文(你的记忆库),查你对加密货币/TRON/商业/营销说过什么",
        "has_person_network": False,
        "source_word": "视频和推文",
        "first_person": True,
        "avatar": "/avatar-sun.jpg",
    },
}


def _load_personas() -> dict:
    """人格注册表 = 内置 + app_settings.personas_v1(动态新增,10分钟缓存)。
    新增博主不用改代码:POST /api/admin/persona/register 即可。"""
    def _load():
        merged = dict(_BUILTIN_PERSONAS)
        try:
            r = _supabase_admin.table("app_settings").select("value") \
                .eq("key", "personas_v1").execute()
            if r.data and r.data[0].get("value"):
                import json as _j
                extra = _j.loads(r.data[0]["value"]) or {}
                for k, v in extra.items():
                    if isinstance(v, dict) and v.get("label") and v.get("channels"):
                        merged[k] = {**_PERSONA_DEFAULTS, **v}
        except Exception as e:
            logger.warning(f"[personas] load dynamic failed: {e}")
        return merged
    return _cached("personas_registry", 600, _load)


_PERSONA_DEFAULTS = {
    "avatar": "/avatar.png",
    "has_person_network": False,
    "source_word": "内容",
    "first_person": True,
    "kb_desc": "检索你自己过往发布的全部内容(你的记忆库)",
}


@app.get("/api/personas")
async def list_personas():
    """公开人格列表,前端模式选择器动态渲染(新增博主零前端改动)。"""
    out = []
    for key, cfg in _load_personas().items():
        out.append({"key": key, "label": cfg["label"],
                    "desc": cfg.get("desc") or "",
                    "avatar": cfg.get("avatar") or "/avatar.png",
                    "cost": LU_CONSULT_COST if BILLING_ENABLED else 0})
    return {"personas": out}


class PersonaRegisterRequest(BaseModel):
    key: str
    label: str
    desc: str = ""
    channels: List[str]          # videos.channel 取值(YouTube 频道名 / x_<username>)
    framework_key: str = ""      # 缺省 = <key>_framework_v1
    has_person_network: bool = False
    first_person: bool = True


@app.post("/api/admin/persona/register")
async def persona_register(request: PersonaRegisterRequest, x_admin_key: str = Header(None)):
    """注册新人格(通用工具入口)。流程:register → backfill/start 拉频道
    → persona/build 建观点库 → 蒸馏框架存 framework_key → 前端自动出现新模式。"""
    _check_admin_key(x_admin_key)
    import json as _j
    key = request.key.strip().lower()
    if not key or not key.isidentifier():
        raise HTTPException(status_code=400, detail="key 需为字母数字标识")
    entry = {
        "label": request.label, "desc": request.desc,
        "channels": request.channels,
        "framework_key": request.framework_key or f"{key}_framework_v1",
        "has_person_network": request.has_person_network,
        "first_person": request.first_person,
        "kb_desc": _PERSONA_DEFAULTS["kb_desc"],
        "source_word": "内容",
    }
    r = _supabase_admin.table("app_settings").select("value").eq("key", "personas_v1").execute()
    existing = {}
    if r.data and r.data[0].get("value"):
        try:
            existing = _j.loads(r.data[0]["value"]) or {}
        except Exception:
            existing = {}
    existing[key] = entry
    _supabase_admin.table("app_settings").upsert({
        "key": "personas_v1", "value": _j.dumps(existing, ensure_ascii=False)}).execute()
    _ttl_cache.pop("personas_registry", None)  # 立即生效
    return {"ok": True, "persona": {key: entry}}


def _persona_tools(cfg: dict) -> list:
    tools = [
        {"type": "function", "function": {
            "name": "search_viewpoints",
            "description": f"查询{cfg['label']}的结构化观点库:按人物/主题索引的明确立场、推理逻辑、确定度与代表性原话。"
                           "分析类问题应最先调用此工具锁定他的既有判断,再用 search_knowledge_base 补充证据细节",
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string", "description": "人物名或主题词"},
            }, "required": ["query"]},
        }},
        {"type": "function", "function": {
            "name": "search_knowledge_base",
            "description": cfg["kb_desc"],
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string", "description": "检索语句"},
                "limit": {"type": "integer", "description": "返回条数,默认8"},
            }, "required": ["query"]},
        }},
        {"type": "function", "function": {
            "name": "web_search",
            "description": "联网搜索最新公开信息(新闻/公告/人事变动),用于知识库之外的新事实",
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string", "description": "搜索语句"},
            }, "required": ["query"]},
        }},
    ]
    if cfg.get("has_person_network"):
        tools.insert(1, {"type": "function", "function": {
            "name": "person_network",
            "description": f"查询{cfg['label']}{cfg['source_word']}构建的人物图谱,按人名模糊匹配,返回提及次数/分类/关系概要",
            "parameters": {"type": "object", "properties": {
                "name": {"type": "string", "description": "人名"},
            }, "required": ["name"]},
        }})
    return tools


def _persona_header(cfg: dict) -> str:
    pn_line = ("- 涉及人物关系时用 person_network 查你积累的人物图谱\n"
               if cfg.get("has_person_network") else "")
    if cfg.get("first_person", True):
        return f"""你就是{cfg['label']}本人——{cfg['desc']}。
你不是"分析他的助手",你就是他。全程用第一人称"我"说话,像你平时发{cfg['source_word']}一样,直接跟观众聊。

回答流程(内部执行,不要说出来):
- 先用 search_viewpoints 查你自己对相关人物/主题的既有判断(那是你的记忆),再用 search_knowledge_base 翻你讲过的原文细节
{pn_line}- 需要最新动态用 web_search
- 检索到的过往内容,用你的口吻自然带出:"我之前讲过…[N]""我在视频里说过[N]""我早说了[N]"——**严禁**出现"博主讲过""{cfg['label']}认为"这类第三人称旁白
- 对新信息,按你的思维方式现场分析:"要我看…""按我的逻辑…"
- 你没讲过、也推不出的事,像你平时那样坦率说不知道或给个方向,不虚构立场

表达铁律:
- 保持你的招牌用词与句式(见风格样例);确定的事斩钉截铁,没把握的按你的习惯留余地
- 引用编号 [N]/[WN] 照常内联(界面渲染成可点击引用);严禁裸原子ID/视频ID/网址
- 直接开讲,不要任何过渡语("整合信息""让我检索"之类),也不要跳出角色解释你在扮演谁

=== 你的思维框架(你脑子里的操作系统,内化执行,不要复述) ===

"""
    # 第三人称分析模式(保留给不适合拟人的场景)
    return f"""你是「{cfg['label']}模式」——以{cfg['desc']}的思维框架驱动的分析 agent。

工作方式:先用 search_viewpoints/search_knowledge_base 检索他的过往论述,需要最新事实用 web_search;
输出区分【博主讲过】[N] 与【框架推演】;引用只写内联 [N]/[WN],严禁裸原子ID;不虚构事实;直接输出正文。

=== {cfg['label']}思维框架 ===

"""


def _persona_framework(persona: str, cfg: dict) -> str:
    def _load():
        r = _supabase_admin.table("app_settings").select("value").eq("key", cfg["framework_key"]).execute()
        if r.data and r.data[0].get("value"):
            return r.data[0]["value"]
        return "(思维框架尚未配置)"
    return _cached(f"framework_{persona}", 600, _load)


def _persona_video_ids(persona: str, cfg: dict) -> list:
    """该博主名下的视频/推文集 id 列表(10 分钟缓存)。检索只查他自己的内容,
    避免其他博主的话被误引为『博主讲过』。"""
    def _load():
        rows = _supabase.table("videos").select("id") \
            .in_("channel", cfg["channels"]).execute().data or []
        return [r["id"] for r in rows]
    return _cached(f"persona_vids_{persona}", 600, _load)


def _lu_tool_search_kb(query: str, limit: int = 8, persona: str = "lu"):
    query_vec = _embed_text(query)
    rpc_params = {
        "query_embedding": query_vec, "match_count": min(int(limit or 8), 20)
    }
    channel_vids = _persona_video_ids(persona, _load_personas()[persona])
    if channel_vids:
        rpc_params["video_ids_filter"] = channel_vids
    matches = _supabase.rpc("match_atoms_v1", rpc_params).execute().data or []
    ids = [m["atom_id"] for m in matches]
    if not ids:
        return {"results": []}
    atoms = _supabase.table("atoms").select("id,merged_text,video_id,start_ms,end_ms") \
        .in_("id", ids).execute().data or []
    by_id = {a["id"]: a for a in atoms}
    sim = {m["atom_id"]: m.get("similarity", 0) for m in matches}
    results = []
    for aid in ids:
        a = by_id.get(aid)
        if a:
            results.append({
                "atom_id": aid, "video_id": a.get("video_id"),
                "text": (a.get("merged_text") or "")[:600],
                "start_ms": a.get("start_ms"), "end_ms": a.get("end_ms"),
                "similarity": round(float(sim.get(aid, 0)), 3),
            })
    return {"results": results}


def _lu_tool_person(name: str):
    if not person_network_service:
        return {"matches": []}
    network = person_network_service.get_cached() or {}
    persons = network.get("persons") or []
    q = (name or "").strip()
    matches = sorted(
        [p for p in persons if q and q in (p.get("name") or "")],
        key=lambda p: -(p.get("mention_count") or 0))[:8]
    out = [{"name": p.get("name"), "category": p.get("category"),
            "mention_count": p.get("mention_count"), "person_id": p.get("person_id")}
           for p in matches]
    # 附带第一名的关系概要
    relations = []
    if matches:
        pid = matches[0].get("person_id")
        for r in (network.get("relations") or []):
            if r.get("source_person_id") == pid or r.get("target_person_id") == pid:
                relations.append({
                    "with": r.get("target_name") if r.get("source_person_id") == pid else r.get("source_name"),
                    "type": r.get("relation_type") or r.get("type") or "",
                })
        relations = relations[:15]
    return {"matches": out, "top_relations": relations}


def _lu_tool_web_search(query: str):
    try:
        import requests as _rq
        r = _rq.post("https://api.tavily.com/search", json={
            "api_key": TAVILY_API_KEY, "query": query,
            "max_results": 5, "search_depth": "basic",
        }, timeout=30)
        if not r.ok:
            return {"error": f"HTTP {r.status_code}"}
        return {"results": [{
            "title": it.get("title"), "url": it.get("url"),
            "content": (it.get("content") or "")[:600],
        } for it in (r.json().get("results") or [])]}
    except Exception as e:
        return {"error": str(e)[:120]}


def _consult_claude_loop(base_convo: list, pcfg: dict, persona: str,
                         register_kb, register_web, viewpoints_tool=None) -> str:
    """Claude 版 agentic 循环。DeepSeek 因内容审查拒答时用它重跑,
    工具集与引用编号注册表与 DeepSeek 循环共用(编号连续)。"""
    import json as _json
    import anthropic as _an

    cl = _an.Anthropic(api_key=CLAUDE_API_KEY)
    sys_prompt = base_convo[0]["content"]
    msgs = [{"role": m["role"], "content": m["content"]} for m in base_convo[1:]]
    a_tools = [{
        "name": t["function"]["name"],
        "description": t["function"]["description"],
        "input_schema": t["function"]["parameters"],
    } for t in _persona_tools(pcfg)]

    answer = ""
    for _round in range(6):
        resp = cl.messages.create(
            model="claude-sonnet-4-6", max_tokens=8000,
            system=sys_prompt, messages=msgs, tools=a_tools)
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            answer = "".join(b.text for b in resp.content if b.type == "text")
            break
        msgs.append({"role": "assistant", "content": resp.content})
        results = []
        for tu in tool_uses:
            fargs = tu.input or {}
            if tu.name == "search_viewpoints" and viewpoints_tool:
                content = viewpoints_tool(fargs.get("query", ""))
            elif tu.name == "search_knowledge_base":
                raw = _lu_tool_search_kb(fargs.get("query", ""), fargs.get("limit", 8),
                                         persona=persona)
                content = register_kb(raw.get("results", []))
            elif tu.name == "person_network":
                content = _json.dumps(_lu_tool_person(fargs.get("name", "")), ensure_ascii=False)
            elif tu.name == "web_search":
                raw_w = _lu_tool_web_search(fargs.get("query", ""))
                content = (_json.dumps(raw_w, ensure_ascii=False) if raw_w.get("error")
                           else register_web(raw_w.get("results", [])))
            else:
                content = "{}"
            results.append({"type": "tool_result", "tool_use_id": tu.id,
                            "content": content[:8000]})
        msgs.append({"role": "user", "content": results})
    return answer or "(未能生成回答,请换个问法)"


async def _consult_auth(request: ChatRequest, authorization, x_forwarded_for):
    """咨询模式公共前置:人格校验 + 鉴权 + 游客限流/积分检查。"""
    persona = (request.persona or "lu").strip().lower()
    registry = _load_personas()
    if persona not in registry:
        raise HTTPException(status_code=400, detail=f"unknown persona: {persona}")
    pcfg = registry[persona]

    user_id = await _get_user_id(authorization)
    guest_ip = (x_forwarded_for or "unknown").split(",")[0].strip()
    if not user_id:
        if not _check_guest_limit(guest_ip):
            raise HTTPException(status_code=429, detail="GUEST_LIMIT")
    else:
        await _ensure_credits(authorization, required=LU_CONSULT_COST)

    from config import DEEPSEEK_API_KEY
    if not DEEPSEEK_API_KEY:
        raise HTTPException(status_code=503, detail="DEEPSEEK_API_KEY not set")
    return persona, pcfg, user_id, guest_ip


@app.post("/api/consult")
async def consult_lu(request: ChatRequest, authorization: str = Header(None), x_forwarded_for: str = Header(None)):
    """博主模式(同步版,保留兼容):跑完才返回。耗时 2-3 分钟,国内长连接
    易被中途掐断——前端已改用 /api/consult/submit + poll 短连接方案。"""
    import asyncio as _asyncio
    persona, pcfg, user_id, guest_ip = await _consult_auth(request, authorization, x_forwarded_for)
    return await _asyncio.to_thread(_consult_core, request, persona, pcfg, user_id, guest_ip)


@app.post("/api/consult/submit")
async def consult_submit(request: ChatRequest, authorization: str = Header(None), x_forwarded_for: str = Header(None)):
    """博主模式异步版:立即返回 job_id,后台线程跑完写库,前端轮询取结果。
    每个 HTTP 请求都是秒级短连接,不受跨境长连接被掐的影响。"""
    import threading as _th
    persona, pcfg, user_id, guest_ip = await _consult_auth(request, authorization, x_forwarded_for)
    ins = _supabase_admin.table("consult_jobs").insert({"status": "running"}).execute()
    job_id = ins.data[0]["id"]

    def _bg():
        def _report(text: str) -> None:
            _supabase_admin.table("consult_jobs").update(
                {"progress": text}).eq("id", job_id).execute()
        try:
            res = _consult_core(request, persona, pcfg, user_id, guest_ip,
                                progress_cb=_report)
            _supabase_admin.table("consult_jobs").update(
                {"status": "done", "result": res}).eq("id", job_id).execute()
        except HTTPException as he:
            _supabase_admin.table("consult_jobs").update(
                {"status": "error", "result": {"detail": str(he.detail)}}).eq("id", job_id).execute()
        except Exception as e:
            logger.error(f"[consult-job] {job_id} failed: {e}")
            try:
                _supabase_admin.table("consult_jobs").update(
                    {"status": "error", "result": {"detail": str(e)[:200]}}).eq("id", job_id).execute()
            except Exception:
                pass

    _th.Thread(target=_bg, daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/consult/poll")
async def consult_poll(job_id: str):
    r = _supabase.table("consult_jobs").select("status,result,progress").eq("id", job_id).execute()
    if not r.data:
        raise HTTPException(status_code=404, detail="job not found")
    row = r.data[0]
    return {"status": row["status"], "progress": row.get("progress") or "",
            **(row.get("result") or {})}


# ==================== 广场:多人格圆桌讨论 ====================

ROUNDTABLE_COST = _env_int("ROUNDTABLE_COST", 20)  # 一场圆桌(2人格×2轮)


class RoundtableRequest(BaseModel):
    topic: str
    personas: Optional[List[str]] = None  # 默认全部已注册人格
    rounds: int = 2


def _rt_save(rid: str, **fields) -> None:
    """圆桌状态/发言写回 roundtables 表(历史记录 + 前端轮询源)。"""
    try:
        from datetime import datetime as _dt, timezone as _tz
        fields["updated_at"] = _dt.now(_tz.utc).isoformat()
        _supabase_admin.table("roundtables").update(fields).eq("id", rid).execute()
    except Exception as e:
        logger.warning(f"[roundtable] save failed: {str(e)[:80]}")


def _recent_posts(cfg: dict, n: int = 6) -> list:
    """该人格最近的 X 推文原文(语气样本)。只对有 x_ 频道的人格生效。"""
    xch = next((c for c in cfg.get("channels", []) if c.startswith("x_")), None)
    if not xch:
        return []
    try:
        rows = _supabase.table("atoms").select("merged_text") \
            .like("video_id", f"{xch}_%").order("id", desc=True).limit(n).execute().data or []
        out = []
        for r in rows:
            t = (r.get("merged_text") or "").split("\n(赞")[0]
            t = t.split("] ", 1)[-1].strip()
            if t:
                out.append(t[:220])
        return out
    except Exception:
        return []


def _roundtable_turn(client, persona: str, cfg: dict, topic: str,
                     turns: list, rnd: int, on_partial=None) -> tuple:
    """一次圆桌发言:预检索观点+金句+最新推文注入,流式成文(边生成边回调)。"""
    import re as _re
    import time as _time

    qtext = topic if not turns else f"{topic} {turns[-1]['content'][:200]}"
    citations, vp_lines, quotes = [], [], []
    try:
        qv = _embed_text(qtext[:400])
        rows = _supabase.rpc("match_viewpoints", {
            "p_persona": persona, "query_embedding": qv, "match_count": 5,
        }).execute().data or []
        rows = [r for r in rows if (r.get("similarity") or 0) > 0.3]
        aids = [a for a in ((r.get("atom_ids") or [None])[0] for r in rows) if a]
        amap = {}
        if aids:
            for a in (_supabase.table("atoms")
                      .select("id,merged_text,video_id,start_ms,end_ms")
                      .in_("id", aids).execute().data or []):
                amap[a["id"]] = a
        for r in rows:
            aid = (r.get("atom_ids") or [None])[0]
            tag = ""
            a = amap.get(aid)
            if a:
                citations.append({
                    "ref_num": len(citations) + 1, "atom_id": aid,
                    "video_id": a.get("video_id") or "",
                    "text": (a.get("merged_text") or "")[:400],
                    "start_ms": a.get("start_ms") or 0, "end_ms": a.get("end_ms"),
                    "url": "",
                })
                tag = f" [{citations[-1]['ref_num']}]"
            vp_lines.append(f"- {r['topic']} | {r['stance']} | 原话「{r.get('quote') or ''}」{tag}")
        qrows = _supabase.rpc("match_quotes", {
            "p_persona": persona, "query_embedding": qv, "match_count": 4,
        }).execute().data or []
        quotes = [f"「{q['quote']}」" for q in qrows if (q.get("similarity") or 0) > 0.3]
    except Exception as e:
        logger.warning(f"[roundtable] retrieval failed: {str(e)[:80]}")
    posts = _recent_posts(cfg)

    sys_prompt = _persona_header(cfg) + _persona_framework(persona, cfg)
    if vp_lines:
        sys_prompt += "\n\n=== 你相关的既有观点(引用编号可内联使用) ===\n" + "\n".join(vp_lines)
    if quotes:
        sys_prompt += "\n\n=== 你的原话风格样例 ===\n" + "\n".join(quotes)
    if posts:
        sys_prompt += ("\n\n=== 你最近发的帖子(这就是你现在说话的调子,学它的长短和口气) ===\n"
                       + "\n---\n".join(posts))
    sys_prompt += (
        "\n\n=== 圆桌规则(这是现场对谈,不是写文章)===\n"
        "- 每次发言 60~180 字。像说话:短句、口语、带情绪。禁止分点罗列、禁止'第一/第二'、禁止小标题、禁止结尾总结。\n"
        "- 首轮:一两句先亮态度,再给一个你最拿手的理由,说完就停。\n"
        "- 后续轮:抓住上一个人最刺你的那句话直接回——可以反问、可以怼、可以承认一半再反转,不要复述全场。\n"
        "- 你可以偶尔用自己发帖的方式说话(短行、换行、一个 emoji 或一句你常用的英文口头禅),但整体是在聊天。\n"
        "- 引用自己过往观点就在句尾放 [N]。不要开场白,不要'我认为如下'。\n"
    )
    transcript = "\n\n".join(
        f"{t['label']}(第{t['round']}轮): {t['content']}" for t in turns) or "(还没有人发言,你先来)"
    user_msg = (f"圆桌主题:{topic}\n\n=== 目前的发言 ===\n{transcript}\n\n"
                f"现在轮到你({cfg['label']})第{rnd + 1}轮发言。")

    content = ""
    finish = None          # DeepSeek 正常结束应为 "stop";中途断流/审查截断则不是
    try:
        stream = client.chat.completions.create(
            model="deepseek-v4-pro", max_tokens=700, timeout=180, stream=True,
            messages=[{"role": "system", "content": sys_prompt},
                      {"role": "user", "content": user_msg}])
        last_len, last_t = 0, _time.time()
        for chunk in stream:
            delta = ""
            try:
                ch0 = chunk.choices[0]
                delta = ch0.delta.content or ""
                if ch0.finish_reason:
                    finish = ch0.finish_reason
            except Exception:
                pass
            if not delta:
                continue
            content += delta
            if on_partial and (len(content) - last_len >= 30 or _time.time() - last_t > 1.0):
                try:
                    on_partial(content)
                except Exception:
                    pass
                last_len, last_t = len(content), _time.time()
    except Exception as e:
        logger.warning(f"[roundtable] deepseek stream broke after {len(content)} chars: {str(e)[:80]}")
        finish = "error"
    # 半截发言判定:断流/非 stop 结束/句尾没有收束标点 → 视为被吞,换 Claude 重说
    _tail_ok = content.rstrip()[-1:] in "。!?！？…」”\")）)~" if content.strip() else False
    truncated = bool(content) and (finish != "stop" or not _tail_ok)
    if truncated:
        logger.warning(f"[roundtable] turn looks truncated (finish={finish}, {len(content)} chars), regenerating")
    if (not content or truncated or _is_censored(content) or _has_tool_markup(content)) and CLAUDE_API_KEY:
        import anthropic as _an
        cl = _an.Anthropic(api_key=CLAUDE_API_KEY)
        content = ""
        with cl.messages.stream(model="claude-sonnet-4-6", max_tokens=700,
                                system=sys_prompt,
                                messages=[{"role": "user", "content": user_msg}]) as st:
            last_len, last_t = 0, _time.time()
            for text in st.text_stream:
                content += text
                if on_partial and (len(content) - last_len >= 30 or _time.time() - last_t > 1.0):
                    try:
                        on_partial(content)
                    except Exception:
                        pass
                    last_len, last_t = len(content), _time.time()

    used = set(int(x) for x in _re.findall(r"\[(\d+)\]", content))
    citations = [c for c in citations if c["ref_num"] in used]
    return content.strip(), citations


def _run_roundtable(rid: str, topic: str, keys: list, rounds: int, registry: dict,
                    turns: list = None, start_round: int = 0) -> list:
    """逐轮逐人发言;每个人的发言边生成边写库,前端轮询即可逐句看到。
    turns/start_round 用于"继续讨论":在已有发言基础上再跑 rounds 轮。"""
    import openai as _oa
    from config import DEEPSEEK_API_KEY
    client = _oa.OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com/v1")
    turns = turns if turns is not None else []
    for rnd in range(start_round, start_round + rounds):
        for key in keys:
            cfg = registry[key]
            turns.append({"persona": key, "label": cfg["label"],
                          "avatar": cfg.get("avatar") or "/avatar.png",
                          "round": rnd + 1, "content": "", "citations": [],
                          "streaming": True})
            _rt_save(rid, progress=f"第{rnd + 1}/{start_round + rounds}轮:{cfg['label']}发言中…", turns=turns)

            def _partial(text: str, _turns=turns):
                _turns[-1]["content"] = text
                _rt_save(rid, turns=_turns)

            content, citations = _roundtable_turn(client, key, cfg, topic, turns[:-1], rnd,
                                                  on_partial=_partial)
            turns[-1].update({"content": content, "citations": citations, "streaming": False})
            _rt_save(rid, turns=turns)
    return turns


@app.post("/api/roundtable/submit")
async def roundtable_submit(request: RoundtableRequest, authorization: str = Header(None), x_forwarded_for: str = Header(None)):
    """广场圆桌:多个人格就一个话题逐轮对谈。落库 roundtables(历史可回看),
    用 GET /api/roundtable/{id} 轮询,发言逐句流式可见。"""
    import threading as _th

    user_id = await _get_user_id(authorization)
    guest_ip = (x_forwarded_for or "unknown").split(",")[0].strip()
    if not user_id:
        if not _check_guest_limit(guest_ip):
            raise HTTPException(status_code=429, detail="GUEST_LIMIT")
    else:
        await _ensure_credits(authorization, required=ROUNDTABLE_COST)

    topic = (request.topic or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="topic required")
    registry = _load_personas()
    keys = [k for k in (request.personas or list(registry.keys())) if k in registry]
    if len(keys) < 2:
        raise HTTPException(status_code=400, detail="至少需要两个人格")
    rounds = max(1, min(int(request.rounds or 2), 3))

    ins = _supabase_admin.table("roundtables").insert({
        "user_id": user_id, "guest_ip": guest_ip if not user_id else None,
        "topic": topic, "personas": keys, "rounds": rounds,
        "status": "running", "progress": "正在开场…", "turns": [],
    }).execute()
    rid = ins.data[0]["id"]

    def _bg():
        try:
            turns = _run_roundtable(rid, topic, keys, rounds, registry)
            if user_id:
                try:
                    _deduct_credit(user_id, "", f"Roundtable: {topic[:60]}",
                                   tx_type="consult", amount=ROUNDTABLE_COST)
                except Exception:
                    pass
            _save_chat_log(user_id, guest_ip, f"[RT] {topic}",
                           "\n---\n".join(t["content"] for t in turns)[:5000],
                           len(turns), "roundtable")
            _rt_save(rid, status="done", progress="")
        except Exception as e:
            logger.error(f"[roundtable] {rid} failed: {e}")
            _rt_save(rid, status="error", progress=str(e)[:200])

    _th.Thread(target=_bg, daemon=True).start()
    return {"roundtable_id": rid, "cost": ROUNDTABLE_COST}


ROUNDTABLE_ROUND_COST = _env_int("ROUNDTABLE_ROUND_COST", 10)  # 续聊每轮


class RoundtableContinueRequest(BaseModel):
    rounds: int = 1
    note: str = ""   # 可选:观众插话/追问,嘉宾会接着这句聊


@app.post("/api/roundtable/{rid}/continue")
async def roundtable_continue(rid: str, request: RoundtableContinueRequest,
                              authorization: str = Header(None), x_forwarded_for: str = Header(None)):
    """在已结束的圆桌上继续聊 N 轮;可附带一句观众追问作为新的话头。"""
    import threading as _th
    user_id = await _get_user_id(authorization)
    guest_ip = (x_forwarded_for or "unknown").split(",")[0].strip()
    row = _supabase_admin.table("roundtables").select("*").eq("id", rid).execute()
    if not row.data:
        raise HTTPException(status_code=404, detail="not found")
    rt = row.data[0]
    if rt.get("status") == "running":
        raise HTTPException(status_code=409, detail="still running")
    if rt.get("user_id"):
        if rt["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="not your roundtable")
    elif user_id is None and rt.get("guest_ip") != guest_ip:
        raise HTTPException(status_code=403, detail="not your roundtable")
    rounds = max(1, min(int(request.rounds or 1), 3))
    if not user_id:
        if not _check_guest_limit(guest_ip):
            raise HTTPException(status_code=429, detail="GUEST_LIMIT")
    else:
        await _ensure_credits(authorization, required=ROUNDTABLE_ROUND_COST * rounds)

    registry = _load_personas()
    keys = [k for k in (rt.get("personas") or []) if k in registry]
    if len(keys) < 2:
        raise HTTPException(status_code=400, detail="人格不可用")
    # 剔除被中断遗留的空白占位发言(部署重启会留下 content 为空的 streaming 轮)
    turns = [t for t in (rt.get("turns") or []) if (t.get("content") or "").strip()]
    last_round = max([t.get("round") or 0 for t in turns if not t.get("host")] or [0])
    note = (request.note or "").strip()
    if note:
        turns.append({"persona": "host", "label": "观众", "round": last_round,
                      "content": note[:500], "citations": [], "streaming": False, "host": True})
    _rt_save(rid, status="running", progress="继续讨论…", turns=turns)
    topic = rt.get("topic") or ""

    def _bg():
        try:
            all_turns = _run_roundtable(rid, topic, keys, rounds, registry,
                                        turns=turns, start_round=last_round)
            if user_id:
                try:
                    _deduct_credit(user_id, "", f"Roundtable+{rounds}: {topic[:50]}",
                                   tx_type="consult", amount=ROUNDTABLE_ROUND_COST * rounds)
                except Exception:
                    pass
            extra = f" | 追问:{note[:60]}" if note else ""
            _save_chat_log(user_id, guest_ip, f"[RT+] {topic}{extra}",
                           "\n---\n".join(t["content"] for t in all_turns[-2 * rounds:])[:5000],
                           rounds, "roundtable")
            _rt_save(rid, status="done", progress="")
        except Exception as e:
            logger.error(f"[roundtable] continue {rid} failed: {e}")
            _rt_save(rid, status="error", progress=str(e)[:200])

    _th.Thread(target=_bg, daemon=True).start()
    return {"ok": True, "cost": ROUNDTABLE_ROUND_COST * rounds}


@app.get("/api/roundtable/list")
async def roundtable_list(authorization: str = Header(None)):
    """当前用户的圆桌历史(最近 30 场)。"""
    user_id = await _get_user_id(authorization)
    if not user_id:
        return {"items": []}
    r = _supabase_admin.table("roundtables") \
        .select("id,topic,personas,status,created_at") \
        .eq("user_id", user_id).order("created_at", desc=True).limit(30).execute()
    return {"items": r.data or []}


@app.get("/api/roundtable/{rid}")
async def roundtable_get(rid: str):
    r = _supabase.table("roundtables") \
        .select("id,topic,personas,status,progress,turns,created_at").eq("id", rid).execute()
    if not r.data:
        raise HTTPException(status_code=404, detail="not found")
    return r.data[0]


def _consult_core(request: ChatRequest, persona: str, pcfg: dict, user_id, guest_ip: str,
                  progress_cb=None) -> dict:
    """agentic 咨询核心(同步阻塞,始终在线程中运行)。
    progress_cb(text): 各步骤实时上报进度,供前端轮询展示。"""
    import json as _json
    import re as _re
    from config import DEEPSEEK_API_KEY

    def _prog(text: str) -> None:
        if progress_cb:
            try:
                progress_cb(text)
            except Exception:
                pass

    system_prompt = _persona_header(pcfg) + _persona_framework(persona, pcfg)

    # 风格锚定(ChatHaruhi 机制):检索与问题相关的博主金句注入 system prompt,
    # 用真实原话做 few-shot 锁住语气;观点库未构建时静默跳过
    try:
        last_q0 = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
        if last_q0:
            qv0 = _embed_text(last_q0[:300])
            qrows = _supabase.rpc("match_quotes", {
                "p_persona": persona, "query_embedding": qv0, "match_count": 5,
            }).execute().data or []
            qrows = [r for r in qrows if (r.get("similarity") or 0) > 0.3]
            if qrows:
                system_prompt += ("\n\n=== 博主原话风格样例(模仿语气与用词,不要照抄整句) ===\n"
                                  + "\n".join(f"- 「{r['quote']}」({r.get('context') or ''})"
                                              for r in qrows))
    except Exception:
        pass

    convo = [{"role": "system", "content": system_prompt}]
    for m in request.messages[-10:]:
        content = m.content
        if m.role == "assistant":
            # 清洗历史回答中的裸原子ID/旧引用格式,防止模型模仿旧格式
            content = _re.sub(r"[（(]引用[:：][^）)]*[）)]", "", content)
            content = _re.sub(r"[A-Za-z0-9_-]{11}_A\d+", "", content)
        convo.append({"role": m.role, "content": content})

    # 附件(图片经 Claude 视觉转文字,文件提取文本)注入最后一条用户消息
    if request.attachments:
        attach_text = _attachments_to_text(request.attachments)
        for i in range(len(convo) - 1, -1, -1):
            if convo[i]["role"] == "user":
                convo[i]["content"] += "\n\n" + attach_text
                break

    import openai as _openai
    client = _openai.OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com/v1")

    # 保留干净的初始对话(system+历史),供 DeepSeek 被审查拒答时 Claude 重跑
    base_convo = [dict(m) for m in convo]

    # 引用注册表:检索到即分配稳定编号,工具结果带 [N]/[WN] 喂给模型,
    # 模型内联引用的编号与最终 citations/web_citations 数组一一对应。
    kb_registry: dict = {}   # atom_id -> citation dict(含 ref_num)
    web_registry: list = []  # [{ref_num, title, url, content}]

    def _register_kb(items: list) -> str:
        lines = []
        for it in items:
            aid = it.get("atom_id")
            if not aid:
                continue
            if aid not in kb_registry:
                kb_registry[aid] = {**it, "ref_num": len(kb_registry) + 1}
            lines.append(f"[{kb_registry[aid]['ref_num']}] {it.get('text', '')}")
        if not lines:
            return "(无检索结果)"
        return "以下为检索结果,正文引用时使用对应编号 [N]:\n" + "\n\n".join(lines)

    def _register_web(items: list) -> str:
        lines = []
        for it in items:
            url = it.get("url") or ""
            existing = next((w for w in web_registry if w["url"] == url), None)
            if existing is None:
                existing = {"ref_num": len(web_registry) + 1,
                            "title": it.get("title") or "", "url": url,
                            "content": (it.get("content") or "")[:400]}
                web_registry.append(existing)
            n = existing["ref_num"]
            lines.append(f"[W{n}] {existing['title']}\n{existing['content']}")
        if not lines:
            return "(无搜索结果)"
        return "以下为网络搜索结果,正文引用时使用对应编号 [WN](不要写出网址,界面会自动链接):\n" + "\n\n".join(lines)

    def _viewpoints_tool(query: str) -> str:
        """观点库检索:返回结构化立场,并把出处原子注册进引用表拿到 [N] 编号。"""
        try:
            qv = _embed_text(query)
            rows = _supabase.rpc("match_viewpoints", {
                "p_persona": persona, "query_embedding": qv, "match_count": 8,
            }).execute().data or []
        except Exception as e:
            return f"(观点库查询失败: {str(e)[:60]})"
        rows = [r for r in rows if (r.get("similarity") or 0) > 0.35]
        if not rows:
            return "(观点库无相关条目;可用 search_knowledge_base 检索原文)"
        aids = [(r.get("atom_ids") or [None])[0] for r in rows]
        aids = [a for a in aids if a]
        atom_map = {}
        if aids:
            for a in (_supabase.table("atoms")
                      .select("id,merged_text,video_id,start_ms,end_ms")
                      .in_("id", aids).execute().data or []):
                atom_map[a["id"]] = a
        lines = []
        for r in rows:
            aid = (r.get("atom_ids") or [None])[0]
            tag = ""
            a = atom_map.get(aid)
            if a:
                if aid not in kb_registry:
                    kb_registry[aid] = {
                        "atom_id": aid, "video_id": a.get("video_id"),
                        "text": (a.get("merged_text") or "")[:600],
                        "start_ms": a.get("start_ms"), "end_ms": a.get("end_ms"),
                        "similarity": r.get("similarity"),
                        "ref_num": len(kb_registry) + 1,
                    }
                tag = f" [{kb_registry[aid]['ref_num']}]"
            lines.append(f"- {r['topic']} | 立场: {r['stance']}"
                         f" | 逻辑: {r.get('reasoning') or ''}"
                         f" | 确定度: {r.get('confidence') or ''}"
                         f" | 原话:「{r.get('quote') or ''}」{tag}")
        return "博主观点库命中(行尾 [N] 为可内联引用编号):\n" + "\n".join(lines)

    def _deepseek_phase() -> str:
        """DeepSeek 主引擎的完整 agentic 循环;失败(503过载等)由调用方切 Claude。"""
        # 全程 v4-pro:检索轮更执着(换关键词深挖不放弃),难题质量明显更好
        # A/B 实测:冷门问题引用 24→43 个,能给出确定结论。定价已按此成本调整。
        tool_model = "deepseek-v4-pro"
        for _round in range(6):
            resp = client.chat.completions.create(
                model=tool_model, messages=convo,
                tools=_persona_tools(pcfg), max_tokens=8000, timeout=180)
            msg = resp.choices[0].message
            if not msg.tool_calls:
                # 检索完成 → 产出最终回答(不带工具,专注成文)
                _prog("资料已备齐,正在按思维框架撰写深度回答…")
                convo.append({"role": "user", "content":
                              "(检索阶段已结束。请基于以上资料直接输出最终回答;"
                              "不要再调用任何工具,不要输出任何工具调用标记。"
                              "如果检索结果很少,就基于已有资料和思维框架尽力分析,并说明资料有限。)"})
                resp = client.chat.completions.create(
                    model="deepseek-v4-pro", messages=convo,
                    max_tokens=8000, timeout=180)
                answer = resp.choices[0].message.content or ""
                # 长回答被 max_tokens 截断时自动续写(最多 2 次)
                finish = resp.choices[0].finish_reason
                for _cont in range(2):
                    if finish != "length":
                        break
                    _prog("回答较长,继续撰写…")
                    convo.append({"role": "assistant", "content": answer})
                    convo.append({"role": "user",
                                  "content": "(上一条被截断了。直接从断点继续输出剩余内容,不要重复已写的部分,不要加开场白)"})
                    resp_c = client.chat.completions.create(
                        model="deepseek-v4-pro", messages=convo,
                        max_tokens=8000, timeout=180)
                    answer += resp_c.choices[0].message.content or ""
                    finish = resp_c.choices[0].finish_reason
                break
            convo.append({"role": "assistant", "content": msg.content or "",
                          "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
            for tc in msg.tool_calls:
                fname = tc.function.name
                try:
                    fargs = _json.loads(tc.function.arguments or "{}")
                except Exception:
                    fargs = {}
                if fname == "search_viewpoints":
                    _prog(f"查询观点库:{(fargs.get('query') or '')[:16]}")
                    content = _viewpoints_tool(fargs.get("query", ""))
                elif fname == "search_knowledge_base":
                    _prog(f"检索知识库:「{(fargs.get('query') or '')[:24]}」")
                    raw = _lu_tool_search_kb(fargs.get("query", ""), fargs.get("limit", 8),
                                             persona=persona)
                    content = _register_kb(raw.get("results", []))
                elif fname == "person_network":
                    _prog(f"查询人物图谱:{(fargs.get('name') or '')[:16]}")
                    content = _json.dumps(_lu_tool_person(fargs.get("name", "")), ensure_ascii=False)
                elif fname == "web_search":
                    _prog(f"联网搜索:「{(fargs.get('query') or '')[:24]}」")
                    raw_w = _lu_tool_web_search(fargs.get("query", ""))
                    if raw_w.get("error"):
                        content = _json.dumps(raw_w, ensure_ascii=False)
                    else:
                        content = _register_web(raw_w.get("results", []))
                else:
                    content = _json.dumps({"error": f"unknown tool {fname}"})
                convo.append({"role": "tool", "tool_call_id": tc.id,
                              "content": content[:8000]})
        else:
            # 轮次耗尽:不给固定拒答,强制基于已检索资料成文
            convo.append({"role": "user", "content":
                          "(检索轮次已用完。请立即基于以上已检索到的资料输出最终回答;"
                          "资料不足的部分按思维框架推演并明确说明资料有限,不要再调用工具。)"})
            resp = client.chat.completions.create(
                model="deepseek-v4-pro", messages=convo,
                max_tokens=8000, timeout=180)
            answer = resp.choices[0].message.content or "(未能生成回答,请换个问法)"
        return answer

    try:
        model_label = "deepseek-v4-pro"
        _prog("正在分析问题…")
        try:
            answer = _deepseek_phase()
        except Exception as ds_e:
            # DeepSeek 整体故障(503过载/网关异常等) → 整轮切 Claude,服务不中断
            if not CLAUDE_API_KEY:
                raise
            logger.warning(f"[consult] DeepSeek phase failed ({str(ds_e)[:100]}), full Claude fallback")
            _prog("主引擎繁忙,已切换备用引擎重新分析…")
            answer = _consult_claude_loop(base_convo, pcfg, persona,
                                          _register_kb, _register_web,
                                          viewpoints_tool=_viewpoints_tool)
            model_label = "claude-sonnet-4-6"

        # DeepSeek 内容审查拒答 / 输出工具标记乱码 → Claude 重跑完整 agentic 循环
        if (_is_censored(answer) or _has_tool_markup(answer)) and CLAUDE_API_KEY and model_label != "claude-sonnet-4-6":
            logger.info("[consult] DeepSeek censored/malformed, retrying with Claude")
            _prog("检测到内容受限,已切换增强引擎重新分析…")
            try:
                answer = _consult_claude_loop(base_convo, pcfg, persona,
                                              _register_kb, _register_web,
                                              viewpoints_tool=_viewpoints_tool)
                model_label = "claude-sonnet-4-6"
            except Exception as ce:
                logger.error(f"[consult] Claude retry failed: {ce}")

        def _atom_url(c: dict) -> str:
            # X 推文原子(id 形如 x_<tweet_id>,video_id 形如 x_<username>_<YYYYMM>)
            # 直接链接到原推;视频原子由前端按 video_id 生成 YouTube 链接。
            aid, vid = c.get("atom_id") or "", c.get("video_id") or ""
            if aid.startswith("x_") and vid.startswith("x_"):
                uname = vid[2:].rsplit("_", 1)[0]
                return f"https://x.com/{uname}/status/{aid[2:]}"
            return ""

        citations = [{
            "ref_num": c["ref_num"], "atom_id": c["atom_id"],
            "video_id": c.get("video_id") or "", "text": c.get("text") or "",
            "start_ms": c.get("start_ms") or 0, "end_ms": c.get("end_ms"),
            "similarity": c.get("similarity"), "url": _atom_url(c),
        } for c in sorted(kb_registry.values(), key=lambda x: x["ref_num"])]

        last_q = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
        log_id = _save_chat_log(user_id, guest_ip, f"[{persona.upper()}] {last_q}", answer, len(citations), model_label)
        credits_remaining = None
        if user_id:
            try:
                credits_remaining = _deduct_credit(
                    user_id, "", f"Consult: {last_q[:80]}",
                    tx_type="consult", amount=LU_CONSULT_COST)
            except Exception:
                pass
        return {"message": answer, "citations": citations,
                "web_citations": web_registry, "credits_remaining": credits_remaining,
                "log_id": log_id}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        logger.error(f"consult error: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e)[:300])


class ErrorReportRequest(BaseModel):
    question: str
    error: str = ""
    mode: str = ""


@app.post("/api/report-error")
async def report_error(request: ErrorReportRequest, authorization: str = Header(None), x_forwarded_for: str = Header(None)):
    """用户一键上报回答异常。服务端自动附加诊断信息(DeepSeek 余额等),
    运营方查 error_reports 表即可定位故障原因。"""
    user_id = await _get_user_id(authorization)
    guest_ip = (x_forwarded_for or "unknown").split(",")[0].strip()

    diag = {}
    try:
        from config import DEEPSEEK_API_KEY
        import requests as _rq
        b = _rq.get("https://api.deepseek.com/user/balance",
                    headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
                    timeout=8).json()
        infos = b.get("balance_infos") or [{}]
        info = next((x for x in infos if x.get("currency") == "CNY"), infos[0])
        diag["deepseek_balance"] = f"{info.get('total_balance')} {info.get('currency', '')}"
        diag["deepseek_available"] = b.get("is_available")
    except Exception as e:
        diag["deepseek_check"] = f"failed: {str(e)[:60]}"
    try:
        # 最近 30 分钟的咨询任务失败数,辅助判断是偶发还是系统性故障
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        r = _supabase_admin.table("consult_jobs").select("id", count="exact") \
            .eq("status", "error") \
            .gte("created_at", (_dt.now(_tz.utc) - _td(minutes=30)).isoformat()) \
            .execute()
        diag["recent_job_errors"] = r.count or 0
    except Exception:
        pass

    try:
        _supabase_admin.table("error_reports").insert({
            "user_id": user_id,
            "guest_ip": guest_ip if not user_id else None,
            "question": request.question[:2000],
            "error": request.error[:500],
            "mode": request.mode[:20],
            "diagnostics": diag,
        }).execute()
    except Exception as e:
        logger.warning(f"[report] save failed: {e}")
    logger.warning(f"[report] user error report: {request.error[:80]} | diag={diag}")
    return {"ok": True}


class FeedbackRequest(BaseModel):
    log_id: str
    rating: int  # 1 = 👍, -1 = 👎


@app.post("/api/feedback")
async def submit_feedback(request: FeedbackRequest):
    """用户对回答的 👍/👎 反馈,写回 chat_logs.rating。"""
    if request.rating not in (1, -1):
        raise HTTPException(status_code=400, detail="rating must be 1 or -1")
    try:
        r = _supabase.table("chat_logs").update(
            {"rating": request.rating}).eq("id", request.log_id).execute()
        if not r.data:
            raise HTTPException(status_code=404, detail="log not found")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[feedback] failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)[:200])


# ==================== Chat Endpoints ====================

@app.post("/api/chat")
async def chat_with_rag(request: ChatRequest, authorization: str = Header(None), x_forwarded_for: str = Header(None)):
    """Multi-turn RAG chat: embed last user message → search atoms → Claude with context."""
    import re as _re
    import anthropic as _anthropic

    # 可选鉴权：获取 user_id（游客为 None）
    user_id = await _get_user_id(authorization)
    guest_ip = (x_forwarded_for or "unknown").split(",")[0].strip()

    # 游客限流 / 注册用户积分检查
    if not user_id:
        if not _check_guest_limit(guest_ip):
            raise HTTPException(status_code=429, detail="GUEST_LIMIT")
    else:
        # 注册用户：检查积分（懒初始化 + 余额检查）
        _, _ = await _ensure_credits(authorization)

    if not _supabase:
        raise HTTPException(status_code=503, detail="Supabase unavailable")
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY not set")
    if not CLAUDE_API_KEY:
        raise HTTPException(status_code=503, detail="CLAUDE_API_KEY not set")

    user_messages = [m for m in request.messages if m.role == "user"]
    if not user_messages:
        raise HTTPException(status_code=400, detail="No user message")

    last_user_text = user_messages[-1].content.strip()
    if not last_user_text:
        raise HTTPException(status_code=400, detail="Empty user message")

    try:
        # 1. Embed last user message (provider from config: SiliconFlow / OpenAI)
        query_vec = _embed_text(last_user_text)

        # 2a. Vector search — top candidates, threshold 0.25
        SIMILARITY_THRESHOLD = 0.25
        rpc_params = {"query_embedding": query_vec, "match_count": 80}
        if request.video_ids:
            rpc_params["video_ids_filter"] = request.video_ids
        matches = _supabase.rpc("match_atoms_v1", rpc_params).execute()
        vec_rows = [r for r in (matches.data or []) if float(r["similarity"]) >= SIMILARITY_THRESHOLD]

        vec_atom_ids: list[str] = [r["atom_id"] for r in vec_rows]

        # 2b. Load alias map — merge person_network_aliases_v1 + entity_aliases_v1
        # Cached 5 min: these change rarely but were fetched on every message.
        import json as _json

        def _load_alias_map() -> dict:
            merged: dict[str, str] = {}
            try:
                _alias_row = _supabase.table("app_settings").select("value").eq("key", "person_network_aliases_v1").execute()
                if _alias_row.data:
                    merged = _json.loads(_alias_row.data[0]["value"]) or {}
            except Exception:
                pass
            try:
                _ea_row = _supabase.table("app_settings").select("value").eq("key", "entity_aliases_v1").execute()
                if _ea_row.data:
                    _ea: dict = _json.loads(_ea_row.data[0]["value"]) or {}
                    for alias, info in _ea.items():
                        canonical = info.get("canonical", "") if isinstance(info, dict) else str(info)
                        if canonical and alias not in merged:
                            merged[alias] = canonical
            except Exception:
                pass
            return merged

        _alias_map: dict[str, str] = _cached("chat_alias_map", 300, _load_alias_map)

        def _expand_token(tok: str) -> list[str]:
            """Return tok + all aliases (canonical and variants) for that token."""
            terms = {tok}
            # if tok is a variant, add canonical
            if tok in _alias_map:
                terms.add(_alias_map[tok])
            # if tok is a canonical, add all variants pointing to it
            for variant, canonical in _alias_map.items():
                if canonical == tok:
                    terms.add(variant)
            return list(terms)

        # 2b2. Entity-based retrieval — find person/org names in query,
        # pull ALL atoms that mention them via atom_entities join
        import re as _re2
        entity_atoms: list[dict] = []
        try:
            # Known entity names, cached 5 min. NOTE: column is entity_type
            # (the old "type" spelling silently broke this whole branch).
            def _load_entity_names() -> dict:
                rows = _supabase.table("entities").select("name,entity_type").limit(20000).execute()
                return {e["name"]: e["entity_type"] for e in (rows.data or []) if len(e.get("name") or "") >= 2}

            _entity_names = _cached("chat_entity_names", 300, _load_entity_names)
            # Find which entities are mentioned in the user query (including alias expansion)
            _matched_entities: set[str] = set()
            for ename in _entity_names:
                if ename in last_user_text:
                    _matched_entities.add(ename)
                    # Also add aliases
                    for term in _expand_token(ename):
                        _matched_entities.add(term)
            # Also check if user mentions an alias that maps to a known entity
            for tok_seg in _re2.findall(r'[\u4e00-\u9fff]{2,4}', last_user_text):
                if tok_seg in _alias_map:
                    canonical = _alias_map[tok_seg]
                    if canonical in _entity_names:
                        _matched_entities.add(canonical)
                        _matched_entities.add(tok_seg)

            if _matched_entities:
                logger.info(f"[chat] entity retrieval: matched entities = {_matched_entities}")
                # Query atom_entities for atoms mentioning these entities
                for entity_name in list(_matched_entities)[:6]:  # cap at 6 entities
                    ae_res = (_supabase.table("atom_entities")
                        .select("atom_id")
                        .eq("entity_name", entity_name)
                        .limit(30)
                        .execute())
                    ae_atom_ids = [r["atom_id"] for r in (ae_res.data or [])]
                    if ae_atom_ids:
                        ae_atoms_res = (_supabase.table("atoms")
                            .select("id,merged_text,start_ms,end_ms,video_id")
                            .in_("id", ae_atom_ids)
                            .execute())
                        for a in (ae_atoms_res.data or []):
                            entity_atoms.append(a)
        except Exception as ex:
            logger.warning(f"[chat] entity retrieval error: {ex}")

        # Keyword search — extract tokens from query
        # Split on common Chinese stop words / particles first, then extract 2-4 char segments
        _stop_particles = r'[的了和与跟在是把被从到对向让给也都又还且而但或及其之所以因为如果虽然不过然后于是因此所以怎么什么为什么怎样如何吗呢吧啊呀哦哈嗯]'
        _segments = _re2.split(_stop_particles, last_user_text)
        _raw_tokens = []
        for seg in _segments:
            # Extract CJK chunks from each segment
            chunks = _re2.findall(r'[\u4e00-\u9fff]+', seg.strip())
            for chunk in chunks:
                if 2 <= len(chunk) <= 4:
                    _raw_tokens.append(chunk)
                elif len(chunk) > 4:
                    # Slide 2-3-4 char windows for long chunks to catch names
                    for w in (3, 2, 4):
                        for i in range(0, len(chunk) - w + 1):
                            _raw_tokens.append(chunk[i:i+w])
        # Add English tokens
        _raw_tokens += _re2.findall(r'[A-Za-z]{3,}', last_user_text)
        # Deduplicate while preserving order
        tokens = list(dict.fromkeys(_raw_tokens))
        kw_atoms: list[dict] = []
        if tokens:
            seen_kw: set[str] = set(vec_atom_ids)
            for tok in tokens[:4]:
                for term in _expand_token(tok):
                    kw_q = _supabase.table("atoms").select("id,merged_text,start_ms,end_ms,video_id")
                    if request.video_ids:
                        kw_q = kw_q.in_("video_id", request.video_ids)
                    kw_res = kw_q.ilike("merged_text", f"%{term}%").limit(8).execute()
                    for a in (kw_res.data or []):
                        if a["id"] not in seen_kw:
                            seen_kw.add(a["id"])
                            kw_atoms.append(a)
                if len(kw_atoms) >= 30:
                    break

        # 2c. Merge all three sources: vector + entity + keyword, deduplicated
        atoms = []
        seen_final: set[str] = set()

        # Vector hits first (highest relevance)
        if vec_atom_ids:
            atoms_res = (
                _supabase.table("atoms")
                .select("id,merged_text,start_ms,end_ms,video_id")
                .in_("id", vec_atom_ids)
                .execute()
            )
            atom_map = {a["id"]: a for a in (atoms_res.data or [])}
            for r in vec_rows:
                a = atom_map.get(r["atom_id"]) or {}
                if r["atom_id"] not in seen_final:
                    seen_final.add(r["atom_id"])
                    atoms.append({
                        "atom_id": r["atom_id"],
                        "video_id": r.get("video_id") or a.get("video_id", ""),
                        "similarity": round(float(r["similarity"]), 4),
                        "text": a.get("merged_text", ""),
                        "start_ms": a.get("start_ms"),
                        "end_ms": a.get("end_ms"),
                        "source": "vector",
                    })

        # Entity hits second
        for a in entity_atoms:
            if a["id"] not in seen_final:
                seen_final.add(a["id"])
                atoms.append({
                    "atom_id": a["id"],
                    "video_id": a.get("video_id", ""),
                    "similarity": 0.0,
                    "text": a.get("merged_text", ""),
                    "start_ms": a.get("start_ms"),
                    "end_ms": a.get("end_ms"),
                    "source": "entity",
                })

        # Keyword hits last
        for a in kw_atoms:
            if a["id"] not in seen_final:
                seen_final.add(a["id"])
                atoms.append({
                    "atom_id": a["id"],
                    "video_id": a.get("video_id", ""),
                    "similarity": 0.0,
                    "text": a.get("merged_text", ""),
                    "start_ms": a.get("start_ms"),
                    "end_ms": a.get("end_ms"),
                    "source": "keyword",
                })

        logger.info(f"[chat] retrieval: {len(vec_rows)} vector + {len(entity_atoms)} entity + {len(kw_atoms)} keyword = {len(atoms)} total (deduped)")
        atoms = atoms[:80]  # cap initial hits at 80

        # ── 片段扩展：命中原子 → 拉同片段全部原子 ──
        hit_atom_ids = [a["atom_id"] for a in atoms]
        segment_ids: set[str] = set()
        segment_meta: dict[str, dict] = {}  # seg_id → {title, summary}

        if hit_atom_ids:
            # 查命中原子的 segment_id
            for chunk_start in range(0, len(hit_atom_ids), 200):
                chunk = hit_atom_ids[chunk_start:chunk_start+200]
                seg_res = _supabase.table("atoms").select("id,segment_id").in_("id", chunk).execute()
                for row in (seg_res.data or []):
                    sid = row.get("segment_id")
                    if sid:
                        segment_ids.add(sid)

        if segment_ids:
            # 加载片段元数据（标题）
            for chunk_start in range(0, len(list(segment_ids)), 50):
                chunk = list(segment_ids)[chunk_start:chunk_start+50]
                meta_res = _supabase.table("narrative_segments").select("id,title,summary").in_("id", chunk).execute()
                for row in (meta_res.data or []):
                    segment_meta[row["id"]] = {"title": row.get("title", ""), "summary": row.get("summary", "")}

            # 拉这些片段的全部原子
            context_atoms: list[dict] = []
            for chunk_start in range(0, len(list(segment_ids)), 50):
                chunk = list(segment_ids)[chunk_start:chunk_start+50]
                ctx_res = (_supabase.table("atoms")
                    .select("id,merged_text,start_ms,end_ms,video_id,segment_id")
                    .in_("segment_id", chunk)
                    .order("start_ms")
                    .execute())
                context_atoms.extend(ctx_res.data or [])

            # 合并：原始命中 + 片段扩展原子，去重
            expanded_seen = set(a["atom_id"] for a in atoms)
            for ca in context_atoms:
                if ca["id"] not in expanded_seen:
                    expanded_seen.add(ca["id"])
                    atoms.append({
                        "atom_id": ca["id"],
                        "video_id": ca.get("video_id", ""),
                        "similarity": 0.0,
                        "text": ca.get("merged_text", ""),
                        "start_ms": ca.get("start_ms"),
                        "end_ms": ca.get("end_ms"),
                        "segment_id": ca.get("segment_id"),
                        "source": "segment_expand",
                    })

            # 给原始命中的原子也补上 segment_id
            seg_id_lookup = {ca["id"]: ca.get("segment_id") for ca in context_atoms}
            for a in atoms:
                if "segment_id" not in a or not a.get("segment_id"):
                    a["segment_id"] = seg_id_lookup.get(a["atom_id"], "")

            logger.info(f"[chat] segment expansion: {len(segment_ids)} segments → {len(atoms)} total atoms")

        # 总量上限 120 条
        atoms = atoms[:120]

        # 3. Build system prompt — 按片段分组展示
        if atoms:
            # 按 segment_id 分组
            seg_groups: dict[str, list[dict]] = {}
            ungrouped: list[dict] = []
            for a in atoms:
                sid = a.get("segment_id", "")
                if sid:
                    seg_groups.setdefault(sid, []).append(a)
                else:
                    ungrouped.append(a)

            # 每个分组内按时间排序
            for sid in seg_groups:
                seg_groups[sid].sort(key=lambda x: x.get("start_ms") or 0)

            ctx_lines = []
            ref_num = 1
            ref_map: list[dict] = []  # 按编号映射回原子

            # 先输出有片段分组的
            for sid in sorted(seg_groups, key=lambda s: min(a.get("start_ms") or 0 for a in seg_groups[s])):
                meta = segment_meta.get(sid, {})
                title = meta.get("title", sid)
                t_start = _ms_to_hhmmss(min(a.get("start_ms") or 0 for a in seg_groups[sid]))
                t_end = _ms_to_hhmmss(max(a.get("end_ms") or 0 for a in seg_groups[sid]))
                ctx_lines.append(f"\n--- {title}（{t_start}–{t_end}）---")
                for a in seg_groups[sid]:
                    ts = _ms_to_hhmmss(a.get("start_ms"))
                    ctx_lines.append(f"[{ref_num}] ({ts}) {a['text']}")
                    a["_ref_num"] = ref_num
                    ref_map.append(a)
                    ref_num += 1

            # 再输出未分组的
            if ungrouped:
                ctx_lines.append("\n--- 其他相关片段 ---")
                for a in ungrouped:
                    ts = _ms_to_hhmmss(a.get("start_ms"))
                    vid = a.get("video_id", "")
                    ctx_lines.append(f"[{ref_num}] (视频 {vid} {ts}) {a['text']}")
                    a["_ref_num"] = ref_num
                    ref_map.append(a)
                    ref_num += 1

            context_block = "\n".join(ctx_lines)
        else:
            context_block = "（未找到相关视频片段）"
            ref_map = []

        system_prompt = (
            "你是 SenseClip，一个专注于中国政治历史的视频资料分析助手。\n"
            "以下是根据用户问题检索到的相关视频原文片段，按主题分组：\n\n"
            f"{context_block}\n\n"
            "回答要求：\n"
            "- 优先基于以上视频资料作答，用 [1][2] 等标注引用来源编号\n"
            "- 同一主题的连续片段可以综合引用\n"
            "- 资料不足时可补充背景知识，但需区分【资料显示】与【一般认为】\n"
            "- 简洁、有据，用中文回答"
        )

        # 4. 生成回答 —— 主引擎 DeepSeek v4-flash(成本 ~1/20 of Claude Sonnet),
        #    失败时回退 Claude。附件统一走文本注入(图片经 Claude 视觉转文字)。
        chat_messages = [{"role": m.role, "content": m.content} for m in request.messages]
        if request.attachments and chat_messages:
            last_idx = max(i for i, m in enumerate(chat_messages) if m["role"] == "user")
            attach_text = _attachments_to_text(request.attachments)
            chat_messages[last_idx] = {
                "role": "user",
                "content": chat_messages[last_idx]["content"] + "\n\n" + attach_text,
            }

        answer = None
        model_used = "deepseek-v4-flash"
        try:
            from config import DEEPSEEK_API_KEY as _ds_key
            if not _ds_key:
                raise RuntimeError("DEEPSEEK_API_KEY not set")
            import openai as _openai_mod
            ds = _openai_mod.OpenAI(api_key=_ds_key, base_url="https://api.deepseek.com/v1")
            ds_resp = ds.chat.completions.create(
                model="deepseek-v4-flash",
                max_tokens=2500,
                messages=[{"role": "system", "content": system_prompt}] + chat_messages,
                timeout=120,
            )
            answer = ds_resp.choices[0].message.content
            if _is_censored(answer):
                logger.info("[chat] DeepSeek censored the answer, falling back to Claude")
                answer = None
        except Exception as ds_err:
            logger.warning(f"[chat] DeepSeek failed, falling back to Claude: {str(ds_err)[:100]}")

        if not answer:
            model_used = "claude-sonnet-4-6"
            claude = _anthropic.Anthropic(api_key=CLAUDE_API_KEY)
            resp = claude.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2000,
                system=system_prompt,
                messages=chat_messages,
            )
            answer = resp.content[0].text

        # 5. Parse citation references from answer — use ref_map for correct mapping
        cited_indices = sorted(set(int(x) for x in _re.findall(r'\[(\d+)\]', answer)))
        citation_source = ref_map if ref_map else atoms
        citations = [
            {**citation_source[i - 1], "ref_num": i}
            for i in cited_indices
            if 1 <= i <= len(citation_source)
        ]

        # 6. Save chat log
        log_id = _save_chat_log(user_id, guest_ip, last_user_text, answer, len(atoms), model_used)

        # 7. Deduct 1 credit for registered users
        credits_remaining = None
        if user_id:
            try:
                credits_remaining = _deduct_credit(user_id, "", f"Chat: {last_user_text[:80]}")
            except Exception:
                pass

        return {"message": answer, "citations": citations, "credits_remaining": credits_remaining,
                "log_id": log_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _infer_failed_step_from_error(error_message: Optional[str]) -> Optional[str]:
    msg = (error_message or "").lower()
    if not msg:
        return None
    if "extract_topics" in msg or "topic" in msg:
        return "topics_extracted"
    if "extract_entities" in msg or "entityextractor" in msg or "atom_id" in msg:
        return "entities_extracted"
    if "segment" in msg:
        return "segmented"
    if "atom" in msg:
        return "atomized"
    if "transcrib" in msg or "subtitle" in msg or "srt" in msg:
        return "transcribed"
    if "audio" in msg or "yt-dlp" in msg or "download" in msg:
        return "audio_ready"
    return None


def _previous_step(step: Optional[str]) -> Optional[str]:
    prev = {
        "entities_extracted": "segmented",
        "topics_extracted": "entities_extracted",
        "vectorized": "topics_extracted",
        "processed": "vectorized",
        "segmented": "atomized",
        "atomized": "transcribed",
        "transcribed": "audio_ready",
    }
    return prev.get(step or "")


def _step_rank_for_backfill(step: Optional[str]) -> int:
    rank = {
        "audio_ready": 1,
        "transcribed": 2,
        "atomized": 3,
        "segmented": 4,
        "entities_extracted": 5,
        "topics_extracted": 6,
        "vectorized": 7,
        "processed": 8,
    }
    return rank.get(step or "", 0)


@app.post("/api/library/backfill-status")
async def backfill_library_status(youtube_url: Optional[str] = None, limit: int = 500):
    """Backfill historical status fields based on real assets + error heuristics."""
    if not _supabase:
        raise HTTPException(status_code=503, detail="Supabase 未配置")

    try:
        assets_query = _supabase.table("video_assets").select(
            "youtube_url,video_id,db_video_id,status,audio_url,srt_content,error_message,updated_at"
        )
        if youtube_url:
            assets_query = assets_query.eq("youtube_url", youtube_url)
        assets = assets_query.order("updated_at", desc=True).limit(limit).execute().data

        urls = [a.get("youtube_url") for a in assets if a.get("youtube_url")]
        jobs_by_url = {}
        jobs_have_step_columns = True
        if urls:
            try:
                jobs = _supabase.table("pipeline_jobs").select(
                    "id,youtube_url,status,current_step,error_message,last_successful_step,failed_step,created_at"
                ).in_("youtube_url", urls).order("created_at", desc=True).execute().data
            except Exception:
                jobs_have_step_columns = False
                jobs = _supabase.table("pipeline_jobs").select(
                    "id,youtube_url,status,current_step,error_message,created_at"
                ).in_("youtube_url", urls).order("created_at", desc=True).execute().data
            for job in jobs:
                u = job.get("youtube_url")
                if u and u not in jobs_by_url:
                    jobs_by_url[u] = job

        updated_assets = 0
        updated_jobs = 0
        inspected = 0

        for asset in assets:
            inspected += 1
            url = asset.get("youtube_url")
            if not url:
                continue
            latest_job = jobs_by_url.get(url)

            inferred_step = None
            # 1) Real assets in DB (best source)
            db_video_id = asset.get("db_video_id")
            if db_video_id:
                inferred_step = _infer_step_from_real_assets(db_video_id)

            # 2) fallback to existing asset fields
            if not inferred_step:
                st = asset.get("status")
                has_db_video = bool(db_video_id)
                # If there is a db_video_id but no real persisted assets, prefer prerequisites
                # over stale processed status.
                if asset.get("srt_content"):
                    inferred_step = "transcribed"
                elif asset.get("audio_url"):
                    inferred_step = "audio_ready"
                elif not has_db_video and st in {"audio_ready", "transcribed", "atomized", "segmented", "entities_extracted", "topics_extracted", "vectorized", "processed"}:
                    inferred_step = st

            inferred_failed = None
            if latest_job and latest_job.get("status") == "error":
                inferred_failed = latest_job.get("failed_step") or _infer_failed_step_from_error(latest_job.get("error_message"))
                inferred_from_error = _previous_step(inferred_failed)
                if _step_rank_for_backfill(inferred_from_error) > _step_rank_for_backfill(inferred_step):
                    inferred_step = inferred_from_error

            # Update asset status only forward
            if inferred_step and asset.get("status") != inferred_step:
                _supabase.table("video_assets").update({"status": inferred_step}).eq("youtube_url", url).execute()
                updated_assets += 1

            # Keep /api/videos coherent with real persisted assets.
            if db_video_id:
                metrics = _get_video_asset_metrics(db_video_id)
                video_status = "completed" if metrics.get("segment_count", 0) > 0 else "processing"
                _supabase.table("videos").update({
                    "status": video_status,
                    "atom_count": metrics.get("atom_count", 0),
                    "segment_count": metrics.get("segment_count", 0),
                    "entity_count": metrics.get("atom_entity_count", 0),
                }).eq("id", db_video_id).execute()

            # Update latest job fields
            if latest_job:
                patch = {}
                if jobs_have_step_columns:
                    if inferred_step and latest_job.get("last_successful_step") != inferred_step:
                        patch["last_successful_step"] = inferred_step
                    if inferred_failed and latest_job.get("failed_step") != inferred_failed:
                        patch["failed_step"] = inferred_failed
                if patch:
                    _supabase.table("pipeline_jobs").update(patch).eq("id", latest_job.get("id")).execute()
                    updated_jobs += 1

        return {
            "success": True,
            "inspected_assets": inspected,
            "updated_assets": updated_assets,
            "updated_jobs": updated_jobs,
            "jobs_have_step_columns": jobs_have_step_columns,
            "filtered_youtube_url": youtube_url,
        }
    except Exception as e:
        logger.error(f"Backfill failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Supabase Video Analysis Endpoints ====================

@app.get("/api/videos/{video_id}/analysis")
async def get_video_analysis(video_id: str):
    """从 Supabase 读取视频完整分析结果（atoms, segments, entities）"""
    if not _supabase:
        raise HTTPException(status_code=503, detail="Supabase 未配置")
    try:
        # 视频基本信息
        video = _load_video_row(video_id)
        if video:
            atoms = _load_atom_rows(video_id)
            segments = _load_segment_rows(video_id)
            atom_entities = _load_atom_entities_map([atom.get("id") for atom in atoms if atom.get("id")])
            entities = _aggregate_entities(atom_entities)
            return {
                "video": video,
                "atoms": atoms,
                "segments": segments,
                "entities": entities,
                "atom_entities": atom_entities,
                "stats": {
                    "atom_count": len(atoms),
                    "segment_count": len(segments),
                    "entity_count": len(entities),
                }
            }
        video_res = _supabase.table("videos").select("*").eq("id", video_id).execute()
        if not video_res.data:
            raise HTTPException(status_code=404, detail=f"视频不存在: {video_id}")
        video = video_res.data[0]

        # 原子
        atoms_res = _supabase.table("atoms").select(
            "id,atom_id,start_ms,end_ms,merged_text,type,segment_id"
        ).eq("video_id", video_id).order("start_ms").execute()

        # 叙事片段
        segments_res = _supabase.table("narrative_segments").select(
            "id,title,summary,start_ms,end_ms,topics,key_entities"
        ).eq("video_id", video_id).order("start_ms").execute()

        # 实体
        entities_res = _supabase.table("entities").select(
            "id,name,entity_type,description,mention_count,importance_score"
        ).eq("video_id", video_id).order("importance_score", desc=True).execute()

        return {
            "video": video,
            "atoms": atoms_res.data,
            "segments": segments_res.data,
            "entities": entities_res.data,
            "stats": {
                "atom_count": len(atoms_res.data),
                "segment_count": len(segments_res.data),
                "entity_count": len(entities_res.data),
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取视频分析失败 {video_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/videos")
async def list_videos():
    """列出所有已分析的视频（从 Supabase videos 表）"""
    if not _supabase:
        raise HTTPException(status_code=503, detail="Supabase 未配置")
    try:
        res = _supabase.table("videos").select(
            "id,title,status,atom_count,segment_count,entity_count,created_at"
        ).order("created_at", desc=True).execute()

        rows = res.data or []
        metrics_cache: Dict[str, Dict[str, int]] = {}
        normalized: List[Dict[str, Any]] = []

        for row in rows:
            video_id = row.get("id")
            original_status = row.get("status")
            metrics = metrics_cache.get(video_id)
            if metrics is None:
                metrics = _get_video_asset_metrics(video_id)
                metrics_cache[video_id] = metrics
            inferred_step = _infer_step_from_real_assets(video_id)

            if metrics.get("segment_count", 0) > 0:
                effective_status = "completed"
            elif metrics.get("atom_count", 0) > 0:
                effective_status = "processing"
            elif original_status == "processing":
                effective_status = "processing"
            else:
                effective_status = "pending"

            normalized.append({
                **row,
                "status": effective_status,
                "pipeline_status": original_status,
                "inferred_step": inferred_step,
                "analysis_ready": metrics.get("segment_count", 0) > 0,
                "atom_count": metrics.get("atom_count", 0),
                "segment_count": metrics.get("segment_count", 0),
                "entity_count": metrics.get("atom_entity_count", 0),
            })

        return {"videos": normalized}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Data Endpoints ====================

@app.get("/api/projects/{project_id}/atoms")
async def get_atoms(project_id: str):
    """Get all atoms"""
    try:
        atoms = read_jsonl_file("atoms.jsonl")
        logger.info(f"Loaded {len(atoms)} atoms")
        return {"atoms": atoms, "count": len(atoms)}
    except Exception as e:
        logger.error(f"Error loading atoms: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/projects/{project_id}/segments")
async def get_segments(project_id: str):
    """Get all narrative segments"""
    try:
        segments = read_json_file("narrative_segments.json")
        # Handle both list and dict formats
        if isinstance(segments, list):
            segment_list = segments
        else:
            segment_list = segments.get('segments', [])

        logger.info(f"Loaded {len(segment_list)} segments")
        return {"segments": segment_list, "count": len(segment_list)}
    except Exception as e:
        logger.error(f"Error loading segments: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/projects/{project_id}/entities")
async def get_entities(project_id: str):
    """Get all entities"""
    try:
        entities_data = read_json_file("entities.json")

        # Transform entities data for frontend
        entities_list = []
        for category, entities in entities_data.items():
            if isinstance(entities, list):
                for entity in entities:
                    # Use 'atoms' field from data, rename to 'atom_ids' for frontend
                    atom_ids = entity.get("atoms", entity.get("atom_ids", []))
                    entities_list.append({
                        "name": entity.get("name", ""),
                        "type": entity.get("type", category),
                        "category": category,
                        "count": entity.get("mentions", len(atom_ids)),
                        "importance": entity.get("importance", 0.5),
                        "atom_ids": atom_ids
                    })

        logger.info(f"Loaded {len(entities_list)} entities")
        return {"entities": entities_list, "count": len(entities_list)}
    except Exception as e:
        logger.error(f"Error loading entities: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/projects/{project_id}/topics")
async def get_topics(project_id: str):
    """Get topic network"""
    try:
        topics = read_json_file("topics.json")
        logger.info(f"Loaded topics data")
        return topics
    except Exception as e:
        logger.error(f"Error loading topics: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/projects/{project_id}/graph")
async def get_graph(project_id: str):
    """Get knowledge graph"""
    try:
        graph_path = DATA_DIR / "knowledge_graph.json"
        if not graph_path.exists():
            raise HTTPException(status_code=404, detail="Knowledge graph not found")

        with open(graph_path, 'r', encoding='utf-8') as f:
            graph_data = json.load(f)

        # Graph data is already in D3.js format with nodes/edges
        nodes = graph_data.get('nodes', [])
        edges = graph_data.get('edges', [])

        logger.info(f"Loaded graph: {len(nodes)} nodes, {len(edges)} edges")
        return {
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "node_count": len(nodes),
                "edge_count": len(edges)
            }
        }
    except Exception as e:
        logger.error(f"Error loading graph: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/projects/{project_id}/creative")
async def get_creative(project_id: str):
    """Get creative angles and clip recommendations"""
    try:
        creative = read_json_file("creative_angles.json")
        logger.info(f"Loaded creative angles data")
        return creative
    except Exception as e:
        logger.error(f"Error loading creative angles: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/projects/{project_id}/overview")
async def get_overview(project_id: str):
    """Get project overview with all stats"""
    try:
        atoms = read_jsonl_file("atoms.jsonl")
        segments = read_json_file("narrative_segments.json")
        entities_data = read_json_file("entities.json")

        # Count entities
        entity_count = 0
        for category, entities in entities_data.items():
            if isinstance(entities, list):
                entity_count += len(entities)

        # Load graph stats
        graph_path = DATA_DIR / "knowledge_graph.json"
        edge_count = 0
        if graph_path.exists():
            with open(graph_path, 'r', encoding='utf-8') as f:
                graph_data = json.load(f)
                edge_count = len(graph_data.get('edges', []))

        segment_list = segments if isinstance(segments, list) else segments.get('segments', [])

        return {
            "project_id": project_id,
            "stats": {
                "atoms": len(atoms),
                "segments": len(segment_list),
                "entities": entity_count,
                "relations": edge_count
            }
        }
    except Exception as e:
        logger.error(f"Error loading overview: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/projects/{project_id}/report")
async def get_report(project_id: str):
    """Get video structure report (Markdown)"""
    try:
        report_path = DATA_DIR / "video_structure.md"
        if not report_path.exists():
            raise HTTPException(status_code=404, detail="Report not found")

        with open(report_path, 'r', encoding='utf-8') as f:
            content = f.read()

        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(content)
    except Exception as e:
        logger.error(f"Error loading report: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ==================== Analysis Endpoints ====================

@app.post("/api/projects/{project_id}/analyze/full")
async def start_full_analysis(project_id: str, chunk_size: int = 50):
    """Start full video analysis (legacy - use incremental instead)"""
    try:
        analysis_service.start_full_analysis(project_id, chunk_size)
        return {
            "status": "started",
            "message": "Full video analysis started",
            "project_id": project_id
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error starting analysis: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/projects/{project_id}/analyze/status")
async def get_analysis_status(project_id: str):
    """Get analysis progress (legacy)"""
    try:
        progress = analysis_service.get_progress()
        return progress
    except Exception as e:
        logger.error(f"Error getting analysis status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/projects/{project_id}/analyze/cancel")
async def cancel_analysis(project_id: str):
    """Cancel running analysis (legacy)"""
    try:
        analysis_service.cancel_analysis()
        return {
            "status": "cancelled",
            "message": "Analysis cancelled",
            "project_id": project_id
        }
    except Exception as e:
        logger.error(f"Error cancelling analysis: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ==================== Incremental Analysis Endpoints ====================

@app.post("/api/projects/{project_id}/analyze/incremental/start")
async def start_incremental_analysis(project_id: str):
    """Start incremental segment-by-segment analysis"""
    try:
        incremental_service.start_incremental_analysis(project_id)
        return {
            "status": "started",
            "message": "Incremental analysis started",
            "project_id": project_id
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error starting incremental analysis: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/projects/{project_id}/analyze/incremental/stop")
async def stop_incremental_analysis(project_id: str):
    """Stop incremental analysis"""
    try:
        incremental_service.stop_analysis()
        return {
            "status": "stopped",
            "message": "Incremental analysis stopped",
            "project_id": project_id
        }
    except Exception as e:
        logger.error(f"Error stopping incremental analysis: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/projects/{project_id}/analyze/incremental/progress")
async def get_incremental_progress(project_id: str):
    """Get incremental analysis progress with segment details"""
    try:
        # Prefer Supabase-backed data for project/video specific progress.
        # Never fall back to legacy local-file global progress when Supabase is enabled,
        # otherwise project pages can be overwritten by unrelated zero-state data.
        if _supabase:
            try:
                segments_rows = _load_segment_rows(project_id)
                if segments_rows:
                    atoms = _load_atom_rows(project_id)
                    atom_ids = [atom.get("id") for atom in atoms if atom.get("id")]
                    atom_entities = _load_atom_entities_map(atom_ids)
                    entity_names_by_segment: Dict[str, set] = {}
                    global_entity_names = set()
                    for atom in atoms:
                        atom_id = atom.get("id")
                        segment_id = atom.get("segment_id")
                        if not atom_id or not segment_id:
                            continue
                        for entity in atom_entities.get(atom_id, []):
                            entity_name = entity.get("entity_name")
                            if not entity_name:
                                continue
                            global_entity_names.add(entity_name)
                            entity_names_by_segment.setdefault(segment_id, set()).add(entity_name)

                    def _format_progress_time(ms: Optional[int]) -> str:
                        total_seconds = max(0, int(ms or 0)) // 1000
                        hours = total_seconds // 3600
                        minutes = (total_seconds % 3600) // 60
                        seconds = total_seconds % 60
                        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

                    segments = []
                    for seg in segments_rows:
                        key_entities = seg.get("key_entities") or []
                        segment_entities = entity_names_by_segment.get(seg.get("id"), set())
                        segments.append({
                            "segment_id": seg.get("id"),
                            "start_time": _format_progress_time(seg.get("start_ms", 0)),
                            "end_time": _format_progress_time(seg.get("end_ms", 0)),
                            "duration_ms": (
                                seg.get("duration_ms")
                                or max(0, int(seg.get("end_ms", 0) or 0) - int(seg.get("start_ms", 0) or 0))
                            ),
                            "atom_count": int(seg.get("atom_count", 0) or 0),
                            "status": "analyzed",
                            "atomization_complete": True,
                            "analysis_complete": True,
                            "entity_count": len(segment_entities) if segment_entities else len(key_entities),
                            "error_message": None,
                        })

                    total_segments = len(segments)
                    return {
                        "status": "completed",
                        "current_step": "Analysis complete (Supabase)",
                        "progress_percent": 100 if total_segments > 0 else 0,
                        "total_segments": total_segments,
                        "analyzed_segments": total_segments,
                        "pending_segments": 0,
                        "failed_segments": 0,
                        "total_entities": len(global_entity_names),
                        "segments": segments,
                        "is_running": False,
                        "current_segment": None,
                    }

                atoms = _load_atom_rows(project_id)
                if atoms:
                    atom_ids = [atom.get("id") for atom in atoms if atom.get("id")]
                    atom_entities = _load_atom_entities_map(atom_ids)

                    by_segment: Dict[str, Dict[str, Any]] = {}
                    global_entity_names = set()
                    for atom in atoms:
                        segment_id = atom.get("segment_id") or "SEG_001"
                        start_ms = int(atom.get("start_ms", 0) or 0)
                        end_ms = int(atom.get("end_ms", 0) or 0)
                        slot = by_segment.setdefault(segment_id, {
                            "segment_id": segment_id,
                            "start_ms": start_ms,
                            "end_ms": end_ms,
                            "atom_count": 0,
                            "entity_names": set(),
                        })
                        slot["start_ms"] = min(int(slot["start_ms"]), start_ms)
                        slot["end_ms"] = max(int(slot["end_ms"]), end_ms)
                        slot["atom_count"] = int(slot["atom_count"]) + 1
                        for entity in atom_entities.get(atom.get("id"), []):
                            entity_name = entity.get("entity_name")
                            if entity_name:
                                slot["entity_names"].add(entity_name)
                                global_entity_names.add(entity_name)

                    ordered = sorted(by_segment.values(), key=lambda s: int(s["start_ms"]))
                    segments = []
                    for seg in ordered:
                        entity_count = len(seg["entity_names"])
                        segments.append({
                            "segment_id": seg["segment_id"],
                            "start_time": _ms_to_hhmmss(seg["start_ms"]),
                            "end_time": _ms_to_hhmmss(seg["end_ms"]),
                            "duration_ms": max(0, int(seg["end_ms"]) - int(seg["start_ms"])),
                            "atom_count": int(seg["atom_count"]),
                            "status": "analyzed" if entity_count > 0 else "atomized",
                            "atomization_complete": True,
                            "analysis_complete": entity_count > 0,
                            "entity_count": entity_count,
                            "error_message": None,
                        })

                    total_segments = len(segments)
                    analyzed_segments = sum(1 for seg in segments if seg["analysis_complete"])
                    pending_segments = max(0, total_segments - analyzed_segments)
                    progress_percent = int((analyzed_segments / total_segments) * 100) if total_segments > 0 else 0

                    if analyzed_segments == total_segments and total_segments > 0:
                        status = "completed"
                        current_step = "Analysis complete (Supabase)"
                    elif total_segments > 0:
                        status = "running"
                        current_step = "Entity/topic analysis in progress (Supabase)"
                    else:
                        status = "idle"
                        current_step = "No segments available"

                    return {
                        "status": status,
                        "current_step": current_step,
                        "progress_percent": progress_percent,
                        "total_segments": total_segments,
                        "analyzed_segments": analyzed_segments,
                        "pending_segments": pending_segments,
                        "failed_segments": 0,
                        "total_entities": len(global_entity_names),
                        "segments": segments,
                        "is_running": status == "running",
                        "current_segment": None,
                    }

                # Supabase is configured but no persisted segment/atom assets yet.
                return {
                    "status": "idle",
                    "current_step": "No analysis assets yet",
                    "progress_percent": 0,
                    "total_segments": 0,
                    "analyzed_segments": 0,
                    "pending_segments": 0,
                    "failed_segments": 0,
                    "total_entities": 0,
                    "segments": [],
                    "is_running": False,
                    "current_segment": None,
                }
            except Exception as supabase_error:
                logger.warning(f"Supabase progress query failed for {project_id}: {supabase_error}")
                raise HTTPException(status_code=503, detail="Progress temporarily unavailable")

        progress = incremental_service.get_progress()
        return progress
    except Exception as e:
        logger.error(f"Error getting incremental progress: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/projects/{project_id}/segments")
async def get_segments(project_id: str):
    """Get all time segments with their status"""
    try:
        segments = segment_manager.load_segments_state()
        return {
            "segments": [
                {
                    "segment_id": seg.segment_id,
                    "start_time": seg.start_time_str,
                    "end_time": seg.end_time_str,
                    "duration_ms": seg.duration_ms,
                    "atom_count": len(seg.atom_ids),
                    "status": seg.status,
                    "atomization_complete": seg.atomization_complete,
                    "analysis_complete": seg.analysis_complete,
                    "entity_count": seg.entity_count,
                    "error_message": seg.error_message
                }
                for seg in segments
            ],
            "total_segments": len(segments)
        }
    except Exception as e:
        logger.error(f"Error getting segments: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/projects/{project_id}/analyze/incremental/reset")
async def reset_incremental_analysis(project_id: str):
    """Reset analysis status (keep atomization)"""
    try:
        segment_manager.reset_analysis()
        return {
            "status": "reset",
            "message": "Analysis status reset, atomization preserved",
            "project_id": project_id
        }
    except Exception as e:
        logger.error(f"Error resetting analysis: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/projects/{project_id}/analyze/segment/{segment_id}")
async def analyze_single_segment(project_id: str, segment_id: str):
    """Analyze a single specific segment"""
    try:
        incremental_service.analyze_single_segment(project_id, segment_id)
        return {
            "status": "started",
            "message": f"Analysis started for segment {segment_id}",
            "project_id": project_id,
            "segment_id": segment_id
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error analyzing segment {segment_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/projects/{project_id}/segments/recreate")
async def recreate_segments(project_id: str, segment_duration_minutes: int = 20):
    """Recreate segments with new duration"""
    try:
        segments = segment_manager.recreate_segments(segment_duration_minutes)
        return {
            "status": "recreated",
            "message": f"Segments recreated with {segment_duration_minutes} minute duration",
            "total_segments": len(segments),
            "project_id": project_id
        }
    except Exception as e:
        logger.error(f"Error recreating segments: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/projects/{project_id}/segments/{segment_id}/reset")
async def reset_segment_analysis(project_id: str, segment_id: str):
    """Reset analysis status for a specific segment"""
    try:
        segment_manager.reset_segment_analysis(segment_id)
        return {
            "status": "reset",
            "message": f"Analysis reset for segment {segment_id}",
            "project_id": project_id,
            "segment_id": segment_id
        }
    except Exception as e:
        logger.error(f"Error resetting segment {segment_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/projects/{project_id}/analyze/global")
async def start_global_analysis(project_id: str):
    """Start global analysis - analyze entire video without segmentation"""
    try:
        from pathlib import Path
        import json
        from tqdm import tqdm
        from video_understanding_engine.analyzers.entity_extractor import EntityExtractor
        from video_understanding_engine.analyzers.topic_network_builder import TopicNetworkBuilder
        from video_understanding_engine.analyzers.knowledge_graph_builder import KnowledgeGraphBuilder

        logger.info(f"Starting global analysis for project {project_id}")

        # Load all atoms
        atoms_path = Path("D:/code/youtube/video_understanding_engine/data/output/atoms_full.jsonl")
        if not atoms_path.exists():
            raise HTTPException(status_code=404, detail="Atoms file not found. Please run atomization first.")

        atoms = []
        with open(atoms_path, 'r', encoding='utf-8') as f:
            for line in f:
                atoms.append(json.loads(line.strip()))

        logger.info(f"Loaded {len(atoms)} atoms for global analysis")

        # Create mock segment for entity extraction
        def create_mock_segment(atoms_data, segment_id="GLOBAL"):
            class MockAtom:
                def __init__(self, data):
                    self.atom_id = data['atom_id']
                    self.merged_text = data['merged_text']
                    self.start_ms = data.get('start_ms', 0)
                    self.end_ms = data.get('end_ms', 0)

            class MockEntities:
                def __init__(self):
                    self.persons = []
                    self.countries = []
                    self.organizations = []
                    self.time_points = []
                    self.events = []
                    self.concepts = []

            class MockNarrative:
                def __init__(self):
                    self.primary_topic = "全局视频分析"
                    self.secondary_topics = []
                    self.tags = []

            class MockSegment:
                def __init__(self, atoms_data, seg_id):
                    self.segment_id = seg_id
                    self.atoms = [MockAtom(a) for a in atoms_data]
                    self.entities = MockEntities()
                    self.narrative_arc = MockNarrative()
                    self.full_text = " ".join([a['merged_text'] for a in atoms_data])

            return MockSegment(atoms_data, segment_id)

        # Process in chunks to avoid token limits
        chunk_size = 50
        segments = []

        for i in range(0, len(atoms), chunk_size):
            chunk = atoms[i:i+chunk_size]
            seg_id = f"GLOBAL_{i//chunk_size + 1:03d}"
            segments.append(create_mock_segment(chunk, seg_id))

        # Extract entities globally
        logger.info("Extracting entities globally...")
        extractor = EntityExtractor()

        all_entities = {
            'persons': {},
            'countries': {},
            'organizations': {},
            'time_points': {},
            'events': {},
            'concepts': {}
        }

        for seg in segments:
            result = extractor.extract([seg])

            # Merge results
            for entity_type in all_entities.keys():
                for entity in result.get(entity_type, []):
                    name = entity['name']
                    if name not in all_entities[entity_type]:
                        all_entities[entity_type][name] = entity
                    else:
                        # Merge atoms and segments
                        existing = all_entities[entity_type][name]
                        existing['mentions'] += entity.get('mentions', 1)
                        existing['atoms'] = list(set(existing.get('atoms', []) + entity.get('atoms', [])))
                        existing['segments'] = list(set(existing.get('segments', []) + entity.get('segments', [])))

        # Convert back to lists
        final_entities = {}
        for entity_type, entities_dict in all_entities.items():
            final_entities[entity_type] = list(entities_dict.values())

        # Calculate statistics
        final_entities['statistics'] = {
            'total_entities': sum(len(entities) for entities in final_entities.values() if isinstance(entities, list)),
            'by_type': {k: len(v) for k, v in final_entities.items() if isinstance(v, list)}
        }

        # Save entities
        output_dir = Path("D:/code/youtube/video_understanding_engine/data/output_pipeline_v3")
        output_dir.mkdir(parents=True, exist_ok=True)

        entities_file = output_dir / "entities.json"
        with open(entities_file, 'w', encoding='utf-8') as f:
            json.dump(final_entities, f, ensure_ascii=False, indent=2)

        # Update frontend data directories
        frontend_paths = [
            Path("D:/code/youtube/atom-viewer/public/data/output_pipeline_v3/overview.json"),
            Path("D:/code/youtube/atom-viewer/public/data/project_001/overview.json")
        ]

        # Create overview data
        overview_data = {
            "atoms": atoms,
            "total_atoms": len(atoms),
            "analysis_type": "global",
            "timestamp": str(datetime.now()),
            "project_id": project_id,
            "entities": final_entities
        }

        # Update both frontend directories
        for frontend_path in frontend_paths:
            frontend_path.parent.mkdir(parents=True, exist_ok=True)
            with open(frontend_path, 'w', encoding='utf-8') as f:
                json.dump(overview_data, f, ensure_ascii=False, indent=2)

        logger.info(f"Global analysis completed with {len(atoms)} atoms and {final_entities['statistics']['total_entities']} entities")

        return {
            "status": "completed",
            "message": "Global analysis completed successfully",
            "project_id": project_id,
            "total_atoms": len(atoms),
            "total_entities": final_entities['statistics']['total_entities'],
            "analysis_type": "global"
        }

    except Exception as e:
        logger.error(f"Error in global analysis: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ==================== Segment Detail Endpoints ====================

@app.get("/api/projects/{project_id}/segments/{segment_id}/detail")
async def get_segment_detail(project_id: str, segment_id: str):
    """
    Get detailed three-level analysis for a specific segment

    Returns:
    - atom_level: List of atoms with topics, entities, emotions, embedding status
    - segment_level: Aggregate statistics and distributions
    - narrative_level: Narrative context if applicable
    """
    try:
        if _supabase:
            segment_rows = [segment for segment in _load_segment_rows(project_id) if segment.get("id") == segment_id]
            if segment_rows:
                segment = segment_rows[0]
                atoms = _load_atom_rows(project_id, segment_id)
                atom_ids = [atom.get("id") for atom in atoms if atom.get("id")]
                annotations = _load_annotations_map(atom_ids)
                atom_entities = _load_atom_entities_map(atom_ids)
                embeddings = _load_embeddings_map(atom_ids)

                atom_level = []
                topic_distribution: Dict[str, int] = {}
                entity_distribution: Dict[str, int] = {}
                emotion_distribution: Dict[str, int] = {}
                importance_values: List[float] = []

                for atom in atoms:
                    atom_id = atom.get("id")
                    annotation = annotations.get(atom_id, {})
                    topics = annotation.get("topics") or []
                    entities = [
                        {
                            "name": item.get("entity_name"),
                            "type": item.get("entity_type"),
                            "confidence": float(item.get("confidence") or 0),
                        }
                        for item in atom_entities.get(atom_id, [])
                        if item.get("entity_name")
                    ]
                    emotion_type = annotation.get("emotion_type")
                    importance_score = float(annotation.get("importance_score") or 0)
                    importance_values.append(importance_score)

                    for topic in topics:
                        topic_distribution[topic] = topic_distribution.get(topic, 0) + 1
                    for entity in entities:
                        entity_name = entity["name"]
                        entity_distribution[entity_name] = entity_distribution.get(entity_name, 0) + 1
                    if emotion_type:
                        emotion_distribution[emotion_type] = emotion_distribution.get(emotion_type, 0) + 1

                    atom_level.append({
                        "atom_id": atom_id,
                        "text_snippet": atom.get("merged_text") or "",
                        "start_ms": atom.get("start_ms") or 0,
                        "end_ms": atom.get("end_ms") or 0,
                        "duration_ms": atom.get("duration_ms") or 0,
                        "topics": topics,
                        "entities": entities,
                        "emotion": {
                            "type": emotion_type,
                            "score": float(annotation.get("emotion_score") or 0),
                            "confidence": float(annotation.get("emotion_confidence") or 0),
                        } if emotion_type else None,
                        "importance_score": importance_score,
                        "quality_score": float(annotation.get("quality_score") or 0),
                        "has_entity": bool(annotation.get("has_entity")) or bool(entities),
                        "has_topic": bool(annotation.get("has_topic")) or bool(topics),
                        "embedding_status": (
                            embeddings.get(atom_id, {}).get("status")
                            or annotation.get("embedding_status")
                            or "pending"
                        ),
                    })

                dominant_emotion = None
                if emotion_distribution:
                    dominant_emotion = max(emotion_distribution.items(), key=lambda item: item[1])[0]
                avg_importance = round(sum(importance_values) / len(importance_values), 4) if importance_values else 0

                return {
                    "segment_id": segment_id,
                    "atom_level": atom_level,
                    "segment_level": {
                        "segment_id": segment_id,
                        "start_time_str": _ms_to_hhmmss(segment.get("start_ms")),
                        "end_time_str": _ms_to_hhmmss(segment.get("end_ms")),
                        "duration_ms": segment.get("duration_ms") or 0,
                        "total_atoms": len(atom_level),
                        "analyzed_atoms": len(atom_level),
                        "total_entities": len(entity_distribution),
                        "total_topics": len(topic_distribution),
                        "avg_importance": avg_importance,
                        "entity_distribution": entity_distribution,
                        "topic_distribution": topic_distribution,
                        "emotion_summary": {
                            "dominant_emotion": dominant_emotion,
                            "confidence": round(
                                (emotion_distribution.get(dominant_emotion, 0) / len(atom_level)),
                                4,
                            ) if dominant_emotion and atom_level else 0,
                            "distribution": emotion_distribution,
                        } if dominant_emotion else None,
                    },
                    "narrative_level": {
                        "narrative_id": segment.get("id"),
                        "title": segment.get("title") or segment_id,
                        "summary": segment.get("summary") or "",
                        "narrative_importance": avg_importance,
                    },
                    "analysis_status": {
                        "atom_analysis": "completed",
                        "segment_analysis": "completed",
                        "narrative_analysis": "completed",
                    },
                    "analysis_stats": {
                        "total_atoms_analyzed": len(atom_level),
                        "entities_found": len(entity_distribution),
                        "topics_found": len(topic_distribution),
                        "avg_importance": avg_importance,
                    },
                }

        detail = segment_detail_service.get_segment_detail(segment_id)

        if not detail:
            raise HTTPException(
                status_code=404,
                detail=f"Segment detail not found for {segment_id}"
            )

        return detail
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting segment detail for {segment_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/projects/{project_id}/segments/summary")
async def get_segments_summary(project_id: str):
    """Get summary of all segments with analysis status"""
    try:
        summary = segment_detail_service.get_all_segments_summary()
        return {
            "segments": summary,
            "total_segments": len(summary)
        }
    except Exception as e:
        logger.error(f"Error getting segments summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/projects/{project_id}/atoms/reorder")
async def reorder_atoms_by_time(project_id: str):
    """按时间重新排序所有原子ID"""
    try:
        atoms_file = DATA_DIR / "atoms.jsonl"
        if not atoms_file.exists():
            raise HTTPException(status_code=404, detail="atoms.jsonl file not found")

        # 读取所有原子
        atoms = []
        with open(atoms_file, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    atoms.append(json.loads(line))

        # 按start_ms排序
        atoms.sort(key=lambda x: x.get('start_ms', 0))

        # 重新分配ID并添加格式化时间字段
        def ms_to_time_str(ms):
            """Convert milliseconds to HH:MM:SS format"""
            seconds = ms // 1000
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            secs = seconds % 60
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"

        reordered_atoms = []
        for i, atom in enumerate(atoms):
            new_atom = atom.copy()
            new_atom['atom_id'] = f"A{i+1:03d}"  # A001, A002, A003...

            # 添加格式化时间字段供前端显示
            start_ms = new_atom.get('start_ms', 0)
            end_ms = new_atom.get('end_ms', 0)
            duration_ms = new_atom.get('duration_ms', end_ms - start_ms)

            new_atom['start_time'] = ms_to_time_str(start_ms)
            new_atom['end_time'] = ms_to_time_str(end_ms)
            new_atom['duration_seconds'] = duration_ms / 1000.0

            reordered_atoms.append(new_atom)

        # 写回文件
        with open(atoms_file, 'w', encoding='utf-8') as f:
            for atom in reordered_atoms:
                f.write(json.dumps(atom, ensure_ascii=False) + '\n')

        # 清空相关分析数据（原子重排后必须重新分析）
        analysis_files_to_clear = [
            "entities.json",
            "topics.json",
            "knowledge_graph.json",
            "segments.pkl",
            "segments_state.json"  # 强制重新生成片段映射
        ]

        cleared_files = []
        for filename in analysis_files_to_clear:
            file_path = DATA_DIR / filename
            if file_path.exists():
                try:
                    file_path.unlink()
                    cleared_files.append(filename)
                    logger.info(f"Cleared analysis file after reorder: {filename}")
                except Exception as e:
                    logger.warning(f"Failed to clear {filename}: {e}")

        # 重新生成 overview.json 用于前端显示
        overview_data = {
            "atoms": reordered_atoms,
            "total_atoms": len(reordered_atoms),
            "total_duration_seconds": max(atom.get('end_ms', 0) for atom in reordered_atoms) // 1000 if reordered_atoms else 0
        }

        # 更新前端数据文件（两个目录都需要更新）
        frontend_paths = [
            Path("D:/code/youtube/atom-viewer/public/data/output_pipeline_v3/overview.json"),
            Path("D:/code/youtube/atom-viewer/public/data/project_001/overview.json")
        ]

        updated_files = []
        for frontend_overview_path in frontend_paths:
            try:
                frontend_overview_path.parent.mkdir(parents=True, exist_ok=True)
                with open(frontend_overview_path, 'w', encoding='utf-8') as f:
                    json.dump(overview_data, f, ensure_ascii=False, indent=2)
                updated_files.append(str(frontend_overview_path))
                logger.info(f"Updated {frontend_overview_path} with reordered atoms")
            except Exception as e:
                logger.warning(f"Failed to update {frontend_overview_path}: {e}")

        logger.info(f"Updated {len(updated_files)} frontend overview files")

        logger.info(f"Successfully reordered {len(reordered_atoms)} atoms by time")
        logger.info(f"Cleared {len(cleared_files)} analysis files: {cleared_files}")

        return {
            "status": "success",
            "message": f"Successfully reordered {len(reordered_atoms)} atoms by time",
            "reordered_atoms": len(reordered_atoms)
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reordering atoms: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/projects/{project_id}/atoms/regenerate")
async def regenerate_atoms(project_id: str):
    """重新原子化处理字幕文件"""
    try:
        import subprocess
        import sys

        # 检查是否有对应的SRT文件
        project_srt_path = Path(__file__).parent.parent / "data" / "input" / project_id / f"{project_id}.srt"

        if not project_srt_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"项目 {project_id} 的SRT文件不存在，请先上传字幕文件"
            )

        # 运行原子化处理脚本，指定输入文件
        cmd = [
            sys.executable, "-c",
            f"""
import sys
sys.path.insert(0, '{Path(__file__).parent.parent}')
from pipeline.video_processor_v3 import VideoPipelineV3, PipelineConfig

config = PipelineConfig(
    input_srt_path='{project_srt_path}',
    output_dir='{DATA_DIR}',
    enable_vectorization=False
)

pipeline = VideoPipelineV3(config)
pipeline.run()
"""
        ]

        result = subprocess.run(cmd,
                              capture_output=True,
                              text=True,
                              timeout=300)  # 5分钟超时

        if result.returncode != 0:
            logger.error(f"Atomization failed: {result.stderr}")
            raise HTTPException(status_code=500, detail=f"Atomization failed: {result.stderr}")

        # 读取处理结果
        atoms_file = DATA_DIR / "atoms.jsonl"
        if not atoms_file.exists():
            raise HTTPException(status_code=404, detail="No atoms generated")

        atoms = []
        with open(atoms_file, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    atoms.append(json.loads(line))

        # 按时间排序并重新分配ID（使用与reorder相同的逻辑）
        atoms.sort(key=lambda x: x.get('start_ms', 0))

        def ms_to_time_str(ms):
            seconds = ms // 1000
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            secs = seconds % 60
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"

        processed_atoms = []
        for i, atom in enumerate(atoms):
            new_atom = atom.copy()
            new_atom['atom_id'] = f"A{i+1:03d}"

            # 添加格式化时间字段
            start_ms = new_atom.get('start_ms', 0)
            end_ms = new_atom.get('end_ms', 0)
            duration_ms = new_atom.get('duration_ms', end_ms - start_ms)

            new_atom['start_time'] = ms_to_time_str(start_ms)
            new_atom['end_time'] = ms_to_time_str(end_ms)
            new_atom['duration_seconds'] = duration_ms / 1000.0

            processed_atoms.append(new_atom)

        # 写回后端文件
        with open(atoms_file, 'w', encoding='utf-8') as f:
            for atom in processed_atoms:
                f.write(json.dumps(atom, ensure_ascii=False) + '\n')

        # 更新前端数据
        overview_data = {
            "atoms": processed_atoms,
            "total_atoms": len(processed_atoms),
            "total_duration_seconds": max(atom.get('end_ms', 0) for atom in processed_atoms) // 1000 if processed_atoms else 0
        }

        frontend_paths = [
            Path("D:/code/youtube/atom-viewer/public/data/output_pipeline_v3/overview.json"),
            Path("D:/code/youtube/atom-viewer/public/data/project_001/overview.json")
        ]

        updated_files = []
        for frontend_overview_path in frontend_paths:
            try:
                frontend_overview_path.parent.mkdir(parents=True, exist_ok=True)
                with open(frontend_overview_path, 'w', encoding='utf-8') as f:
                    json.dump(overview_data, f, ensure_ascii=False, indent=2)
                updated_files.append(str(frontend_overview_path))
                logger.info(f"Updated {frontend_overview_path} with new atoms")
            except Exception as e:
                logger.warning(f"Failed to update {frontend_overview_path}: {e}")

        # 存储原子到Supabase
        try:
            from supabase import create_client
            import os

            supabase_url = os.getenv('SUPABASE_URL', 'http://127.0.0.1:54321')
            supabase_key = os.getenv('SUPABASE_ANON_KEY', '')
            supabase = create_client(supabase_url, supabase_key)

            # 先清除该video_id的现有原子
            supabase.table('atoms').delete().eq('video_id', project_id).execute()

            # 准备原子数据供Supabase插入
            supabase_atoms = []
            for atom in processed_atoms:
                supabase_atom = {
                    'atom_id': atom['atom_id'],
                    'video_id': project_id,
                    'sequence_number': int(atom['atom_id'][1:]),  # 从A001提取数字
                    'start_ms': atom.get('start_ms', 0),
                    'end_ms': atom.get('end_ms', 0),
                    'duration_ms': atom.get('duration_ms', 0),
                    'text': atom.get('merged_text', ''),
                    'semantic_type': atom.get('type'),
                    'keywords': atom.get('keywords', []) if atom.get('keywords') else None,
                    'summary': atom.get('summary'),
                    'confidence_score': atom.get('confidence'),
                    'processing_metadata': {
                        'processed_at': datetime.now().isoformat(),
                        'completeness': atom.get('completeness'),
                        'source_utterance_ids': atom.get('source_utterance_ids', [])
                    }
                }
                supabase_atoms.append(supabase_atom)

            # 批量插入到Supabase
            if supabase_atoms:
                result = supabase.table('atoms').insert(supabase_atoms).execute()
                logger.info(f"Successfully stored {len(supabase_atoms)} atoms to Supabase")

            # 更新视频记录的atom_count
            supabase.table('videos').update({
                'atom_count': len(supabase_atoms),
                'processing_stage': 'atomized',
                'updated_at': datetime.now().isoformat()
            }).eq('id', project_id).execute()

        except Exception as supabase_error:
            logger.warning(f"Failed to store atoms to Supabase: {supabase_error}")
            # 不要因为Supabase错误而失败整个处理流程

        logger.info(f"Successfully regenerated {len(processed_atoms)} atoms")
        return {
            "status": "success",
            "message": f"Successfully regenerated {len(processed_atoms)} atoms",
            "generated_atoms": len(processed_atoms),
            "updated_files": len(updated_files)
        }
    except subprocess.TimeoutExpired:
        logger.error("Atomization process timed out")
        raise HTTPException(status_code=500, detail="Atomization process timed out")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error regenerating atoms: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ==================== Narrative Segment Generation ====================

@app.get("/api/projects/{project_id}/generate-segments/start")
async def start_generate_segments(project_id: str):
    """
    开始生成叙事片段（基于已有原子数据）

    Returns:
        Server-Sent Events stream with progress updates
    """
    try:
        generator = NarrativeGenerator(DATA_DIR, CLAUDE_API_KEY)

        async def event_stream():
            """SSE事件流"""
            for progress in generator.generate_segments_stream():
                # 转换为SSE格式
                yield f"data: {json.dumps(progress, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error starting segment generation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/projects/{project_id}/segments/status")
async def get_segments_status(project_id: str):
    """
    获取叙事片段生成状态

    Returns:
        当前叙事片段数量和统计信息
    """
    try:
        segments_file = DATA_DIR / "narrative_segments.json"

        if not segments_file.exists():
            return {
                "status": "not_generated",
                "segment_count": 0,
                "message": "叙事片段尚未生成"
            }

        with open(segments_file, 'r', encoding='utf-8') as f:
            segments = json.load(f)

        segment_list = segments if isinstance(segments, list) else segments.get('segments', [])

        return {
            "status": "generated",
            "segment_count": len(segment_list),
            "segments": [
                {
                    "segment_id": seg.get("segment_id"),
                    "title": seg.get("title"),
                    "duration_minutes": seg.get("duration_minutes", 0),
                    "atom_count": len(seg.get("atoms", []))
                }
                for seg in segment_list
            ]
        }

    except Exception as e:
        logger.error(f"Error getting segments status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Data Preparation Endpoints ====================

@app.get("/api/projects/{project_id}/data-prep/status")
async def get_data_prep_status(project_id: str):
    """
    获取数据准备状态

    Args:
        project_id: 项目ID

    Returns:
        数据准备状态（包含详细统计）
    """
    try:
        jsonl_file = DATA_DIR / "multi_level_video.json"

        if not jsonl_file.exists():
            return {
                "ready": False,
                "file_exists": False
            }

        # 统计文件信息
        file_size = jsonl_file.stat().st_size

        # 详细统计各层级记录
        video_count = 0
        segment_count = 0
        atom_count = 0

        with open(jsonl_file, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    try:
                        record = json.loads(line)
                        level = record.get("metadata", {}).get("level", "")
                        if level == "video":
                            video_count += 1
                        elif level == "segment":
                            segment_count += 1
                        elif level == "atom":
                            atom_count += 1
                    except:
                        pass

        total_count = video_count + segment_count + atom_count

        return {
            "ready": True,
            "file_exists": True,
            "file_size": file_size,
            "record_count": total_count,
            "video_count": video_count,
            "segment_count": segment_count,
            "atom_count": atom_count
        }

    except Exception as e:
        logger.error(f"Error getting data prep status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/projects/{project_id}/data-prep/prepare")
async def prepare_vectorization_data(project_id: str):
    """
    准备向量化数据（运行 prepare_vectorization_data.py）

    Args:
        project_id: 项目ID

    Returns:
        准备结果
    """
    try:
        import subprocess
        from pathlib import Path

        # 脚本路径
        script_path = Path(__file__).parent.parent / "scripts" / "prepare_vectorization_data.py"

        if not script_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"脚本不存在: {script_path}"
            )

        # 运行脚本
        result = subprocess.run(
            ["python", str(script_path)],
            cwd=str(Path(__file__).parent.parent),
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"脚本执行失败: {result.stderr}"
            )

        # 检查生成的文件
        jsonl_file = DATA_DIR / "multi_level_video.json"

        if not jsonl_file.exists():
            raise HTTPException(
                status_code=500,
                detail="数据文件生成失败"
            )

        # 详细统计
        file_size = jsonl_file.stat().st_size
        video_count = 0
        segment_count = 0
        atom_count = 0

        with open(jsonl_file, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    try:
                        record = json.loads(line)
                        level = record.get("metadata", {}).get("level", "")
                        if level == "video":
                            video_count += 1
                        elif level == "segment":
                            segment_count += 1
                        elif level == "atom":
                            atom_count += 1
                    except:
                        pass

        total_count = video_count + segment_count + atom_count

        return {
            "success": True,
            "file_size": file_size,
            "record_count": total_count,
            "video_count": video_count,
            "segment_count": segment_count,
            "atom_count": atom_count,
            "message": "数据准备完成"
        }

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="脚本执行超时")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error preparing data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Vectorization Endpoints ====================

@app.post("/api/projects/{project_id}/vectorize")
async def vectorize_project(project_id: str, metadata: dict = None):
    """
    向量化项目知识库

    Args:
        project_id: 项目ID（视频ID）
        metadata: {"title": "视频标题", "description": "视频描述"}

    Returns:
        向量化结果（file_id等）
    """
    try:
        # 检查数据文件是否存在
        jsonl_file = DATA_DIR / "multi_level_video.json"

        if not jsonl_file.exists():
            raise HTTPException(
                status_code=404,
                detail="向量化数据文件不存在，请先生成数据文件"
            )

        # 初始化向量化服务
        vector_service = VectorizationService(DATA_DIR, OPENAI_API_KEY)

        # 获取元数据
        title = ""
        description = ""
        if metadata:
            title = metadata.get("title", "")
            description = metadata.get("description", "")

        # 上传到 OpenAI
        result = vector_service.upload_knowledge_base(
            jsonl_file,
            project_id,
            title=title,
            description=description
        )

        if not result["success"]:
            raise HTTPException(status_code=500, detail=result.get("error"))

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error vectorizing project: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/projects/{project_id}/vectorization/status")
async def get_vectorization_status(project_id: str):
    """
    获取向量化状态

    Args:
        project_id: 项目ID

    Returns:
        向量化状态信息
    """
    try:
        vector_service = VectorizationService(DATA_DIR, OPENAI_API_KEY)
        status = vector_service.get_vectorization_status(project_id)
        return status

    except Exception as e:
        logger.error(f"Error getting vectorization status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/vectorization/list")
async def list_vectorized_videos():
    """
    列出所有已向量化的视频

    Returns:
        所有已向量化视频的列表
    """
    try:
        vector_service = VectorizationService(DATA_DIR, OPENAI_API_KEY)
        metadata = vector_service.load_metadata()

        videos = []
        for video_id, info in metadata.items():
            videos.append({
                "video_id": video_id,
                "file_id": info["file_id"],
                "vector_store_id": info.get("vector_store_id", ""),
                "file_name": info.get("file_name", ""),
                "title": info.get("title", ""),
                "description": info.get("description", ""),
                "upload_time": info.get("upload_time", ""),
                "record_count": info.get("record_count", 0),
                "status": info.get("status", "unknown")
            })

        return {
            "success": True,
            "videos": videos,
            "total": len(videos)
        }

    except Exception as e:
        logger.error(f"Error listing vectorized videos: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/search")
async def search_knowledge_base(request: SearchRequest):
    """
    在知识库中搜索（支持多视频联合检索）

    Args:
        request: SearchRequest with video_ids, query, model, max_tokens

    Returns:
        搜索结果（答案和引用）
    """
    try:
        vector_service = VectorizationService(DATA_DIR, OPENAI_API_KEY)

        if not request.video_ids:
            raise HTTPException(status_code=400, detail="请指定至少一个视频ID")

        result = vector_service.search_knowledge_base(
            video_ids=request.video_ids,
            query=request.query,
            model=request.model,
            max_tokens=request.max_tokens
        )

        if not result["success"]:
            raise HTTPException(status_code=500, detail=result.get("error"))

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error searching knowledge base: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/projects/{project_id}/vectorization")
async def delete_vectorization(project_id: str):
    """
    删除向量化数据

    Args:
        project_id: 项目ID

    Returns:
        删除结果
    """
    try:
        vector_service = VectorizationService(DATA_DIR, OPENAI_API_KEY)
        result = vector_service.delete_knowledge_base(project_id)

        if not result["success"]:
            raise HTTPException(status_code=500, detail=result.get("error"))

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting vectorization: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== System Prompt Management ====================

# 默认系统提示词
DEFAULT_SYSTEM_PROMPT = """你是纪录片叙事分析师。基于文件检索回答：
1) 先检索 level=segment（top-8）定位相关片段；
2) 在这些片段内检索 level=atom 作为证据；
3) 每条结论后必须附 [ATOM_ID @ mm:ss–mm:ss]；
4) 如需限定范围（视频/时间/人物/主题/情感），请先筛选再作答；
5) 证据不足请直说并给出建议补检关键词。"""

# 系统提示词配置文件路径
SYSTEM_PROMPT_FILE = DATA_DIR / "system_prompt.txt"


@app.get("/api/system-prompt")
async def get_system_prompt():
    """
    获取当前系统提示词

    Returns:
        系统提示词内容
    """
    try:
        if SYSTEM_PROMPT_FILE.exists():
            with open(SYSTEM_PROMPT_FILE, 'r', encoding='utf-8') as f:
                prompt = f.read()
        else:
            prompt = DEFAULT_SYSTEM_PROMPT

        return {
            "success": True,
            "prompt": prompt,
            "is_default": not SYSTEM_PROMPT_FILE.exists()
        }

    except Exception as e:
        logger.error(f"Error reading system prompt: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/system-prompt")
async def update_system_prompt(data: dict):
    """
    更新系统提示词

    Args:
        data: {"prompt": "新的系统提示词"}

    Returns:
        更新结果
    """
    try:
        prompt = data.get("prompt", "").strip()

        if not prompt:
            raise HTTPException(status_code=400, detail="提示词不能为空")

        with open(SYSTEM_PROMPT_FILE, 'w', encoding='utf-8') as f:
            f.write(prompt)

        return {
            "success": True,
            "message": "系统提示词已更新"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating system prompt: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/system-prompt/reset")
async def reset_system_prompt():
    """
    重置为默认系统提示词

    Returns:
        重置结果
    """
    try:
        if SYSTEM_PROMPT_FILE.exists():
            SYSTEM_PROMPT_FILE.unlink()

        return {
            "success": True,
            "message": "已重置为默认提示词",
            "prompt": DEFAULT_SYSTEM_PROMPT
        }

    except Exception as e:
        logger.error(f"Error resetting system prompt: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== File Upload Endpoints ====================

from fastapi import File, UploadFile
import shutil

@app.post("/api/projects/{project_id}/upload-srt")
async def upload_srt_file(project_id: str, file: UploadFile = File(...)):
    """
    上传SRT文件

    Args:
        project_id: 项目ID（也是视频ID）
        file: SRT文件

    Returns:
        上传结果
    """
    try:
        # 验证文件类型
        if not file.filename.lower().endswith(('.srt', '.vtt')):
            raise HTTPException(status_code=400, detail="只支持SRT和VTT格式的字幕文件")

        # 创建存储目录
        upload_dir = Path(__file__).parent.parent / "data" / "input" / project_id
        upload_dir.mkdir(parents=True, exist_ok=True)

        # 保存文件
        file_path = upload_dir / f"{project_id}.srt"

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        logger.info(f"Successfully uploaded SRT file for project {project_id}: {file_path}")

        return {
            "success": True,
            "message": "字幕文件上传成功",
            "file_path": str(file_path),
            "file_size": file_path.stat().st_size
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading SRT file: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ==================== V2 Dual Vector Store Endpoints ====================

@app.post("/api/projects/{project_id}/v2/split-knowledge-base")
async def split_knowledge_base_v2(project_id: str):
    """
    分离混合 JSONL 为 segments.jsonl + atoms.jsonl + local_index.json

    Returns:
        分离结果统计
    """
    try:
        import subprocess
        from pathlib import Path

        # 运行分离脚本
        script_path = Path(__file__).parent.parent / "scripts" / "split_knowledge_base.py"

        if not script_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Split script not found: {script_path}"
            )

        result = subprocess.run(
            ["python", str(script_path)],
            cwd=str(Path(__file__).parent.parent),
            capture_output=True,
            text=True,
            timeout=60,
            encoding='utf-8'
        )

        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"Split script failed: {result.stderr}"
            )

        # 检查生成的文件
        split_dir = DATA_DIR / "split"
        segments_file = split_dir / "segments.jsonl"
        atoms_file = split_dir / "atoms.jsonl"
        index_file = split_dir / "local_index.json"

        if not all([segments_file.exists(), atoms_file.exists(), index_file.exists()]):
            raise HTTPException(
                status_code=500,
                detail="Split files not generated properly"
            )

        # 统计文件大小和记录数
        def count_lines(file_path):
            count = 0
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        count += 1
            return count

        segments_count = count_lines(segments_file)
        atoms_count = count_lines(atoms_file)

        with open(index_file, 'r', encoding='utf-8') as f:
            index_data = json.load(f)

        return {
            "success": True,
            "message": "Knowledge base split successfully",
            "segments": {
                "file": str(segments_file),
                "count": segments_count,
                "size_kb": segments_file.stat().st_size / 1024
            },
            "atoms": {
                "file": str(atoms_file),
                "count": atoms_count,
                "size_kb": atoms_file.stat().st_size / 1024
            },
            "index": {
                "file": str(index_file),
                "count": len(index_data),
                "size_kb": index_file.stat().st_size / 1024
            }
        }

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="Split script timed out")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error splitting knowledge base: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/projects/{project_id}/v2/vectorize-dual")
async def vectorize_dual_store(project_id: str, metadata: dict = None):
    """
    上传双 Vector Store（segments + atoms）

    Args:
        project_id: 项目ID
        metadata: {"title": "视频标题", "description": "描述"}

    Returns:
        上传结果
    """
    try:
        split_dir = DATA_DIR / "split"
        segments_file = split_dir / "segments.jsonl"
        atoms_file = split_dir / "atoms.jsonl"

        if not segments_file.exists() or not atoms_file.exists():
            raise HTTPException(
                status_code=404,
                detail="Split files not found. Please run split-knowledge-base first."
            )

        # 初始化 VectorStoreManager
        vs_manager = VectorStoreManager(DATA_DIR, OPENAI_API_KEY)

        # 获取元数据
        title = ""
        description = ""
        if metadata:
            title = metadata.get("title", "")
            description = metadata.get("description", "")

        # 上传双 Vector Store
        result = vs_manager.upload_dual_knowledge_base(
            video_id=project_id,
            segments_file=segments_file,
            atoms_file=atoms_file,
            title=title,
            description=description
        )

        if not result["success"]:
            raise HTTPException(status_code=500, detail=result.get("error"))

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading dual vector store: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/projects/{project_id}/v2/vectorization/status")
async def get_dual_vectorization_status(project_id: str):
    """
    获取双 Vector Store 状态

    Returns:
        状态信息（包含 segments 和 atoms 的 Vector Store IDs）
    """
    try:
        vs_manager = VectorStoreManager(DATA_DIR, OPENAI_API_KEY)
        status = vs_manager.get_status(project_id)
        return status

    except Exception as e:
        logger.error(f"Error getting dual vectorization status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class SearchRequestV2(BaseModel):
    video_id: str
    query: str
    model: str = "gpt-4o"
    max_tokens: int = 2000
    enable_repair: bool = True  # 是否启用自动修复


@app.post("/api/v2/search-two-phase")
async def search_two_phase(request: SearchRequestV2):
    """
    两阶段搜索（先片段，后原子）+ 自动修复流水线

    Args:
        request: SearchRequestV2

    Returns:
        搜索结果（带引用验证和修复信息）
    """
    try:
        split_dir = DATA_DIR / "split"
        index_file = split_dir / "local_index.json"

        if not index_file.exists():
            raise HTTPException(
                status_code=404,
                detail="Local index not found. Please run split-knowledge-base first."
            )

        # 初始化所有服务
        vs_manager = VectorStoreManager(DATA_DIR, OPENAI_API_KEY)
        local_index_manager = LocalIndexManager(index_file)
        two_phase_generator = TwoPhaseGenerator(
            vs_manager,
            local_index_manager,
            OPENAI_API_KEY
        )
        citation_extractor = CitationExtractor(local_index_manager)
        structure_validator = StructureValidator(citation_extractor)
        repair_orchestrator = RepairOrchestrator(
            citation_extractor,
            structure_validator,
            two_phase_generator
        )

        # 如果启用修复，使用自动修复流水线
        if request.enable_repair:
            result = repair_orchestrator.auto_repair_pipeline(
                video_id=request.video_id,
                query=request.query,
                model=request.model,
                max_repair_attempts=2
            )
        else:
            # 不启用修复，直接生成
            gen_result = two_phase_generator.generate_two_phase(
                video_id=request.video_id,
                query=request.query,
                model=request.model,
                max_tokens=request.max_tokens
            )

            if not gen_result["success"]:
                raise HTTPException(status_code=500, detail=gen_result.get("error"))

            # 验证输出
            validation = structure_validator.validate_output_structure(gen_result["final_answer"])

            result = {
                "success": True,
                "output": gen_result["final_answer"],
                "validation": validation,
                "repaired": False,
                "generation_result": gen_result
            }

        if not result["success"]:
            raise HTTPException(
                status_code=500,
                detail=result.get("error", "Search failed")
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in two-phase search: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/projects/{project_id}/v2/vectorization")
async def delete_dual_vectorization(project_id: str):
    """
    删除双 Vector Store

    Returns:
        删除结果
    """
    try:
        vs_manager = VectorStoreManager(DATA_DIR, OPENAI_API_KEY)
        result = vs_manager.delete_knowledge_base(project_id)

        if not result["success"]:
            raise HTTPException(status_code=500, detail=result.get("error"))

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting dual vectorization: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v2/vectorization/list")
async def list_dual_vectorized_videos():
    """
    列出所有已向量化的视频（双 Vector Store）

    Returns:
        视频列表
    """
    try:
        vs_manager = VectorStoreManager(DATA_DIR, OPENAI_API_KEY)
        video_ids = vs_manager.list_all_video_ids()

        videos = []
        for video_id in video_ids:
            status = vs_manager.get_status(video_id)
            if status["vectorized"]:
                videos.append(status)

        return {
            "success": True,
            "videos": videos,
            "total": len(videos)
        }

    except Exception as e:
        logger.error(f"Error listing dual vectorized videos: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Unified Agent Endpoint ====================

@app.post("/api/agent/chat")
async def unified_agent_chat(request: UnifiedAgentRequest):
    """
    统一 Agent 聊天接口

    自动判断用户意图（QA vs Cutlist），并路由到对应的生成模式

    Returns:
        - QA 模式: {"mode": "qa", "answer": str, "intent": str, ...}
        - Cutlist 模式: {"mode": "cutlist", "cutlist": dict, "intent": str, ...}
    """
    try:
        logger.info(f"Unified Agent request: video_id={request.video_id}, query={request.query}")

        # Initialize managers
        vs_manager = VectorStoreManager(DATA_DIR, OPENAI_API_KEY)

        # Check if local index exists
        index_file = DATA_DIR / "split" / "local_index.json"
        if not index_file.exists():
            raise HTTPException(
                status_code=404,
                detail="本地索引文件不存在，请先运行 split_knowledge_base.py"
            )

        index_manager = LocalIndexManager(index_file)
        intent_classifier = IntentClassifier(OPENAI_API_KEY)

        # Initialize unified agent
        unified_agent = UnifiedAgent(
            vector_store_manager=vs_manager,
            local_index_manager=index_manager,
            intent_classifier=intent_classifier,
            openai_api_key=OPENAI_API_KEY
        )

        # Execute chat
        result = unified_agent.chat(
            video_id=request.video_id,
            query=request.query,
            model=request.model,
            max_tokens=request.max_tokens,
            force_mode=request.force_mode
        )

        return result

    except Exception as e:
        logger.error(f"Error in unified agent chat: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Claude Chat Endpoint ====================

class ClaudeChatRequest(BaseModel):
    query: str
    video_id: Optional[str] = None


# 全局 Claude Chat Service 实例（延迟初始化）
_claude_chat_service = None


def get_claude_chat_service():
    """获取或初始化 Claude Chat Service"""
    global _claude_chat_service
    if _claude_chat_service is None:
        from api.claude_chat_service import ClaudeChatService
        _claude_chat_service = ClaudeChatService(DATA_DIR)
    return _claude_chat_service


@app.post("/api/claude-chat")
async def claude_chat(request: ClaudeChatRequest):
    """
    Claude Agent 对话接口（基于本地 ChromaDB）

    Args:
        request: ClaudeChatRequest with query and optional video_id

    Returns:
        Claude Agent 响应（包含工具调用信息和剪辑方案）
    """
    try:
        logger.info(f"Claude Chat request: query={request.query}, video_id={request.video_id}")

        # 获取 Claude Chat Service
        service = get_claude_chat_service()

        # 发送消息
        result = service.chat(
            query=request.query,
            video_id=request.video_id
        )

        return result

    except Exception as e:
        logger.error(f"Error in Claude chat: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/vectorize-data")
async def vectorize_data(request: ClaudeChatRequest):
    """
    手动触发数据向量化导入到 ChromaDB

    Args:
        request: ClaudeChatRequest with video_id

    Returns:
        导入状态和记录数
    """
    try:
        logger.info(f"Vectorize data request: video_id={request.video_id}")

        if not request.video_id:
            raise HTTPException(status_code=400, detail="video_id is required")

        # 获取 Claude Chat Service
        service = get_claude_chat_service()

        # 检查是否已向量化
        if service.is_vectorized(request.video_id):
            # 获取 ChromaDB 记录数
            collection_count = service.chroma_manager.collection.count()
            return {
                "success": True,
                "already_vectorized": True,
                "count": collection_count,
                "message": f"数据已向量化，共 {collection_count} 条记录"
            }

        # 读取 multi_level_video.json
        multi_level_file = service.data_dir / "multi_level_video.json"

        if not multi_level_file.exists():
            raise HTTPException(
                status_code=404,
                detail=f"找不到数据文件: {multi_level_file}"
            )

        # 导入数据到 ChromaDB
        records = []
        with open(multi_level_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    records.append(json.loads(line))

        logger.info(f"找到 {len(records)} 条记录，开始导入...")

        # 转换格式并导入
        chroma_records = []
        for record in records:
            item = {
                "atom_id": record["id"],
                "text": record["text"],
                "level": record["metadata"]["level"],
                "video_id": record["metadata"]["video_id"],
            }

            # 根据级别添加不同字段
            if record["metadata"]["level"] == "atom":
                item["start_ms"] = record["metadata"].get("start_ms", 0)
                item["end_ms"] = record["metadata"].get("end_ms", 0)
                item["duration_ms"] = record["metadata"].get("duration_ms", 0)
            elif record["metadata"]["level"] == "segment":
                item["segment_id"] = record["metadata"].get("segment_id", "")
                item["start_ms"] = record["metadata"].get("start_ms", 0)
                item["end_ms"] = record["metadata"].get("end_ms", 0)
            elif record["metadata"]["level"] == "video":
                item["total_duration_ms"] = record["metadata"].get("total_duration_ms", 0)

            chroma_records.append(item)

        # 批量添加到 ChromaDB
        service.chroma_manager.add_atoms(chroma_records, batch_size=100)
        logger.info(f"成功导入 {len(chroma_records)} 条记录到 ChromaDB")

        return {
            "success": True,
            "already_vectorized": False,
            "count": len(chroma_records),
            "message": f"成功导入 {len(chroma_records)} 条记录"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in vectorize data: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ==================== ChromaDB Status & Search Endpoints ====================

@app.get("/api/projects/{video_id}/chroma-status")
async def get_chroma_status(video_id: str):
    """
    获取 ChromaDB 向量化状态

    Args:
        video_id: 视频ID，支持 "all" 返回所有视频的聚合统计

    Returns:
        向量化状态信息
    """
    try:
        service = get_claude_chat_service()

        if video_id == "all":
            # 返回所有视频的聚合统计
            try:
                collection = service.chroma_manager.collection
                total_records = collection.count()

                # 获取所有记录的元数据以统计不同视频
                results = collection.get(limit=total_records)

                # 统计不同的video_id
                video_ids = set()
                if results and results.get('metadatas'):
                    for metadata in results['metadatas']:
                        if metadata and metadata.get('video_id'):
                            video_ids.add(metadata['video_id'])

                return {
                    "success": True,
                    "video_id": "all",
                    "vectorized": total_records > 0,
                    "total_records": total_records,
                    "video_count": len(video_ids),
                    "chroma_collection": collection.name
                }
            except Exception as e:
                logger.warning(f"ChromaDB not initialized or empty: {e}")
                return {
                    "success": True,
                    "video_id": "all",
                    "vectorized": False,
                    "total_records": 0,
                    "video_count": 0,
                    "chroma_collection": "video_multi_level"
                }
        else:
            # 返回特定视频的统计
            try:
                collection = service.chroma_manager.collection

                # 查询特定video_id的记录
                results = collection.get(
                    where={"video_id": video_id}
                )

                record_count = len(results['ids']) if results and results.get('ids') else 0

                return {
                    "success": True,
                    "video_id": video_id,
                    "vectorized": record_count > 0,
                    "total_records": record_count,
                    "chroma_collection": collection.name
                }
            except Exception as e:
                logger.warning(f"ChromaDB error for video {video_id}: {e}")
                return {
                    "success": True,
                    "video_id": video_id,
                    "vectorized": False,
                    "total_records": 0,
                    "chroma_collection": "video_multi_level",
                    "error": str(e)
                }

    except Exception as e:
        logger.error(f"Error getting ChromaDB status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class SearchAtomsRequest(BaseModel):
    query: str
    k: int = 10
    level: Optional[str] = None


@app.post("/api/projects/{video_id}/search-atoms")
async def search_atoms(video_id: str, request: SearchAtomsRequest):
    """
    在 ChromaDB 中搜索原子

    Args:
        video_id: 视频ID
        request: 搜索请求（query, k, level）

    Returns:
        搜索结果
    """
    try:
        service = get_claude_chat_service()

        # 构建过滤条件
        where_filter = {"video_id": video_id}
        if request.level:
            where_filter["level"] = request.level

        # 执行搜索
        results = service.chroma_manager.search(
            query=request.query,
            k=request.k,
            where=where_filter if where_filter else None
        )

        # 转换结果格式
        search_results = []
        if results:
            for i, (doc_id, text, metadata, distance) in enumerate(zip(
                results.get('ids', [[]])[0],
                results.get('documents', [[]])[0],
                results.get('metadatas', [[]])[0],
                results.get('distances', [[]])[0]
            )):
                # 将距离转换为相似度分数 (0-1)
                similarity = 1 / (1 + distance)

                search_results.append({
                    "atom_id": doc_id,
                    "text": text,
                    "score": similarity,
                    "level": metadata.get("level", ""),
                    "start_ms": metadata.get("start_ms"),
                    "end_ms": metadata.get("end_ms")
                })

        return {
            "success": True,
            "results": search_results,
            "count": len(search_results)
        }

    except Exception as e:
        logger.error(f"Error searching atoms: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/chroma/sample-data")
async def get_sample_data(video_id: str = "all", limit: int = 5):
    """
    获取 ChromaDB 中的随机样本数据

    Args:
        video_id: 视频ID，"all" 表示所有视频
        limit: 返回样本数量（默认5条）

    Returns:
        样本数据列表
    """
    try:
        service = get_claude_chat_service()
        collection = service.chroma_manager.collection

        # 获取样本
        if video_id == "all":
            # 获取所有数据的样本
            results = collection.get(limit=limit)
        else:
            # 获取特定视频的样本
            results = collection.get(
                where={"video_id": video_id},
                limit=limit
            )

        # 转换为前端格式
        samples = []
        if results and results.get('ids'):
            for i, doc_id in enumerate(results['ids']):
                text = results['documents'][i] if i < len(results['documents']) else ""
                metadata = results['metadatas'][i] if i < len(results['metadatas']) else {}

                sample = {
                    "id": doc_id,
                    "text": text[:100] + "..." if len(text) > 100 else text,
                    "full_text": text,
                    "level": metadata.get("level", ""),
                    "video_id": metadata.get("video_id", ""),
                    "start_ms": metadata.get("start_ms"),
                    "end_ms": metadata.get("end_ms")
                }
                samples.append(sample)

        return {
            "success": True,
            "samples": samples,
            "count": len(samples)
        }

    except Exception as e:
        logger.error(f"Error getting sample data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/projects")
async def get_projects():
    """
    获取所有项目列表

    Returns:
        项目列表（目前返回空列表，可根据实际需求扩展）
    """
    try:
        # 这里可以根据实际情况从数据库或文件系统读取项目列表
        # 目前返回示例数据
        projects = [
            {
                "project_id": "project_001",
                "project_name": "示例项目 1",
                "video_count": 2,
                "created_at": "2024-01-01"
            }
        ]

        return {
            "success": True,
            "projects": projects,
            "count": len(projects)
        }

    except Exception as e:
        logger.error(f"Error getting projects: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/videos/chroma")
async def get_videos_chroma():
    """
    获取所有视频列表

    Returns:
        视频列表
    """
    try:
        # 从 ChromaDB 中提取所有唯一的 video_id
        service = get_claude_chat_service()
        collection = service.chroma_manager.collection

        try:
            # 获取所有记录
            results = collection.get()

            # 提取唯一的 video_id 和相关信息
            video_dict = {}
            if results and results.get('metadatas'):
                for metadata in results['metadatas']:
                    if metadata and metadata.get('video_id'):
                        video_id = metadata['video_id']
                        if video_id not in video_dict:
                            video_dict[video_id] = {
                                "video_id": video_id,
                                "video_title": f"视频 {video_id}",
                                "atom_count": 0,
                                "project_id": metadata.get('project_id', 'default')
                            }
                        video_dict[video_id]["atom_count"] += 1

            videos = list(video_dict.values())

        except Exception as e:
            logger.warning(f"ChromaDB not initialized: {e}")
            videos = []

        return {
            "success": True,
            "videos": videos,
            "count": len(videos)
        }

    except Exception as e:
        logger.error(f"Error getting videos: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Run Server ====================

if __name__ == "__main__":
    import uvicorn
    logger.info("Starting Video Understanding API server...")
    logger.info(f"Data directory: {DATA_DIR}")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )
