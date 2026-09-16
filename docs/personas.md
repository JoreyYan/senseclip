# 人格(Persona)配置

人格由 `personas/<key>.yaml` 定义,服务启动加载(10 分钟缓存;也可 `POST /api/admin/persona/register` 动态注册,优先级更高)。

| 字段 | 必填 | 说明 |
|---|---|---|
| `key` | 是 | 英文标识,前端/接口用它引用(缺省取文件名) |
| `label` | 是 | 显示名(模式按钮、圆桌标签) |
| `desc` | 建议 | 一句话身份描述,进入系统提示词 |
| `channels` | 是 | 内容归属:`videos.channel` 取值列表。YouTube 频道用 handle(如 `zrzjpl`),X 账号用 `x_<username>` |
| `avatar` | 否 | 头像路径(放 `apps/web/public/`),默认 `/avatar.png` |
| `first_person` | 否 | `true`(默认)= 以本人第一人称回答;`false` = 第三人称分析模式 |
| `has_person_network` | 否 | 是否启用人物图谱工具(需先构建群英图) |
| `source_word` | 否 | 提示词里对其内容的称呼,如"视频"、"视频和推文" |
| `framework_key` | 否 | 思维框架文本在 `app_settings` 的 key,默认 `<key>_framework_v1` |
| `kb_desc` | 否 | 知识库检索工具的描述(给模型看) |
| `hidden` | 否 | `true` = 暂不对外(不出现在前端和圆桌),资料准备好再公开;管理员可用 `preview_key` 预览 |
| `autopilot` | 否 | `true` = 交给引擎自动驾驶:增量建观点库 → 蒸馏框架 → 达标后自动公开(见下文) |

## 人格的四层材料

1. **原子库**:频道视频 / 推文经转录、原子化、向量化后的内容(由引擎自动产出)
2. **观点库 + 金句库**:`POST /api/admin/persona/build {"persona": "<key>"}` 从原子中抽取结构化立场与代表性原话(表 `persona_viewpoints` / `persona_quotes`)
3. **思维框架**:`tools/distill/distill_framework.py` 蒸馏出的世界观/决策模式/表达风格文档,写入 `app_settings.<framework_key>`
4. **实时语气样本**:对话与圆桌会自动注入该人格最近的推文原文(仅 X 频道)

## 自动驾驶(推荐)

新增博主最省事的方式:YAML 里写 `hidden: true` + `autopilot: true`,把频道加进巡航,剩下的交给引擎。
引擎每 30 分钟检查一次每个自动驾驶人格:

1. **观点库**:频道有新处理完的视频 → 增量抽取观点与金句(按视频记录进度,频道持续更新也不会漏)
2. **思维框架**:处理完的视频 ≥ `PERSONA_MIN_VIDEOS_DISTILL`(默认 15),且比上次蒸馏增长一半以上 → 服务端重新蒸馏(`api/persona_distill.py`,逐视频提取可增量缓存)
3. **公开**:有框架、观点 ≥ `PERSONA_MIN_VIEWPOINTS`(默认 120)、视频覆盖达到计划量的 `PERSONA_PUBLISH_COVERAGE`(默认 0.5)→ 写入 `app_settings.persona_visibility_v1`,前端自动出现;提名榜里对应频道同步标为「已上线」

查看进度:`GET /api/admin/persona/autopilot`。手动提前公开/下线:把 `persona_visibility_v1` 里该 key 设为 `"public"` / `"hidden"`。

## 频道巡航的按频道配置

`app_settings.backfill_worker_v1` 的 `channels` 列表里,每个频道除 `url`、`name` 外还支持:

| 字段 | 说明 |
|---|---|
| `max_seconds` | 该频道的时长上限,覆盖全局 `BACKFILL_MAX_SECONDS`(如长直播为主的财经频道设 `10800`);`0` 不限 |
| `filter` | 自然语言收录标准。引擎扫描时把标题交给 DeepSeek 判断,只处理符合的视频;判定结果缓存在 `app_settings.backfill_topic_filter_v1`,每个视频只判一次,新视频自动判 |

示例:只收一个综合频道里的金融财经内容

```json
{"url": "https://www.youtube.com/@somechannel", "name": "somechannel", "max_seconds": 10800,
 "filter": "只收录以金融、经济、财经为主题的内容……不收录玄学、生活、政治八卦……多话题拼盘只看第一个话题"}
```

下载失败会自动分类:会员专享视频 7 天后再试(换有会员权限的 cookies 后立即恢复),预约直播按退避重试,其余失败从 20 分钟起逐次翻倍、最长一天一次。

## 提示词模板

`apps/api/prompts/persona/<lang>/first_person.txt`、`third_person.txt`、`person_network_line.txt`;`PROMPT_LANG=zh|en` 切换。占位符:`{label}` `{desc}` `{source_word}` `{pn_line}`。

## 使用规范

人格是基于公开内容的 AI 模拟。请在界面明确标注;不要用于冒充、欺骗或诽谤;为真实人物建立人格前评估授权与所在地法律。
