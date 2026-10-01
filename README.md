# Avatar Scene Indexer

Личный инструмент для индексации серий «Аватар: Легенда об Аанге» и семантического поиска сцен для монтажа/сценария.

Полный план: `C:\Users\brath\.claude\plans\twinkly-tinkering-treehouse.md`

## Установка

```
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
copy .env.example .env   # и вписать ANTHROPIC_API_KEY

cd ../frontend
npm install
```

## Phase 1 — проверка пайплайна на одной серии (без UI)

```
cd backend
.venv/Scripts/python scripts/ingest_episode.py "D:\avatar\s01e01.mkv" --book 1 --episode 1 --title "The Boy in the Iceberg"
```

Смотрим результат прямо в SQLite (`backend/data/avatar.db`) — таблицы `transcript`, `shots`, `scenes`.

## Запуск (Phase 3+)

Backend API:
```
cd backend
.venv/Scripts/uvicorn app.main:app --reload
```

Воркер индексации (обрабатывает добавленные через UI источники):
```
cd backend
.venv/Scripts/python -m app.pipeline.worker
```

Frontend:
```
cd frontend
npm run dev
```

Открыть http://localhost:5173
