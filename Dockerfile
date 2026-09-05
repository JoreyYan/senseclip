FROM python:3.11-slim

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# 安装 Deno（yt-dlp n-challenge JS solver 需要）
RUN curl -fsSL https://deno.land/install.sh | sh
ENV DENO_INSTALL="/root/.deno"
ENV PATH="${DENO_INSTALL}/bin:${PATH}"

# 安装 yt-dlp（最新版）
RUN curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp \
    && chmod a+rx /usr/local/bin/yt-dlp

WORKDIR /app

# 先复制并安装依赖（利用 Docker layer 缓存）
COPY apps/api/requirements.txt ./requirements.txt
COPY apps/api/requirements_supabase.txt ./requirements_supabase.txt
RUN pip install --no-cache-dir -r requirements.txt -r requirements_supabase.txt

# 复制项目代码
COPY apps/api/ ./apps/api/
COPY railway.toml ./
COPY personas/ ./personas/

WORKDIR /app/apps/api

EXPOSE $PORT

CMD ["sh", "-c", "uvicorn api.server:app --host 0.0.0.0 --port ${PORT:-8000}"]
