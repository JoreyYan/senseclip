# SenseClip Backend API Reference

**Base URL**: `https://video-understanding-api-production.up.railway.app`

---

## 1. Health & Root

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | 根端点，返回版本信息 |
| GET | `/api/health` | 健康检查 |

---

## 2. 视频摄入（Ingestion）

| Method | Endpoint | Description | Body |
|--------|----------|-------------|------|
| POST | `/api/ingest` | 提交 YouTube URL 开始处理 | `{ youtube_url: string }` |
| POST | `/api/ingest/upload` | 直接上传音频文件（跳过 yt-dlp） | multipart/form-data |
| POST | `/api/ingest/reprocess/{video_id}` | 强制重新处理：清除原子/片段/实体，从 SRT 重新原子化 | — |
| GET | `/api/ingest/{job_id}` | 查询摄入任务状态 | — |

---

## 3. 视频库（Library）

| Method | Endpoint | Description | Body |
|--------|----------|-------------|------|
| GET | `/api/library` | 列出所有视频资产 + 最近任务状态 | — |
| POST | `/api/library/retry` | 重试失败的视频处理 | `{ youtube_url: string }` |
| POST | `/api/library/backfill-status` | 回填历史任务状态字段 | `{ youtube_url?: string, limit?: int }` |

---

## 4. 人物网络（Person Network）

| Method | Endpoint | Description | Params/Body |
|--------|----------|-------------|-------------|
| GET | `/api/person-network` | 获取缓存的跨视频人物网络 | — |
| POST | `/api/person-network/rebuild` | 重建人物网络 | `?limit=200&use_ai=false&ai_model=claude-sonnet-4-6` |
| GET | `/api/person-network/rebuild/status/{job_id}` | 查询 AI 重建进度 | — |
| GET | `/api/person-network/person/{person_id}` | 获取人物卡片（简介 + 关系 + 原子） | `?use_ai=true&ai_model=claude-haiku-4-5` |
| GET | `/api/person-network/aliases` | 获取手动别名映射 | — |
| POST | `/api/person-network/merge` | 合并两个人物并重建缓存 | `{ source_name: string, target_name: string, use_ai?: bool }` |
| PATCH | `/api/person-network/person/{person_id}/rename` | 重命名人物并刷新网络缓存 | `{ new_name: string }` |
| POST | `/api/person-network/person/{person_id}/ask` | 回答关于某人物的问题（基于原子证据 + 关系 + 履历） | `{ question: string, ai_model?: string, include_web?: bool }` |
| POST | `/api/person-network/person/{person_id}/enrich-web` | 通过 Tavily 获取人物公开资料 | — |

---

## 5. 人物关系（Person Relations）

| Method | Endpoint | Description | Params/Body |
|--------|----------|-------------|-------------|
| POST | `/api/person-relations/extract` | 跨视频提取人物关系 | `{ video_ids: string[], batch_size?: int, model?: string }` |
| GET | `/api/person-relations/graph` | 获取关系图 | `?limit=40&min_confidence=0.5` |
| GET | `/api/person-relations/stats` | 获取关系统计 | `?video_ids=id1,id2` |
| GET | `/api/person-relations/{person_id}` | 获取某人的关系子图 | `?person_name=xxx&hops=2` |

---

## 6. 人物同步 & 补充

| Method | Endpoint | Description | Body |
|--------|----------|-------------|------|
| POST | `/api/persons/sync` | 跨视频同步人物 | `{ video_ids?: string[] }` |
| POST | `/api/persons/enrich-web-batch` | 批量从网络补充人物信息 | `{ person_names: string[], model?: string }` |

---

## 7. 原子搜索 & 管理（Atoms）

| Method | Endpoint | Description | Body |
|--------|----------|-------------|------|
| POST | `/api/atoms/search` | 搜索原子 | `{ query: string, limit?: int, video_ids?: string[] }` |
| PATCH | `/api/atoms/{atom_id}/text` | 更新原子文本 | `{ text: string }` |

---

## 8. 别名系统（Aliases）

| Method | Endpoint | Description | Body |
|--------|----------|-------------|------|
| GET | `/api/aliases` | 获取 entity_aliases_v1 映射 | — |
| POST | `/api/aliases` | 添加/更新别名 | `{ alias: string, canonical: string, type?: string }` |
| DELETE | `/api/aliases/{alias}` | 删除别名 | — |
| DELETE | `/api/person-profiles/cache` | 清除人物简介缓存 | — |

---

## 9. 对话 & RAG（Chat）

| Method | Endpoint | Description | Body |
|--------|----------|-------------|------|
| POST | `/api/chat` | 多轮 RAG 对话（向量检索原子 → Claude 生成） | `{ messages: [{role, content}], video_ids?: string[] }` |
| POST | `/api/agent/chat` | 统一 Agent 对话（意图分类：问答 or 剪辑） | `{ video_id: string, query: string, model?: string, force_mode?: 'qa'\|'cutlist' }` |
| POST | `/api/claude-chat` | Claude 对话（基于本地 ChromaDB） | `{ query: string, video_id?: string }` |

---

## 10. 视频 & 项目分析（Analysis）

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/videos` | 列出所有视频 |
| GET | `/api/videos/{video_id}/analysis` | 获取视频分析数据 |
| GET | `/api/projects` | 获取所有项目 |
| GET | `/api/projects/{project_id}/atoms` | 获取项目原子 |
| GET | `/api/projects/{project_id}/segments` | 获取片段及状态 |
| GET | `/api/projects/{project_id}/entities` | 获取实体 |
| GET | `/api/projects/{project_id}/topics` | 获取主题 |
| GET | `/api/projects/{project_id}/graph` | 获取知识图谱 |
| GET | `/api/projects/{project_id}/overview` | 获取项目概览 |

---

## 11. 分析工作流（Analysis Workflows）

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/projects/{project_id}/analyze/full` | 启动全量分析 |
| GET | `/api/projects/{project_id}/analyze/status` | 查询分析状态 |
| POST | `/api/projects/{project_id}/analyze/cancel` | 取消分析 |
| POST | `/api/projects/{project_id}/analyze/incremental/start` | 启动增量分析 |
| POST | `/api/projects/{project_id}/analyze/incremental/stop` | 停止增量分析 |
| GET | `/api/projects/{project_id}/analyze/incremental/progress` | 查询增量分析进度 |
| POST | `/api/projects/{project_id}/analyze/incremental/reset` | 重置分析（保留原子化） |
| POST | `/api/projects/{project_id}/segments/recreate` | 用新时长重建片段 |

---

## 12. 向量化（Vectorization）

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/projects/{project_id}/data-prep/prepare` | 准备向量化数据 |
| POST | `/api/projects/{project_id}/vectorize` | 执行向量化 |
| GET | `/api/projects/{project_id}/vectorization/status` | 查询向量化状态 |
| DELETE | `/api/projects/{project_id}/vectorization` | 删除向量化 |
| POST | `/api/search` | 跨项目搜索 |

---

## 13. 设置（Settings）

| Method | Endpoint | Description | Body |
|--------|----------|-------------|------|
| POST | `/api/settings/cookies` | 上传 YouTube cookies.txt | multipart/form-data |
| GET | `/api/settings/cookies` | 获取 cookies 上传状态 | — |
| GET | `/api/system-prompt` | 获取当前系统提示词 | — |
| POST | `/api/system-prompt` | 更新系统提示词 | `{ prompt: string }` |
| POST | `/api/system-prompt/reset` | 重置为默认系统提示词 | — |

---

## 关键技术细节

- **向量搜索**: 使用 OpenAI `text-embedding-3-small` 模型，相似度阈值 0.25
- **Supabase RPC**: `match_atoms_v1` 用于向量相似度搜索
- **AI 模型**: Claude Sonnet（重建）、Claude Haiku（人物详情/关系提取）、GPT-4o（搜索/Agent）
- **别名系统**: 双存储模型 `person_network_aliases_v1` + `entity_aliases_v1`
- **增量分析**: 基于片段的处理，支持进度追踪和部分失败恢复
