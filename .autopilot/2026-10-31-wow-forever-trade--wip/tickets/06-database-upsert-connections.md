# 06 — Исправление базы данных: UPSERT, фабрика соединений и оптимизация ротации

**Требования:** R22, R23, R24
**Зависит от:** 01
**Зона:** `src/database.py` · `tests/test_database.py`
**Волна:** 5
**Ревью:** да — фундамент
**Модель:** сильная

## Что должно заработать

1. `load_catalog` и `load_recipes` используют честный SQLite UPSERT (`INSERT INTO ... ON CONFLICT(item_id) DO UPDATE ...` для items и аналогично для recipes/recipe_reagents). При наличии уже существующих снимков в `price_snapshots` повторная загрузка каталога не вызывает `sqlite3.IntegrityError: FOREIGN KEY constraint failed`.
2. Функция `init_schema(conn)` выделена для разовой инициализации таблиц и индексов. Создана фабрика `get_connection(db_path)` с таймаутом `timeout=30.0`, `PRAGMA foreign_keys = ON;`, `PRAGMA journal_mode = WAL;`, `PRAGMA synchronous = NORMAL;` и `PRAGMA busy_timeout = 30000;`. `init_db(db_path)` сохранена как совместимая обертка.
3. Очистка `prune_snapshots`: добавлен индекс `idx_snapshots_timestamp ON price_snapshots(timestamp)`, время приведено к целочисленному epoch (`INTEGER`), удаление устаревших снимков выполняется чанками по 5000 строк с промежуточным `conn.commit()`, предотвращая Full Table Scan и долгие блокировки.

## Из брифа, дословно

> «1.1 Заменить INSERT OR REPLACE на честный SQLite UPSERT (ON CONFLICT ... DO UPDATE) для предотвращения нарушения Foreign Keys»
> «1.2 Разделить первичную инициализацию схемы init_schema() и фабрику соединений get_connection(), задать busy_timeout = 30000, synchronous = NORMAL и WAL»
> «1.3 Оптимизировать prune_snapshots: индекс idx_snapshots_timestamp, INTEGER epoch timestamp, удаление батчами по 5000 строк»

## Разделы спецификации

Истории 22, 23, 24; Решения по реализации; `interfaces.md` §1.

## Критерии приёмки

- [ ] Повторный вызов `load_catalog()` после вставки снимков в `price_snapshots` успешно обновляет атрибуты предметов и не падает с `IntegrityError`
- [ ] Фабрика `get_connection()` возвращает настроенное соединение с активным `WAL`, `synchronous = NORMAL` и `busy_timeout = 30000`
- [ ] `prune_snapshots()` удаляет записи старше `max_days` порциями по 5000 строк и возвращает точное число удаленных строк без блокировки схемы
