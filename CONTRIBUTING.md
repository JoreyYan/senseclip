# 参与贡献

感谢你的兴趣!SenseClip 采用 Apache-2.0 许可,欢迎 issue 与 PR。

## 本地开发

```bash
# 1) 数据库 / Auth / Storage(需要 Docker + Supabase CLI)
supabase start          # 首次会拉取镜像;结束后 `supabase status` 查看本地 URL 与密钥
# 2) 环境变量
cp .env.example .env    # 填入 supabase status 输出的 URL / anon / service_role,以及至少一个模型 key
# 3) 后端
cd apps/api && pip install -r requirements.txt -r requirements_supabase.txt
SERVICE_MODE=api uvicorn api.server:app --reload --port 8000
# 4) 前端
cd apps/web && npm install && npm run dev
```

或者 `docker compose up --build`(见 `docs/deploy.md`)。

## 代码结构速览

- `apps/api/api/server.py` — FastAPI 入口(问答、人格、圆桌、计费、管理端点;引擎 worker 亦由此启动)
- `apps/api/api/backfill_worker.py` — 频道巡航与视频处理调度
- `apps/api/api/persona_builder.py` — 观点库 / 金句库构建
- `apps/api/api/x_poller.py` — X 推文抓取
- `apps/api/utils/api_client.py` — DeepSeek → Claude 弹性调用链(审查 / 软拒绝 / 余额 / 过载)
- `apps/api/supabase_pipeline.py` + `atomizers/ structurers/ vectorizers/` — 转录后的原子化 → 片段 → 实体 → 向量流水线
- `apps/api/prompts/` — 提示词模板(按语言分目录)
- `apps/web/src/pages/` — ChatPage(对话)、SquarePage(广场)、BillingPage 等
- `personas/` — 人格 YAML;`supabase/migrations/` — 数据库基线

## 提交约定

- 分支从 `main` 切出,PR 描述清楚动机与验证方式
- 后端改动请至少通过 `python -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())" apps/api/api/server.py` 与本地启动;前端请通过 `npm run build`
- 涉及数据库结构:在 `supabase/migrations/` 新增带时间戳的迁移文件,不要修改基线
- 不要提交任何密钥、cookies、转录内容或衍生数据

## 内容与合规

本项目生成的人格是对公开内容的 AI 模拟。提交示例人格或演示内容时请确保来源公开、标注清楚,并尊重相关人物与平台的权利。
