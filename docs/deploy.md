# 部署指南

SenseClip 由三部分组成:

| 组件 | 说明 | 推荐托管 |
|---|---|---|
| `apps/api`(SERVICE_MODE=api) | 产品服务:问答、人格、圆桌、计费、用户 | Railway / Fly / 任意容器平台 |
| `apps/api`(SERVICE_MODE=engine) | 处理引擎:频道巡航、转录、原子化、人格构建、推文抓取 | 同上(独立实例,重启不影响用户请求) |
| `apps/web` | Vite + React 前端 | Vercel / Netlify / 静态托管 |
| Supabase | Postgres + pgvector、Auth、Storage | Supabase 云 或 `supabase start` 本地 |

## 1. 数据库(Supabase)

1. 新建 Supabase 项目(任意区域;国内用户建议新加坡/东京)。
2. SQL Editor 执行 `supabase/migrations/00000000000000_baseline.sql`(幂等,可重复执行)。
3. 记下:Project URL、anon key、service_role key。

本地替代:`supabase start`(需 Docker),会自动应用 `supabase/migrations/`,`supabase status` 查看本地 URL 与密钥。

## 2. 环境变量

复制 `.env.example` → `.env`。最少需要:

```
SUPABASE_URL, SUPABASE_KEY(service_role), SUPABASE_ANON_KEY, ADMIN_KEY
GROQ_API_KEY            # 视频转录(Whisper)
DEEPSEEK_API_KEY 或 CLAUDE_API_KEY   # 至少一个;两个都填可获得自动兜底
SILICONFLOW_API_KEY 或 OPENAI_API_KEY # 向量化
```

可选:`TAVILY_API_KEY`(联网搜索)、`TWITTERAPI_KEY`(X 推文)、`STRIPE_*` + `BILLING_ENABLED=true`(计费)。

产品参数(均可省略):`GUEST_DAILY_LIMIT`、`CONSULT_COST`、`ROUNDTABLE_COST`、`ROUNDTABLE_ROUND_COST`、`SIGNUP_BONUS_CREDITS`、`BACKFILL_CONCURRENCY`、`BACKFILL_RESCAN_MINUTES`、`BACKFILL_MAX_SECONDS`、`PROMPT_LANG`(zh/en)。

## 3. 后端(两个实例)

镜像由根目录 `Dockerfile` 构建(含 ffmpeg / yt-dlp / Deno)。

- **api 实例**:`SERVICE_MODE=api`,`ENGINE_URL=<engine 实例地址>`,`FRONTEND_URL=<前端地址>`
- **engine 实例**:`SERVICE_MODE=engine`

Railway 示例:两个 Service 指向同一仓库,分别设置上述变量;`railway up` 部署(或连接 GitHub 自动部署)。

启动引擎巡航(一次性,状态持久化在数据库,重启自动恢复):

```bash
curl -X POST $ENGINE_URL/api/admin/backfill/start -H "X-Admin-Key: $ADMIN_KEY" \
     -H "Content-Type: application/json" -d '{"channel_url":"https://www.youtube.com/@xxx"}'
```

YouTube 在云主机 IP 上通常需要有效 cookies:在前端 `/settings` 上传浏览器导出的 `cookies.txt`(会存入 `app_settings.youtube_cookies`)。cookies 会被 YouTube 轮换,过期表现为巡航扫描返回 0 个视频,重新上传即可。

## 4. 前端

Vercel:导入仓库,Root Directory 设为 `apps/web`,环境变量:

```
VITE_API_URL=https://<api 实例域名>
VITE_SUPABASE_ANON_KEY=<anon key>
VITE_SUPABASE_DIRECT_URL=https://<project>.supabase.co
```

`apps/web/vercel.json` 里的 `/sb-proxy/*` 反代把 Supabase 请求走同源(解决部分地区无法直连 supabase.co 的问题),把 destination 改成你自己的项目地址。

## 5. 本地一键(开发)

```bash
supabase start                      # 数据库 + Auth + Storage
cp .env.example .env                # 填本地 supabase status 输出的 URL / keys
docker compose up --build           # api :8000  engine :8001  web :5173
```

## 6. 新增一个博主

见 `personas/README.md`:加一个 YAML → 拉频道 → 建观点库 → 蒸馏框架,前端自动出现新模式与圆桌嘉宾。

## 7. 运维要点

- 引擎重启会中断进行中的视频处理,但转录有缓存、进度按视频粒度自动重试;正在生成的问答/圆桌会被标记为"服务已更新",用户重试即可
- DeepSeek 余额耗尽时引擎自动暂停 30 分钟再试;问答自动切换 Claude
- `/api/admin/apply-sql` 默认关闭(`ENABLE_ADMIN_SQL=true` 才开),仅供自有实例排障
