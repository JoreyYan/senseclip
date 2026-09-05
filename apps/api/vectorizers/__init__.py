from utils.api_client import OpenAIClient

# bge-m3 上下文 8192 token；中文按 ~1.5 字/token 保守截断
_MAX_CHARS = 6000
# SiliconFlow 单请求 batch 上限保护
_MAX_BATCH = 32


class OpenAIVectorizer:
    """OpenAI 兼容 embedding 客户端封装（OpenAI / 硅基流动 bge-m3 通用）"""

    def __init__(self, api_key: str, model: str = "text-embedding-3-small", base_url: str = None):
        self.client = OpenAIClient(api_key, base_url=base_url)
        self.model = model

    def vectorize_text(self, text: str) -> list:
        return self.client.generate_embedding(text[:_MAX_CHARS], model=self.model)

    def vectorize_batch(self, texts: list) -> list:
        clipped = [t[:_MAX_CHARS] for t in texts]
        results: list = []
        for i in range(0, len(clipped), _MAX_BATCH):
            chunk = clipped[i:i + _MAX_BATCH]
            results.extend(self.client.generate_embeddings_batch(chunk, model=self.model))
        return results


def create_vectorizer() -> OpenAIVectorizer:
    """根据 config 的 EMBEDDING_* 选择 provider（硅基流动优先）"""
    from config import EMBEDDING_API_KEY, EMBEDDING_MODEL, EMBEDDING_BASE_URL
    return OpenAIVectorizer(EMBEDDING_API_KEY, model=EMBEDDING_MODEL, base_url=EMBEDDING_BASE_URL)
