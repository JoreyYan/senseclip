# SenseClip

把任意创作者的公开内容(YouTube 视频、X 推文)变成**可对话、可引用溯源、第一人称**的 AI 人格,并让多个人格同台圆桌对谈。

> ⚠️ 本项目正在从私有代码库迁移为开源项目,文档与一键部署仍在完善中(见 `docs/`)。

## 能做什么

- **内容摄入**:YouTube 频道自动巡航 → 转录(Whisper)→ 语义原子化 → 实体/向量化;X 推文定时抓取
- **人格构建**:从全部原子中抽取结构化观点库 + 金句库,蒸馏思维框架(无需训练)
- **人格对话**:agentic 检索(观点库 / 原文 / 人物图谱 / 联网),第一人称回答,每句可点回原视频
- **广场圆桌**:多个人格就一个话题逐轮交锋,流式输出、可续聊、观众可插话
- **弹性模型链**:DeepSeek 主力 → Claude 兜底(审查拒答 / 软拒绝 / 过载 / 断流自动切换)
- **可选计费**:积分制 + Stripe 订阅(`BILLING_ENABLED` 开关)

## 仓库结构

```
apps/api      FastAPI 服务(SERVICE_MODE=api 产品服务 / engine 处理引擎)
apps/web      Vite + React 前端
supabase      数据库迁移(Postgres + pgvector)
personas      人格配置示例
tools         蒸馏等离线工具
docs          部署与开发文档
```

## 快速开始(草稿)

1. 复制 `.env.example` 为 `.env` 并填写 Supabase 与模型密钥
2. 应用 `supabase/migrations`
3. 后端:`cd apps/api && pip install -r requirements.txt -r requirements_supabase.txt && uvicorn api.server:app --reload`
4. 前端:`cd apps/web && npm i && npm run dev`

## 使用规范

本项目生成的人格是**基于公开内容的 AI 模拟,不代表本人**。部署者需在界面明确标注 AI 模拟身份,不得用于冒充、欺骗或诽谤;为真实人物构建人格前请评估授权与当地法律要求。

## License

Apache-2.0 © 2026 Jorey Yan and contributors
