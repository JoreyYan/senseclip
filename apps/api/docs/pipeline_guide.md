# 视频处理Pipeline使用指南

## 概述

视频处理Pipeline是一个标准化的模块化架构，自动处理长视频的所有步骤：
- ✅ 自动切分（长视频）
- ✅ 批量处理（缓存）
- ✅ 断点续传（容错）
- ✅ 时间重叠修复（后处理）
- ✅ 质量验证（评估）

## 快速开始

```python
from pipeline import VideoProcessor, PipelineConfig
from config import CLAUDE_API_KEY

# 配置Pipeline
config = PipelineConfig(
    input_srt_path="data/raw/test.srt",
    auto_segment=True,              # 自动切分
    segment_duration_minutes=10,    # 10分钟/片段
    segment_threshold_minutes=15,   # >15分钟才切分
    batch_size=50,
    use_cache=True,
    use_checkpoint=True,
    fix_overlap=True
)

# 创建处理器并执行
processor = VideoProcessor(CLAUDE_API_KEY, config)
result = processor.process()
```

## Pipeline工作流程

```
输入SRT文件
  ↓
[1] 解析和清洗字幕
  ↓
[2] 判断是否需要切分
  ├─ 短视频 (≤15min) → 整体处理
  └─ 长视频 (>15min) → 切分处理
       ↓
       [2.1] 切分成10分钟片段
       [2.2] 逐个片段处理
         ├─ 原子化 (使用缓存)
         ├─ 断点续传 (失败恢复)
         ├─ 修复重叠
         └─ 保存片段结果
       [2.3] 合并所有片段
  ↓
[3] 全局质量验证
  ↓
[4] 保存最终结果
  ├─ atoms.jsonl (原子列表)
  ├─ validation.json (质量报告)
  ├─ stats.json (统计信息)
  ├─ frontend_data.json (前端数据)
  └─ segments/ (各片段结果)
```

## 配置选项详解

### PipelineConfig 参数

#### 输入配置
```python
input_srt_path: str = "data/raw/test.srt"
# SRT字幕文件路径
```

#### 切分配置
```python
auto_segment: bool = True
# 是否启用自动切分
# True: 长视频自动切分成多个片段
# False: 不管多长都整体处理

segment_duration_minutes: int = 10
# 每个片段的时长（分钟）
# 推荐值：10-15分钟
# 太小：片段过多，管理复杂
# 太大：单片段失败影响大

segment_threshold_minutes: int = 15
# 触发切分的阈值（分钟）
# 视频时长 > threshold 才会切分
# 推荐值：15-30分钟
```

**切分决策示例**：
- 5分钟视频：不切分（<15分钟）
- 20分钟视频：切分成2个片段（>15分钟）
- 2小时视频：切分成12个片段（>15分钟）

#### 原子化配置
```python
batch_size: int = 50
# 每批处理的字幕数量
# 50: 平衡速度和质量（推荐）
# 100: 更快但质量稍差
# 25: 更慢但质量更好

prompt_version: str = 'v1'
# 使用的prompt版本
# v1: 标准版本
# v2: 实验版本（可能更严格）

use_cache: bool = True
# 是否使用API响应缓存
# True: 相同内容不重复调用API（强烈推荐）
# False: 每次都调用API

use_checkpoint: bool = True
# 是否使用断点续传
# True: 失败后可从断点恢复（推荐）
# False: 失败后需要从头开始
```

#### 后处理配置
```python
fix_overlap: bool = True
# 是否修复时间重叠
# True: 修复LLM产生的时间重叠（推荐）
# False: 保持原始时间

overlap_strategy: str = 'proportional_split'
# 重叠修复策略
# 'proportional_split': 按比例分割重叠区域（推荐，更公平）
# 'adjust_boundary': 只调整后一个原子（保守）
```

#### 输出配置
```python
output_dir: str = "data/output"
# 输出目录

save_segments: bool = True
# 是否保存各片段的单独结果
# True: 保存到 output_dir/segments/
# False: 只保存最终合并结果

save_frontend_data: bool = True
# 是否生成前端数据文件
# True: 生成 frontend_data.json
# False: 只保存原始数据
```

## 使用场景

### 场景1：处理短视频（10-30分钟）
```python
config = PipelineConfig(
    auto_segment=False,  # 不切分
    use_cache=True,
    use_checkpoint=False  # 短视频不需要断点
)
```

### 场景2：处理长视频（1-2小时）
```python
config = PipelineConfig(
    auto_segment=True,
    segment_duration_minutes=10,
    use_cache=True,
    use_checkpoint=True  # 必须开启断点续传
)
```

### 场景3：测试/开发（只处理前N分钟）
```python
config = PipelineConfig(...)
processor = VideoProcessor(CLAUDE_API_KEY, config)

# 只处理前30分钟
result = processor.process(time_limit_ms=30*60*1000)
```

### 场景4：重新处理（清除缓存）
```python
# 1. 删除缓存目录
rm -rf data/cache/

# 2. 运行处理
config = PipelineConfig(use_cache=True)  # 会重新生成缓存
processor = VideoProcessor(CLAUDE_API_KEY, config)
result = processor.process()
```

### 场景5：失败恢复
```python
# 如果处理失败，直接重新运行
# Pipeline会自动：
# 1. 使用已有的缓存（跳过已处理的batch）
# 2. 从断点恢复（跳过已完成的片段）
processor = VideoProcessor(CLAUDE_API_KEY, config)
result = processor.process()  # 从失败点继续
```

## 输出文件说明

### 整体处理模式
```
data/output/
  ├── atoms.jsonl              # 原子列表（JSONL格式）
  ├── validation.json          # 质量验证报告
  ├── stats.json               # API统计信息
  └── frontend_data.json       # 前端显示数据
```

### 切分处理模式
```
data/output/
  ├── atoms.jsonl              # 完整原子列表
  ├── validation.json          # 全局质量报告
  ├── stats.json               # 全局统计
  ├── frontend_data.json       # 前端数据
  └── segments/                # 各片段结果
      ├── segment_001.json     # 片段1 (00:00-10:00)
      ├── segment_002.json     # 片段2 (10:00-20:00)
      └── ...
```

## 性能和成本

### 时间估算
| 视频时长 | 切分模式 | 预计时间（无缓存） | 预计时间（有缓存） |
|---------|---------|-------------------|-------------------|
| 10分钟  | 整体     | 1-2分钟           | <1秒              |
| 30分钟  | 切分(3)  | 4-5分钟           | <3秒              |
| 60分钟  | 切分(6)  | 8-10分钟          | <5秒              |
| 120分钟 | 切分(12) | 15-20分钟         | <10秒             |

### 成本估算（batch_size=50）
| 视频时长 | 字幕数量 | API调用次数 | 预估成本 |
|---------|---------|-----------|---------|
| 10分钟  | ~300    | ~6        | ~$0.13  |
| 30分钟  | ~900    | ~18       | ~$0.38  |
| 60分钟  | ~1800   | ~36       | ~$0.76  |
| 120分钟 | ~3600   | ~72       | ~$1.52  |

## 最佳实践

1. **始终开启缓存** (`use_cache=True`)
   - 避免重复调用API
   - 大幅降低成本

2. **长视频开启断点** (`use_checkpoint=True`)
   - 防止失败需要重头开始
   - 特别是>1小时的视频

3. **合理设置切分粒度**
   - 推荐10-15分钟/片段
   - 太小片段过多，太大失败影响大

4. **修复时间重叠** (`fix_overlap=True`)
   - 提升质量评分
   - 使用 `proportional_split` 策略

5. **保存片段结果** (`save_segments=True`)
   - 便于查看进度
   - 失败时可以定位问题片段

## 故障排查

### 问题1：处理很慢
**原因**：缓存未生效
**解决**：检查 `use_cache=True`，查看日志是否显示 `[缓存命中]`

### 问题2：失败后重新开始
**原因**：断点未开启
**解决**：设置 `use_checkpoint=True`

### 问题3：质量评分低
**原因**：时间重叠问题
**解决**：确保 `fix_overlap=True` 和 `overlap_strategy='proportional_split'`

### 问题4：某个片段失败
**操作**：
1. 查看失败片段的checkpoint ID
2. 直接重新运行，会自动跳过已完成的片段
3. 或者删除该片段的checkpoint文件，单独重试

## 示例：完整使用流程

```python
# 1. 导入
from pipeline import VideoProcessor, PipelineConfig
from config import CLAUDE_API_KEY

# 2. 配置
config = PipelineConfig(
    input_srt_path="data/raw/my_video.srt",
    auto_segment=True,
    segment_duration_minutes=10,
    segment_threshold_minutes=15,
    batch_size=50,
    use_cache=True,
    use_checkpoint=True,
    fix_overlap=True,
    overlap_strategy='proportional_split',
    output_dir="data/output/my_video"
)

# 3. 创建处理器
processor = VideoProcessor(CLAUDE_API_KEY, config)

# 4. 执行处理
result = processor.process()

# 5. 查看结果
print(f"处理完成！")
print(f"原子数：{len(result['atoms'])}")
print(f"质量评分：{result['report']['quality_score']}")
print(f"成本：{result['stats']['total_cost_formatted']}")
```

## 后续步骤

处理完成后，可以：
1. 将 `frontend_data.json` 复制到前端项目
2. 查看各片段结果（`segments/` 目录）
3. 分析质量报告（`validation.json`）
4. 继续下一阶段的处理（主题标注、情感分析等）
