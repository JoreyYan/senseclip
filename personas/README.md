# personas/

每个 `*.yaml` 定义一个人格(博主分身)。服务启动时自动加载,与内置定义、数据库动态注册(`POST /api/admin/persona/register`)合并,同 key 时:数据库 > YAML > 内置。

新增一个博主的完整流程:
1. 在这里新建 `<key>.yaml`(照抄示例,改 label/channels/avatar)
2. 让引擎拉取其频道:`POST /api/admin/backfill/start {"channel_url": "https://www.youtube.com/@xxx"}`;X 账号:`POST /api/admin/xpoller/start {"username": "xxx"}`
3. 内容入库后构建观点库:`POST /api/admin/persona/build {"persona": "<key>"}`
4. 蒸馏思维框架:`python tools/distill/distill_framework.py`(写入 app_settings.<framework_key>)
5. 前端模式按钮与广场嘉宾会自动出现该人格

> 注意:人格是基于公开内容的 AI 模拟,请在界面明确标注;为真实人物建立人格前请评估授权与法律风险。
