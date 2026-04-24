# EXEC-LIBLOOM-QUALITY-REAL-RUN

Первый real-run замер качества LIBLOOM на локальном mini-корпусе из `apk/`.

## Методика

- Скрипт: `python3 -m script.run_libloom_quality_smoke`
- Корпус: `apk/` в текущем worktree, 5 APK:
  `simple_app-empty.apk`, `simple_app-releaseNonOptimized.apk`,
  `simple_app-releaseOptimized.apk`, `simple_app-releaseRename.apk`,
  `snake.apk`
- Разметка: inline mini-labeling по 5 TPL-кандидатам:
  `okhttp3`, `gson`, `retrofit`, `glide`, `kotlinx-coroutines`
- Для каждого APK скрипт вызывает
  `noise_profile_envelope.apply_libloom_detection(...)`, извлекает найденные
  TPL и считает:
  `precision = TP / (TP + FP)`,
  `recall = TP / (TP + FN)`,
  `coverage = share(APK with >=1 detected TPL)`

## Результат запуска 2026-04-24

- Статус: `libloom_unavailable`
- Причина: `LIBLOOM_HOME is not set`
- `corpus_size = 5`
- `precision = 0.0`
- `recall = 0.0`
- `coverage = 0.0`

Подробности в [report.json](/tmp/wave20-B-submodule/experiments/artifacts/EXEC-LIBLOOM-QUALITY-REAL-RUN/report.json).

## Ограничения

- Это не репрезентативный корпус: всего 5 APK из локального smoke-набора.
- Inline mini-разметка покрывает только 5 известных TPL и не претендует на
  полноту по реальному корпусу.
- Текущий запуск не измерил фактическое качество LIBLOOM, потому что
  внешняя зависимость недоступна в окружении.
- При появлении `LIBLOOM_HOME` и рабочего `libs_profile/` отчёт нужно
  перегенерировать тем же скриптом.
