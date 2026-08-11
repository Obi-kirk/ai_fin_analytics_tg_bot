---
name: rag-implementation
description: RAG для Telegram-бота на Python. Поиск ответов в документах через pgvector.
---

# RAG Implementation

RAG — это когда бот ищет ответ в твоих документах, а не просто фантазирует.

## Что нужно

- PostgreSQL с pgvector
- Модель эмбеддингов (локальная, бесплатно)

## Шаги

### 1. Включи pgvector в PostgreSQL

```sql
CREATE EXTENSION IF NOT EXISTS vector;
Таблица для хранения:

sql
CREATE TABLE documents (
    id SERIAL PRIMARY KEY,
    text TEXT,
    embedding vector(384)  -- размерность модели ниже
);
2. Получай эмбеддинги (локально, бесплатно)
bash
pip install sentence-transformers
python
from sentence_transformers import SentenceTransformer

model = SentenceTransformer('all-MiniLM-L6-v2')

def get_embedding(text):
    return model.encode(text).tolist()
3. Разбивай документы на чанки
python
def chunk_text(text, chunk_size=800, overlap=100):
    words = text.split()
    chunks = []
    for i in range(0, len(words), chunk_size - overlap):
        chunk = ' '.join(words[i:i+chunk_size])
        chunks.append(chunk)
    return chunks
4. Сохраняй в БД (SQLAlchemy async)
python
from sqlalchemy import Column, Integer, Text
from pgvector.sqlalchemy import Vector

class Document(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True)
    text = Column(Text)
    embedding = Column(Vector(384))

# Сохранение
async def save_chunk(text):
    emb = get_embedding(text)
    doc = Document(text=text, embedding=emb)
    session.add(doc)
    await session.commit()
5. Поиск топ-3 похожих чанков
python
from sqlalchemy import select

async def search(query, top_k=3):
    q_emb = get_embedding(query)
    stmt = select(Document).order_by(
        Document.embedding.cosine_distance(q_emb)
    ).limit(top_k)
    result = await session.execute(stmt)
    return result.scalars().all()
6. Используй в боте (aiogram)
python
@dp.message()
async def handle(message: types.Message):
    # Ищем похожие чанки
    docs = await search(message.text)
    if not docs:
        await message.answer("Не нашёл ответа в документах.")
        return
    
    # Собираем контекст
    context = "\n\n".join([d.text for d in docs])
    sources = [f"Источник {i+1}" for i in range(len(docs))]
    
    # Отправляем в LLM (через ai-wrapper)
    prompt = f"Контекст:\n{context}\n\nВопрос: {message.text}"
    answer = await llm.generate(prompt)
    
    # Ответ с источниками
    await message.answer(f"{answer}\n\n📚 {', '.join(sources)}")
Обновление индекса
Простой скрипт для загрузки документов:

python
# indexer.py
async def index_documents(folder_path):
    for file in os.listdir(folder_path):
        text = open(f"{folder_path}/{file}").read()
        for chunk in chunk_text(text):
            await save_chunk(chunk)
Запускай вручную, когда добавляешь новые документы.

Экономия
Размер чанка: 500–800 токенов

overlap: 50–100

top_k: 3 (больше не нужно)

Связанные скиллы
ai-wrapper — вызов LLM

telegram-bot-builder — сам бот