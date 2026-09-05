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

## 人格的四层材料

1. **原子库**:频道视频 / 推文经转录、原子化、向量化后的内容(由引擎自动产出)
2. **观点库 + 金句库**:`POST /api/admin/persona/build {"persona": "<key>"}` 从原子中抽取结构化立场与代表性原话(表 `persona_viewpoints` / `persona_quotes`)
3. **思维框架**:`tools/distill/distill_framework.py` 蒸馏出的世界观/决策模式/表达风格文档,写入 `app_settings.<framework_key>`
4. **实时语气样本**:对话与圆桌会自动注入该人格最近的推文原文(仅 X 频道)

## 提示词模板

`apps/api/prompts/persona/<lang>/first_person.txt`、`third_person.txt`、`person_network_line.txt`;`PROMPT_LANG=zh|en` 切换。占位符:`{label}` `{desc}` `{source_word}` `{pn_line}`。

## 使用规范

人格是基于公开内容的 AI 模拟。请在界面明确标注;不要用于冒充、欺骗或诽谤;为真实人物建立人格前评估授权与所在地法律。
