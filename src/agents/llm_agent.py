"""简单的LLM Agent - 基于OpenAI客户端直接调用，支持 DeepSeek thinking 模式"""

import os
import time
from openai import OpenAI


class LLMAgent:
    """封装OpenAI客户端的简单Agent，内置重试逻辑

    use_thinking=True: 使用推理模型 (deepseek-v4-pro) + thinking enabled
    use_thinking=False: 使用普通对话模型 (deepseek-chat)
    """

    def __init__(self, role_name: str, system_prompt: str, use_thinking: bool = False):
        self.role_name = role_name
        self.system_prompt = system_prompt
        self.use_thinking = use_thinking

        api_key = os.getenv("API_KEY")
        base_url = os.getenv("BASE_URL")

        if use_thinking:
            self.model = os.getenv("MODEL_ID_THINKING", "deepseek-v4-pro")
        else:
            self.model = os.getenv("MODEL_ID", "deepseek-chat")

        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self.max_retries = 3
        self.retry_delay = 5  # 秒

    async def step(self, user_message: str) -> str:
        """发送消息并返回文本响应"""
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._call, user_message)

    def _call(self, user_message: str) -> str:
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message},
        ]

        kwargs = dict(
            model=self.model,
            messages=messages,
            temperature=0.7,
            max_tokens=4096,
        )

        if self.use_thinking:
            kwargs["extra_body"] = {
                "thinking": {"type": "enabled"},
                "reasoning_effort": "high",
            }
            # thinking 模式下 max_tokens 需要更大，推理内容不占用输出
            kwargs["max_tokens"] = 8192

        last_error = None
        for attempt in range(self.max_retries):
            try:
                response = self._client.chat.completions.create(**kwargs)
                if response and response.choices and response.choices[0].message.content:
                    return response.choices[0].message.content
                print(f"  [!] API返回空，重试 {attempt+1}/{self.max_retries}...")
                time.sleep(self.retry_delay * (attempt + 1))
            except Exception as e:
                last_error = e
                print(f"  [!] API异常: {e}，重试 {attempt+1}/{self.max_retries}...")
                time.sleep(self.retry_delay * (attempt + 1))

        raise RuntimeError(f"API调用失败（重试{self.max_retries}次）: {last_error}")
