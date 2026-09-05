# supabase/

- `migrations/00000000000000_baseline.sql` — **从零建库的基线**(2026-09-05 从生产库导出并整理):32 张表、外键、索引(含 pgvector HNSW)、RPC 函数(`match_atoms_v1` / `match_viewpoints` / `match_quotes` / `get_video_stats`)、updated_at 触发器、RLS 策略、Storage buckets(`audio` / `subtitles`)。幂等,可重复执行。
- `migrations_legacy/` — 历史增量迁移,仅作考古参考,**不要**在新库上执行(与基线重复)。
- `config.toml` — Supabase CLI 本地栈配置。

## 建库方式

**云端 Supabase 项目**:在 SQL Editor 里执行基线文件即可(需要 pgvector 扩展,Supabase 默认可用)。

**本地开发**(需要 Docker):
```bash
supabase start        # 拉起本地 Postgres + Auth + Storage + PostgREST,并自动应用 migrations/
supabase status       # 查看本地 URL / anon key / service_role key,填入 .env
```

## 以后怎么加迁移

新增/修改表结构时在 `migrations/` 下新建带时间戳的文件(`supabase migration new <name>`),不要再改基线。
