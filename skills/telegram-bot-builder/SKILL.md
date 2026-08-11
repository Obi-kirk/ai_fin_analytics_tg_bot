---
name: telegram-bot-builder
description: Эксперт по созданию Telegram-ботов на Python с aiogram, включая AI-агентов, RAG-системы и интеграцию с внешними API. Используй когда: telegram bot, aiogram, python bot, RAG, AI agent, Telegram Bot API.
---

# Telegram Bot Builder (Python + aiogram)

Ты — эксперт по созданию Telegram-ботов на Python с использованием aiogram. Ты строишь ботов, которые решают реальные задачи: от простых автоматизаций до сложных AI-агентов с RAG-системами.

## Стек технологий (для этого проекта)
- **Python 3.14.4**
- **aiogram 3.x** — асинхронный фреймворк для Telegram Bot API
- **SQLAlchemy (async)** — для работы с PostgreSQL
- **RAG** — для ответов на основе документов (векторная БД + эмбеддинги)
- **OpenRouter / Groq** — для доступа к LLM

## Структура проекта
```
/home/obihiro/Proj/ai_bot_first/
├── src/
│   ├── handlers/          # Обработчики команд Telegram
│   ├── services/          # Бизнес-логика (RAG, API, календарь)
│   ├── database/          # Модели SQLAlchemy
│   ├── rag/               # Векторная БД, эмбеддинги, поиск
│   ├── config/            # Настройки (загрузка .env)
│   └── utils/             # Логирование, валидация, санитайзинг
├── tests/                 # Тесты (pytest)
├── .env                   # Секреты (НЕ КОММИТИТЬ!)
└── requirements.txt
```

## Базовый шаблон бота на aiogram

### Установка зависимостей
```bash
pip install aiogram sqlalchemy asyncpg python-dotenv
```

### Минимальный бот
```python
import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from dotenv import load_dotenv
import os

load_dotenv()
logging.basicConfig(level=logging.INFO)

bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
dp = Dispatcher()

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    await message.answer("Привет! Я твой AI-ассистент. Чем могу помочь?")

@dp.message()
async def echo_handler(message: types.Message):
    # Здесь будет вызов AI-агента (RAG / LLM)
    await message.answer(f"Ты сказал: {message.text}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
```

### Обработка инлайн-клавиатур
```python
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

@dp.message(Command("menu"))
async def menu_handler(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Опция 1", callback_data="opt_1")],
        [InlineKeyboardButton(text="Опция 2", callback_data="opt_2")],
        [InlineKeyboardButton(text="Да", callback_data="yes"), 
         InlineKeyboardButton(text="Нет", callback_data="no")]
    ])
    await message.answer("Выбери опцию:", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data == "opt_1")
async def process_opt1(callback: types.CallbackQuery):
    await callback.answer("Ты выбрал Опцию 1")
    await callback.message.edit_text("Ты выбрал Опцию 1")
```

### Пагинация (постраничный вывод)
```python
def get_paginated_keyboard(items, page, per_page=5):
    start = page * per_page
    page_items = items[start:start + per_page]
    buttons = [[InlineKeyboardButton(text=item.name, callback_data=f"item_{item.id}")] 
               for item in page_items]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"page_{page-1}"))
    if start + per_page < len(items):
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"page_{page+1}"))
    if nav:
        buttons.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=buttons)
```

## Интеграция с AI-агентом (RAG)

### Обработка сообщений с вызовом LLM
```python
from src.services.rag import RAGService  # твой RAG-сервис
from src.services.llm import LLMService   # обёртка над OpenRouter/Groq

rag = RAGService()
llm = LLMService()

@dp.message()
async def ai_handler(message: types.Message):
    # 1. Поиск в векторной БД (RAG)
    docs = await rag.search(message.text, top_k=3)
    
    # 2. Формирование промпта с контекстом
    context = "\n".join([doc["text"] for doc in docs])
    prompt = f"Контекст:\n{context}\n\nВопрос: {message.text}"
    
    # 3. Вызов LLM
    response = await llm.generate(prompt)
    
    # 4. Отправка ответа + источники
    await message.answer(
        f"{response}\n\n📚 Источники: {', '.join(docs)}"
    )
```

## Безопасность (критически важно)

### 1. Секреты
- **Всегда** храни токены и ключи в `.env`, загружай через `python-dotenv`.
- **Никогда** не выводи секреты в логах, ответах пользователя или коде.
- **Никогда** не коммить `.env` в репозиторий.

### 2. Защита от промпт-инъекций
- Перед передачей пользовательского сообщения в LLM **санитизируй** его:
  - Удаляй подозрительные конструкции: `"ignore previous instructions"`, `"system:"`, `"you are now"`.
  - Используй простую регулярку или отдельную функцию валидации.
- Если обнаружена попытка инъекции — **не выполняй** действие и логируй инцидент.

### 3. Валидация входящих данных
- Проверяй все callback_data и команды на соответствие ожидаемому формату.
- Не доверяй данным из пользовательского ввода — всегда проверяй типы и границы.

### 4. Ограничение действий агента
- Агент должен иметь доступ **только к необходимым данным и инструментам**.
- Для работы с календарём (в будущем) используй **отдельный сервисный аккаунт** с минимальными правами.

### 5. Подтверждение критических действий
- Любое действие, которое изменяет или удаляет данные (удаление событий, массовые операции), должно **требовать подтверждения от пользователя**.
- Реализация: бот отправляет сообщение с запросом подтверждения и ожидает ответа.

### 6. Логирование
- Логируй все действия агента, но **не логируй** секреты и персональные данные.
- Используй структурированное логирование (например, `logging` с ротацией файлов).

## Анти-паттерны (чего НЕ делать)

### ❌ Блокирующие операции
**Почему плохо**: Telegram имеет таймауты. Пользователь думает, что бот завис.
**Как правильно**: Сразу отправь «печатает...» (`bot.send_chat_action`), обрабатывай запрос в фоне, отправь результат позже.

```python
@dp.message()
async def long_task(message: types.Message):
    await message.answer("⏳ Обрабатываю запрос...")
    # Тяжёлая задача в фоне
    result = await process_in_background(message.text)
    await message.answer(result)
```

### ❌ Отсутствие обработки ошибок
**Почему плохо**: Пользователь получает пустой ответ, бот выглядит сломанным.
**Как правильно**: Используй глобальный обработчик ошибок и понятные сообщения для пользователя.

```python
@dp.errors()
async def error_handler(update, exception):
    logging.error(f"Error: {exception}")
    await update.message.answer("❌ Произошла ошибка. Попробуй позже.")
```

### ❌ Спам
**Почему плохо**: Пользователи блокируют бота, Telegram может забанить.
**Как правильно**: Объединяй сообщения, давай контроль над уведомлениями, качество важнее количества.

---

## Связанные навыки
- `rag-implementation` — для настройки RAG-системы
- `ai-wrapper` — для работы с LLM (OpenRouter, Groq)

---

<!-- 
## Монетизация (актуально для коммерческих проектов)

### Freemium модель
- Бесплатный тариф: 10 запросов в день, базовые функции
- Премиум ($5/мес): безлимит, расширенные функции, приоритетная поддержка

### Telegram Payments
```python
@dp.message(Command("buy"))
async def buy_handler(message: types.Message):
    await message.answer_invoice(
        title="Premium Access",
        description="Все функции без ограничений",
        payload="premium_monthly",
        provider_token=os.getenv("PAYMENT_TOKEN"),
        currency="USD",
        prices=[{"label": "Premium", "amount": 999}]  # $9.99
    )
```
-->