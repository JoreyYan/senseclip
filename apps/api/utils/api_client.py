"""
API客户端封装
"""

import anthropic
import openai
import time
from typing import Optional, Dict, Any
from anthropic import APIError, RateLimitError


class ClaudeClient:
    """Claude API客户端（带重试机制）"""

    def __init__(self, api_key: str):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.total_calls = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def call(
        self,
        prompt: str,
        # 说明：部分账号/地区可能不支持带日期后缀的模型名，默认用稳定别名。
        model: str = "claude-haiku-4-5",
        max_tokens: int = 4000,
        max_retries: int = 3
    ) -> str:
        """
        调用Claude API（带重试）

        Args:
            prompt: 提示词
            model: 模型名称
            max_tokens: 最大输出token数
            max_retries: 最大重试次数

        Returns:
            API返回的文本

        Raises:
            Exception: 重试次数用尽后抛出
        """
        for attempt in range(max_retries):
            try:
                response = self.client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    timeout=20*60,  # 20分钟超时
                    messages=[{
                        "role": "user",
                        "content": prompt
                    }]
                )

                # 统计
                self.total_calls += 1
                self.total_input_tokens += response.usage.input_tokens
                self.total_output_tokens += response.usage.output_tokens

                return response.content[0].text

            except RateLimitError:
                # 限流：指数退避
                wait_time = 2 ** attempt
                print(f"WARNING Rate limit hit, waiting {wait_time}s before retry...")
                time.sleep(wait_time)

            except APIError as e:
                # API错误：重试
                print(f"WARNING API error: {e}")
                if attempt == max_retries - 1:
                    raise
                time.sleep(1)

        raise Exception("重试次数用尽")

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        # 价格（Haiku 4.5 估算）
        input_price_per_m = 1.00  # $1/M tokens
        output_price_per_m = 5.00  # $5/M tokens

        input_cost = (self.total_input_tokens / 1_000_000) * input_price_per_m
        output_cost = (self.total_output_tokens / 1_000_000) * output_price_per_m
        total_cost = input_cost + output_cost

        return {
            "total_calls": self.total_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "estimated_cost": f"${total_cost:.2f}"
        }


# DeepSeek 的两种内容拒绝:硬拒绝(400 Content Exists Risk)和软拒绝
# (HTTP 200 但正文是一句拒绝话术)。两种都要落到 Claude 兜底。
_DS_REFUSAL_MARKS = ("无法处理", "无法回答", "还没有学会", "不能协助",
                     "换一个话题", "无法提供")


def _ds_soft_refused(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    return len(t) < 150 and any(m in t for m in _DS_REFUSAL_MARKS)


class DeepSeekClient:
    """DeepSeek API 客户端（OpenAI 兼容格式）"""

    def __init__(self, api_key: str, fallback_on_balance: bool = False):
        self.client = openai.OpenAI(api_key=api_key, base_url="https://api.deepseek.com/v1")
        # 余额耗尽时是否直接换 Claude(小批量任务开启;视频流水线保持 False,
        # 让 backfill 熔断暂停而不是静默烧 Anthropic 账单)
        self.fallback_on_balance = fallback_on_balance
        self.total_calls = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def call(
        self,
        prompt: str,
        model: str = "deepseek-v4-flash",
        max_tokens: int = 4000,
        max_retries: int = 3
    ) -> str:
        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=model,
                    max_tokens=max_tokens,
                    timeout=20 * 60,
                    messages=[{"role": "user", "content": prompt}],
                )
                self.total_calls += 1
                if response.usage:
                    self.total_input_tokens += response.usage.prompt_tokens or 0
                    self.total_output_tokens += response.usage.completion_tokens or 0
                content = response.choices[0].message.content or ""
                if _ds_soft_refused(content):
                    # 软拒绝:HTTP 200 但正文是拒绝话术 → 同样换 Claude
                    print("WARNING DeepSeek soft refusal, falling back to Claude")
                    return self._claude_fallback(prompt, max_tokens)
                return content
            except Exception as e:
                msg = str(e)
                # 内容风控拒绝是确定性失败(敏感内容),重试无用 → 换 Claude 处理这一段
                if "Content Exists Risk" in msg:
                    print("WARNING DeepSeek content-risk rejection, falling back to Claude")
                    return self._claude_fallback(prompt, max_tokens)
                if self.fallback_on_balance and ("Insufficient Balance" in msg or "402" in msg):
                    print("WARNING DeepSeek insufficient balance, falling back to Claude")
                    return self._claude_fallback(prompt, max_tokens)
                print(f"WARNING DeepSeek API error (attempt {attempt+1}): {e}")
                if attempt == max_retries - 1:
                    raise
                time.sleep(2 ** attempt)
        raise Exception("DeepSeek API 重试次数用尽")

    def _claude_fallback(self, prompt: str, max_tokens: int) -> str:
        import os
        api_key = os.getenv("CLAUDE_API_KEY", "")
        if not api_key:
            raise Exception("DeepSeek 内容风控拒绝,且无 CLAUDE_API_KEY 可兜底")
        return ClaudeClient(api_key).call(
            prompt, model="claude-haiku-4-5", max_tokens=max_tokens)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_calls": self.total_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
        }


class GrokClient:
    """xAI Grok API 客户端（OpenAI 兼容格式）"""

    def __init__(self, api_key: str):
        self.client = openai.OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
        self.total_calls = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def call(
        self,
        prompt: str,
        model: str = "grok-4.3",
        max_tokens: int = 4000,
        max_retries: int = 3
    ) -> str:
        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=model,
                    max_tokens=max_tokens,
                    timeout=20 * 60,
                    messages=[{"role": "user", "content": prompt}],
                )
                self.total_calls += 1
                if response.usage:
                    self.total_input_tokens += response.usage.prompt_tokens or 0
                    self.total_output_tokens += response.usage.completion_tokens or 0
                return response.choices[0].message.content or ""
            except Exception as e:
                print(f"WARNING Grok API error (attempt {attempt+1}): {e}")
                if attempt == max_retries - 1:
                    raise
                time.sleep(2 ** attempt)
        raise Exception("Grok API 重试次数用尽")

    def get_stats(self) -> Dict[str, Any]:
        input_cost = (self.total_input_tokens / 1_000_000) * 0.20
        output_cost = (self.total_output_tokens / 1_000_000) * 0.50
        return {
            "total_calls": self.total_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "estimated_cost": f"${input_cost + output_cost:.4f}",
        }


class OpenAIClient:
    """OpenAI API客户端"""

    def __init__(self, api_key: str, base_url: str = None):
        # base_url 支持 OpenAI 兼容的 embedding 服务（如硅基流动）
        if base_url:
            self.client = openai.OpenAI(api_key=api_key, base_url=base_url)
        else:
            self.client = openai.OpenAI(api_key=api_key)

    def generate_embedding(
        self,
        text: str,
        model: str = "text-embedding-3-small"
    ) -> list:
        """生成单个文本的embedding"""
        response = self.client.embeddings.create(
            model=model,
            input=[text]
        )
        return response.data[0].embedding

    def generate_embeddings_batch(
        self,
        texts: list,
        model: str = "text-embedding-3-small"
    ) -> list:
        """批量生成embedding"""
        response = self.client.embeddings.create(
            model=model,
            input=texts
        )
        return [item.embedding for item in response.data]
