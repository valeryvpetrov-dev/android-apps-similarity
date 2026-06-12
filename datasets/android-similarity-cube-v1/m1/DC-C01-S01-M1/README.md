# DC-C01-S01-M1

Дата: 2026-06-12.

Это M1 diagnostic set для `C01 repack` и сценария `S01 pair_similarity`.
Он собран из RePack SHA-пар и материализованных APK из AndroZoo.

## Состав

- положительных repack-пар: 20
- отрицательных пар: 20
- split: dev, holdout
- positive split: dev=16, holdout=4
- negative split: dev=16, holdout=4
- строк тегов объяснений: 82

## Статус

`ready M1 diagnostic`, не `claim-ready benchmark`.

Набор малый: он нужен для диагностики и подготовки экспериментов, а не для сильного вывода о качестве метода.

## Файлы

- `candidate_pairs.csv`: candidate positive-пары до фильтра материализации.
- `download_queue.csv`: очередь SHA для AndroZoo.
- `source_selection.csv`: происхождение выбранных RePack-строк.
- `materialization_status.csv`: скачивание, декомпиляция и smoke-признаки по candidate-парам.
- `manifest.csv`: положительные и отрицательные пары M1 diagnostic set.
- `pair_change_tags.csv`: теги для проверки объяснений.

## Разрешенное использование

Можно использовать для M1-диагностики, ручного разбора repack-пар, проверки negative sampling и подготовки малых прогонов.

## Запрещенное использование

Нельзя считать этот набор claim-ready benchmark и нельзя переносить выводы за пределы малой C01-диагностики.

## Следующий шаг

Использовать набор для диагностических C01/S01 прогонов и ручной проверки отрицательных пар.
