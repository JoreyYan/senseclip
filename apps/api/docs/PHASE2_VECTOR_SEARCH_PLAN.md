# Phase 2: 向量化与语义搜索系统 - 实施规划

## 📋 总体目标

为视频语义理解系统添加向量化和语义搜索能力，使用户能够：
- 通过自然语言搜索相关内容片段
- 基于语义相似度查找相关原子
- 多维度过滤和检索（时间、主题、人物等）

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Phase 2: 向量搜索层                        │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌───────────────┐      ┌──────────────┐     ┌──────────┐   │
│  │ Embedding     │──────│ Vector DB    │─────│ Search   │   │
│  │ Generator     │      │ (Qdrant)     │     │ Engine   │   │
│  └───────────────┘      └──────────────┘     └──────────┘   │
│         │                      │                    │        │
│         │                      │                    │        │
│         ▼                      ▼                    ▼        │
│  [Text→Vector]          [Storage+Index]      [Query+Rank]   │
│                                                               │
└─────────────────────────────────────────────────────────────┘
                            ▲
                            │
                ┌───────────┴──────────┐
                │                      │
         ┌──────▼──────┐      ┌───────▼────────┐
         │   Atoms     │      │   Segments     │
         │  (Level 1)  │      │   (Level 2)    │
         └─────────────┘      └────────────────┘
```

## 📦 模块划分

### Module 1: 向量嵌入生成器 (EmbeddingGenerator)

**目标**: 将文本转换为高维向量表示

**文件**: `video_understanding_engine/embedders/embedding_generator.py`

**功能**:
- 支持多种 embedding 服务（OpenAI, Voyage AI）
- 批量处理（减少API调用）
- 缓存机制（避免重复计算）
- 成本估算

**API 设计**:
```python
class EmbeddingGenerator:
    def __init__(self, provider: str, api_key: str, model: str)
    def generate_embedding(self, text: str) -> List[float]
    def generate_batch(self, texts: List[str]) -> List[List[float]]
    def get_dimension(self) -> int
    def get_stats(self) -> dict
```

**支持的模型**:
- OpenAI: `text-embedding-3-small` (1536维, $0.02/1M tokens)
- OpenAI: `text-embedding-3-large` (3072维, $0.13/1M tokens)
- Voyage AI: `voyage-2` (1024维, $0.12/1M tokens)

**测试策略**:
1. 单文本 embedding 生成
2. 批量文本处理
3. 向量维度验证
4. 成本估算验证

---

### Module 2: 向量数据库集成 (VectorStore)

**目标**: 存储和索引向量数据

**文件**: `video_understanding_engine/vectorstores/qdrant_store.py`

**技术选型**: Qdrant
- 轻量级，易部署（Docker或本地模式）
- 高性能（Rust实现）
- 支持过滤和混合搜索
- 免费开源

**数据结构设计**:

**Collection 1: Atoms (原子级)**
```python
{
    "id": "A001",
    "vector": [0.123, -0.456, ...],  # 1536维
    "payload": {
        "atom_id": "A001",
        "text": "合并后的文本",
        "type": "叙述历史",
        "start_ms": 7000,
        "end_ms": 45000,
        "duration_seconds": 38.0,
        "completeness": "完整",
        "video_id": "test_video",
        "video_title": "金三角历史"
    }
}
```

**Collection 2: Segments (片段级)**
```python
{
    "id": "SEG_001",
    "vector": [0.789, -0.234, ...],
    "payload": {
        "segment_id": "SEG_001",
        "title": "片段标题",
        "summary": "片段摘要",
        "full_text": "完整文本",
        "start_ms": 7000,
        "end_ms": 599000,
        "duration_minutes": 9.9,
        "primary_topic": "主要话题",
        "free_tags": ["tag1", "tag2", ...],
        "persons": ["人物1", "人物2"],
        "events": ["事件1", "事件2"],
        "importance_score": 0.85,
        "quality_score": 0.82,
        "video_id": "test_video"
    }
}
```

**API 设计**:
```python
class QdrantVectorStore:
    def __init__(self, url: str, collection_name: str)
    def create_collection(self, dimension: int, distance: str)
    def insert_atoms(self, atoms: List[Atom], embeddings: List[List[float]])
    def insert_segments(self, segments: List[NarrativeSegment], embeddings: List[List[float]])
    def search(self, query_vector: List[float], limit: int, filters: dict) -> List[dict]
    def delete_by_video_id(self, video_id: str)
    def get_stats(self) -> dict
```

**测试策略**:
1. 本地 Qdrant 启动测试
2. Collection 创建和配置
3. 数据插入验证
4. 基础搜索功能

---

### Module 3: 语义搜索引擎 (SemanticSearchEngine)

**目标**: 提供高级搜索接口

**文件**: `video_understanding_engine/searchers/semantic_search.py`

**功能**:
- 自然语言查询
- 多级搜索（原子级 + 片段级）
- 多维度过滤
- 结果排序和重排

**搜索模式**:

1. **纯向量搜索** (Semantic Search)
   - 输入: 自然语言查询
   - 输出: Top-K 最相似的原子/片段

2. **混合搜索** (Hybrid Search)
   - 向量搜索 + 关键词匹配
   - 结合语义理解和精确匹配

3. **过滤搜索** (Filtered Search)
   - 按时间范围
   - 按主题/标签
   - 按人物/事件
   - 按重要性/质量评分

**API 设计**:
```python
class SemanticSearchEngine:
    def __init__(self, vector_store: QdrantVectorStore, embedder: EmbeddingGenerator)

    # 搜索原子
    def search_atoms(
        self,
        query: str,
        limit: int = 10,
        filters: dict = None
    ) -> List[dict]

    # 搜索片段
    def search_segments(
        self,
        query: str,
        limit: int = 5,
        filters: dict = None
    ) -> List[dict]

    # 查找相似原子
    def find_similar_atoms(
        self,
        atom_id: str,
        limit: int = 10
    ) -> List[dict]

    # 混合搜索
    def hybrid_search(
        self,
        query: str,
        keywords: List[str],
        limit: int = 10
    ) -> List[dict]
```

**测试策略**:
1. 单一查询搜索
2. 多条件过滤搜索
3. 相似度排序验证
4. 搜索性能测试

---

### Module 4: Pipeline 集成

**目标**: 将向量化集成到现有 Pipeline

**文件**: `video_understanding_engine/pipeline/video_processor_v3.py`

**新增步骤**:
```
原有流程:
  字幕 → 原子化 → 片段识别 → 深度分析 → 输出

新流程:
  字幕 → 原子化 → 片段识别 → 深度分析 → [向量化] → [存储到向量库] → 输出
```

**配置新增**:
```python
class PipelineConfig:
    # 向量化配置
    enable_vectorization: bool = True
    embedding_provider: str = 'openai'  # 'openai' or 'voyage'
    embedding_model: str = 'text-embedding-3-small'

    # 向量存储配置
    vector_store_url: str = 'http://localhost:6333'
    vector_collection_atoms: str = 'atoms'
    vector_collection_segments: str = 'segments'
```

---

## 🗓️ 实施计划

### Step 1: 环境准备（15分钟）
- [ ] 安装 Qdrant（Docker 或 pip）
- [ ] 安装依赖库（qdrant-client, openai）
- [ ] 配置 OpenAI API Key

### Step 2: Module 1 - Embedding Generator（30分钟）
- [ ] 创建 `embedders/` 目录
- [ ] 实现 `EmbeddingGenerator` 类
- [ ] 编写单元测试 `test_embedding_generator.py`
- [ ] 测试通过 ✓

### Step 3: Module 2 - Vector Store（45分钟）
- [ ] 创建 `vectorstores/` 目录
- [ ] 实现 `QdrantVectorStore` 类
- [ ] 编写测试脚本 `test_vector_store.py`
- [ ] 使用 10 分钟样本数据测试插入和查询
- [ ] 测试通过 ✓

### Step 4: Module 3 - Search Engine（45分钟）
- [ ] 创建 `searchers/` 目录
- [ ] 实现 `SemanticSearchEngine` 类
- [ ] 编写测试脚本 `test_semantic_search.py`
- [ ] 测试各种搜索场景
- [ ] 测试通过 ✓

### Step 5: Pipeline 集成（30分钟）
- [ ] 创建 `VideoProcessorV3`
- [ ] 集成向量化步骤
- [ ] 端到端测试
- [ ] 测试通过 ✓

### Step 6: 前端集成（后续）
- [ ] 在 atom-viewer 中添加搜索界面
- [ ] 实现搜索结果展示
- [ ] 高亮匹配内容

---

## 💰 成本估算

### Embedding 成本（基于 OpenAI text-embedding-3-small）

**10分钟视频样本**:
- 37个原子，平均每个70字 → 2,590字 → ~1,900 tokens
- 1个片段，2,710字 → ~2,000 tokens
- 总计: ~3,900 tokens
- 成本: $0.02 / 1M tokens × 3,900 = **$0.000078**

**2小时完整视频估算**:
- 约 450 个原子 → ~23,000 tokens
- 约 30-50 个片段 → ~60,000 tokens
- 总计: ~83,000 tokens
- 成本: $0.02 / 1M tokens × 83,000 = **$0.0017** (~0.01元)

### 存储成本

**Qdrant 本地部署**: 免费
**Qdrant Cloud**:
- Free tier: 1GB存储（足够存储约10万条向量）
- 对于个人项目完全够用

---

## 🎯 成功标准

### 功能完整性
- [x] Embedding 生成正常
- [ ] Vector Store 增删查改正常
- [ ] 搜索结果相关性高（人工评估）

### 性能指标
- [ ] 10分钟视频向量化 < 5秒
- [ ] 单次搜索响应 < 200ms
- [ ] Top-10 搜索准确率 > 80%

### 质量指标
- [ ] 所有单元测试通过
- [ ] 端到端测试通过
- [ ] 代码有完整注释

---

## 📚 参考资料

- [Qdrant Documentation](https://qdrant.tech/documentation/)
- [OpenAI Embeddings Guide](https://platform.openai.com/docs/guides/embeddings)
- [Vector Search Best Practices](https://www.pinecone.io/learn/vector-search/)

---

## 📝 实施记录

### 2025-10-01
- 创建 Phase 2 规划文档
- 准备开始实施

---

**下一步**: 开始实施 Step 1 - 环境准备
