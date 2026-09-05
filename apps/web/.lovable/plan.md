
# 群英图 — 第一阶段实现计划

## 视觉风格
暗色简约风格，深色背景 `#0f172a`（slate-900），卡片 `#1e293b`（slate-800），干净克制，类似 Obsidian Graph View。节点按 category 着色（红/金/绿/蓝/紫/灰），边按关系类型着色。

## 图形库
**Cytoscape.js** — 专业图分析库，力导向布局性能好，支持500+节点流畅交互，样式系统完善。

## 第一阶段功能范围

### 1. 全局图视图（主页面）
- Cytoscape.js 力导向图，全屏画布
- 节点大小按 mention_count 映射，颜色按 category 区分
- 边粗细按 strength 映射，颜色/样式按 relation_type 区分（家族绿色、上下级红色箭头、冲突红色虚线等）
- video_count ≥ 3 的节点加发光边框
- 鼠标交互：缩放、平移、拖拽节点、hover 高亮关联节点并淡化其他
- 点击节点进入 Ego-Network 并打开详情面板

### 2. 顶部工具栏
- 搜索框：输入人名快速定位居中
- 筛选：按 category 和 relation_type 筛选
- 图例：颜色/线型说明
- 刷新按钮

### 3. Ego-Network 视图
- 点击人物后，以该人为中心展示1-2跳关联
- 调用 `/api/person-relations/{person_id}` 获取有向关系
- 中心节点放大高亮，边上显示关系描述
- 「← 返回全局图」按钮

### 4. 右侧详情面板（抽屉式）
- 人物名片：名字、类别徽章、提及次数、视频数
- AI 简介（调用 API 3）
- 职业履历时间线，按时间排序
- 权力关系列表，按 confidence 排序
- 提问框：输入问题调用 API 10，显示 AI 回答

### 5. 顶部 Stats 概览卡片
- 视频数、人物数、有向关系数、AI增强状态

### 后续阶段预留
- 管理功能（重建、合并、重命名）
- AI 重建进度条
- 按视频来源筛选

## 项目结构
- `src/services/api.ts` — API 封装层
- `src/components/graph/` — Cytoscape 图组件
- `src/components/panels/` — 详情面板、Stats 卡片
- `src/components/toolbar/` — 搜索、筛选、图例
- `src/types/` — TypeScript 类型定义
- `src/constants/` — 关系类型/类别映射和颜色配置
