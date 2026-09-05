# SenseClip

把任意创作者的公开内容(YouTube 视频、X 推文)变成**可对话、可溯源引用、第一人称**的 AI 人格,并让多个人格同台圆桌对谈。

Turn any creator's public content (YouTube, X) into a first-person, citation-grounded AI persona — and put several personas on stage for a live roundtable. *(English summary below.)*

在线实例:https://mailuo.vercel.app · 许可证:Apache-2.0

## 能做什么

- **内容摄入**:YouTube 频道每 20 分钟自动巡航 → Whisper 转录 → 语义原子化 → 实体 / 向量化;X 推文定时抓取
- **人格构建**(无需训练):从全部原子抽取结构化观点库 + 金句库,蒸馏思维框架;人格只是一个 YAML
- **人格对话**:agentic 检索(观点库 / 原文 / 人物图谱 / 联网),以本人第一人称回答,每个 `[N]` 都能点回原视频片段
- **广场圆桌**:多个人格逐轮交锋,流式输出、可无限续聊、观众可插话
- **弹性模型链**:DeepSeek 主力 → Claude 兜底(审查拒答 / 软拒绝 / 过载 / 断流 / 余额耗尽全部自动切换)
- **可选计费**:积分制 + Stripe 订阅,一个开关 `BILLING_ENABLED`

## 快速开始

```bash
git clone https://github.com/JoreyYan/senseclip && cd senseclip
cp .env.example .env            # 填 Supabase URL / keys + 至少一个模型 key
# 数据库:在 Supabase SQL Editor 执行 supabase/migrations/00000000000000_baseline.sql
#        (或本地 `supabase start`,会自动应用)
docker compose up --build       # api :8000  engine :8001  web :5173
```

不用 Docker 也行:`apps/api` 用 `uvicorn api.server:app`,`apps/web` 用 `npm run dev`。详见 [docs/deploy.md](docs/deploy.md)。

## 新增一个博主

1. 在 `personas/` 加一个 YAML(照抄 `lu.yaml`)
2. 让引擎拉频道:`POST /api/admin/backfill/start`
3. 内容入库后构建观点库:`POST /api/admin/persona/build`
4. 蒸馏思维框架:`python tools/distill/distill_framework.py`

前端模式按钮和圆桌嘉宾自动出现。详见 [docs/personas.md](docs/personas.md)。

## 仓库结构

```
apps/api       FastAPI 服务(SERVICE_MODE=api 产品服务 / engine 处理引擎,同一镜像)
apps/web       Vite + React 前端
supabase/      数据库基线迁移(Postgres + pgvector)
personas/      人格 YAML(示例:鲁社长、孙割)
tools/         思维框架蒸馏等离线工具
docs/          部署、人格配置
```

## 使用规范

本项目生成的人格是**基于公开内容的 AI 模拟,不代表本人**。部署者须在界面明确标注 AI 模拟身份,不得用于冒充、欺骗或诽谤;为真实人物构建人格前请评估授权与当地法律。仓库不包含任何创作者的转录内容或衍生数据。

## English

SenseClip ingests a creator's public videos/tweets (auto-crawl → Whisper → semantic "atoms" → embeddings), distills a **viewpoint library, quote library and thinking framework** without any training, and serves a first-person persona agent whose every claim is linked back to the source clip. Multiple personas can debate a topic in a streaming, resumable roundtable. A resilient LLM chain (DeepSeek → Claude) handles censorship refusals, soft refusals, overload and broken streams automatically. Billing (credits + Stripe) is optional. See `docs/` for deployment; personas are plain YAML files under `personas/`.

Personas are AI simulations built from public content and must be labeled as such; do not use them to impersonate, deceive or defame.

## 贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。

Apache-2.0 © 2026 Jorey Yan and contributors
