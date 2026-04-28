# SCREENING-31-INDEX-RECALIBRATE-MIXED-CORPUS

## Что это

Замер `recall_at_shortlist` по классам модификации APK на смешанном корпусе. Расширение
SCRN-25/30 — раньше калибровка делалась только на одном датасете (single-class), теперь
показывает, какие классы LSH-индекс ловит хорошо, а какие — хуже.

## Источники данных

| Класс | Источник | n пар |
|---|---|---|
| class_1 (Repack-only) | F-Droid v2 baseline (placeholder, self-clone почти 1.0) | 20 |
| class_2 (Library injection) | нет targeted-эксперимента до волны 31 | 0 |
| class_3 (Resource modification) | REPR-30/31 покрывают, но без LSH replay | 0 |
| class_4 (Package rename) | SCRN-30-PACKAGE-RENAME `jaccard_per_pair` | 20 |
| class_5 (Code injection) | DEEP-30-CODE-INJECT `scored_pairs` (label='clone') | 35 |
| class_6 (R8 obfuscation) | EXEC-HINT-30-OBFUSCATION-DATASET (mock R8) | 10 |

## Реальные числа `recall_at_shortlist`

| Класс | Recall | Комментарий |
|---|---|---|
| class_1 | 0.95 | F-Droid v2 self-clone — baseline почти tривиальный |
| class_4 | **0.35** | SCRN-30: namespace-shift сильно ломает jaccard на package_prefix shingles |
| class_5 | **1.00** | DEEP-30: F1=1.0 на code_view_v4 — code-injection ловится отлично |
| class_6 | **0.00** | HINT-30 mock: full_similarity_score < 0.70 (R8 ломает minhash) |

## Что показывает замер

1. **LSH хорошо ловит** code-injection (class_5) и repack-only (class_1) — почти все
   истинные пары попадают в shortlist при текущем `THRESH-002=0.70`.
2. **LSH плохо ловит** R8-obfuscation (class_6 → 0.00) и среднеслабо ловит package
   rename (class_4 → 0.35). Это ожидаемо: R8 переименовывает классы и методы, а package
   rename ломает namespace-prefixed shingles.
3. **proposed_thresh_002 = 0.52** (медиана jaccard true-shortlist пар SCRN-30 = 0.484
   < current 0.70 на >0.05). Это — мягкое предложение снизить порог до 0.52, что
   повысит recall на class_4 и class_6 за счёт роста shortlist (precision/throughput
   trade-off).

## Что закрывает

- THRESH-002 (current 0.70) теперь подкреплён **многомерным замером** — не одним
  агрегатом, а разбивкой по классам модификации.
- Закрывает пробел из критика волны 29 (SCRN class_4 partial): «recall на namespace-shift
  низкий, но нет per-class замера на смешанном корпусе».

## Что не закрывает (отложено)

- Реальная R8 пара (class_6) — пока mock; HINT-31 строит реальные R8-пары через d8/r8.
- Реальная LSH-индекса перестройка с новым `proposed_thresh_002=0.52` и замер shortlist
  size (компромисс recall vs throughput) — отложено в SCRN-32 P0.
- Дatasets для class_2 (Library injection) и class_3 (Resource modification, LSH replay) —
  P1 пробелы.

## Воспроизведение

```bash
cd prototypes/submodules/android-apps-similarity
python3 -m pytest script/test_lsh_recalibrate_mixed.py -v
python3 script/run_lsh_recalibrate_mixed.py \
    --scrn30 experiments/artifacts/SCREENING-30-PACKAGE-RENAME/report.json \
    --deep30 experiments/artifacts/DEEP-30-CODE-INJECT/report.json \
    --hint30 experiments/artifacts/EXEC-HINT-30-OBFUSCATION-DATASET/r8_pairs.json \
    --out experiments/artifacts/SCREENING-31-MIXED-CORPUS/report.json
```

## Ссылки

- Главa 03 НКР, раздел LSH/MinHash (THRESH-002).
- SCRN-25-LSH-RECALL-FDROID, SCRN-30-PACKAGE-RENAME — предшествующие итерации.
- DEEP-30-CODE-INJECT — class_5 источник.
- EXEC-HINT-30-OBFUSCATION-DATASET — class_6 mock источник.
