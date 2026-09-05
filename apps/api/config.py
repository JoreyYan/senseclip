"""
配置文件
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 项目根目录
BASE_DIR = Path(__file__).parent

# 加载.env文件
load_dotenv(BASE_DIR / '.env')

# API密钥（从环境变量读取）
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Supabase 配置
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
AUDIO_BUCKET = "audio"

# Groq API
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# xAI Grok API
XAI_API_KEY = os.getenv("XAI_API_KEY", "")

# DeepSeek API
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

# SiliconFlow API (embedding provider, replaces OpenAI when set)
SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY", "")

# Embedding provider selection: SiliconFlow bge-m3 (1024d) preferred,
# fallback OpenAI text-embedding-3-small (1536d).
if SILICONFLOW_API_KEY:
    EMBEDDING_PROVIDER = "siliconflow"
    EMBEDDING_BASE_URL = "https://api.siliconflow.cn/v1"
    EMBEDDING_API_KEY = SILICONFLOW_API_KEY
    EMBEDDING_MODEL = "BAAI/bge-m3"
    EMBEDDING_DIM = 1024
else:
    EMBEDDING_PROVIDER = "openai"
    EMBEDDING_BASE_URL = None
    EMBEDDING_API_KEY = OPENAI_API_KEY
    EMBEDDING_MODEL = "text-embedding-3-small"
    EMBEDDING_DIM = 1536

# Tavily Search API (for web enrichment)
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# twitterapi.io (X/Twitter 推文抓取)
TWITTERAPI_KEY = os.getenv("TWITTERAPI_KEY", "")

# 数据目录
DATA_DIR = BASE_DIR / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"
OUTPUT_DATA_DIR = DATA_DIR / "output"

# 提示词目录
PROMPTS_DIR = BASE_DIR / "prompts"

# 日志配置
LOG_DIR = BASE_DIR / "logs"
LOG_LEVEL = "INFO"

# 缓存目录
CACHE_DIR = BASE_DIR / ".cache"

# 确保目录存在
for dir_path in [DATA_DIR, RAW_DATA_DIR, PROCESSED_DATA_DIR,
                 OUTPUT_DATA_DIR, PROMPTS_DIR, LOG_DIR, CACHE_DIR]:
    dir_path.mkdir(parents=True, exist_ok=True)
