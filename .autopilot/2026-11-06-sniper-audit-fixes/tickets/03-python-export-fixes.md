# 03 — [Coder Agent] Атомарность и оптимизация экспорта в Python

**Требования:** R03, R13, R14
**Зона:** `src/`, `run_watcher.py`
**Волна:** 2
**Зависит от:** 01
**Итог:** Атомарный экспорт config.lua и устранение повторного расчета при --wow-addon-path

## Что делаем
Устранить дефекты в `src/analytics.py` и `run_watcher.py`:
1. Атомарная запись `config.lua` через `output_path + ".tmp"` и `os.replace`.
2. Дефолтный `min_profit_copper = 0` в `export_sniper_addon_config`.
3. Копирование готового `config.lua` через `shutil.copyfile` при наличии `--wow-addon-path` в `run_watcher.py` вместо повторного тяжелого расчета.

## Критерии приёмки
- Запись файла атомарна.
- Исключен повторный прогон `export_sniper_addon_config` при `--wow-addon-path`.
