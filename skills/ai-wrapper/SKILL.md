---
name: ai-wrapper
description: Простая обёртка для вызова LLM (OpenRouter, Groq, Google) из Telegram-бота на Python.
---

# AI Wrapper

Единый класс для вызова LLM. Поддерживает несколько провайдеров, fallback, кэширование и ограничение токенов.

## Установка зависимостей

```bash
pip install httpx python-dotenv
Код (src/services/llm.py)
python
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

class LLMService:
    def __init__(self):
        self.providers = {
            "openrouter": {
                "url": "https://openrouter.ai/api/v1/chat/completions",
                "key": os.getenv("OPENROUTER_API_KEY"),
                "model": "deepseek/deepseek-v4-flash"  # бесплатная
            },
            "groq": {
                "url": "https://api.groq.com/openai/v1/chat/completions",
                "key": os.getenv("GROQ_API_KEY"),
                "model": "llama3-70b-8192"
            }
        }
        self.default_provider = "openrouter"
        self.max_tokens = 500  # экономим
        self.cache = {}        # простой кэш

    async def generate(self, prompt: str, provider: str = None, use_cache: bool = True) -> str:
        """Главный метод: получить ответ на промпт."""
        if use_cache and prompt in self.cache:
            return self.cache[prompt]

        provider = provider or self.default_provider
        config = self.providers.get(provider)
        if not config or not config["key"]:
            # пробуем другие
            return await self._fallback(prompt)

        try:
            async with httpx.AsyncClient() as client:
                headers = {
                    "Authorization": f"Bearer {config['key']}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": config["model"],
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": self.max_tokens,
                    "temperature": 0.7
                }
                resp = await client.post(config["url"], json=payload, headers=headers)
                resp.raise_for_status()
                answer = resp.json()["choices"][0]["message"]["content"]
                if use_cache:
                    self.cache[prompt] = answer
                return answer
        except Exception as e:
            # логируем ошибку
            print(f"Ошибка {provider}: {e}")
            return await self._fallback(prompt)

    async def _fallback(self, prompt: str) -> str:
        """Перебор остальных провайдеров."""
        for name, config in self.providers.items():
            if name != self.default_provider and config["key"]:
                try:
                    return await self.generate(prompt, provider=name, use_cache=False)
                except:
                    continue
        return "Все провайдеры недоступны. Попробуйте позже."
Использование в боте (aiogram)
python
from src.services.llm import LLMService

llm = LLMService()

@dp.message()
async def ask_ai(message: types.Message):
    answer = await llm.generate(message.text)
    await message.answer(answer)
Экономия токенов
Установи max_tokens в 500–1000 (хватит для большинства ответов).

Кэшируй повторяющиеся вопросы (in-memory словарь или Redis).

Для простых вопросов используй более дешёвую модель (например, groq/llama3-70b-8192 вместо deepseek).

Не отправляй длинные системные промпты — вынеси их в скиллы или в отдельную переменную.

Безопасность
Все ключи храни в .env.

Никогда не выводи ключи в логах или ответах пользователю.

Ограничь частоту запросов (rate limit) в боте, чтобы не сжечь бюджет.

Связанные скиллы
rag-implementation — для поиска по документам перед вызовом LLM.

telegram-bot-builder — интеграция с ботом.