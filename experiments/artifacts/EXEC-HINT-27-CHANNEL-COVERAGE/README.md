# EXEC-HINT-27-CHANNEL-COVERAGE-DATASET

Задача: собрать F-Droid v2 dataset, где channel-faithfulness replay получает
данные по всем 5 evidence-каналам: `code`, `component`, `library`,
`resource`, `signing`.

## Данные

- Corpus: `/Users/valeryvpetrov/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks`
- Размер corpus: 350 APK.
- Dataset: `channel_dataset.json`
- Replay metrics: `per_channel_metrics_v2.json`
- Seed: 42.
- Mix: 8 `clone` + 8 `repackage` + 8 `similar` + 6 `different` = 30 пар.

Ground truth эвристический:

- `clone`: одинаковый APK sha/signature либо near-duplicate fallback:
  одинаковый package name и высокий quick static overlap по
  `code/component/resource/library`;
- `repackage`: одинаковый package name, разные sha256;
- `similar`: library-set overlap >= 0.5 по TPL/quick library set;
- `different`: library-set overlap < 0.1 и разные package-category prefix.

Сравнение пар делалось через quick-path `m_static_views.compare_all` без
`pairwise_runner` и без нового decode. Для library-set использован уже
существующий decoded F-Droid v2 cache, чтобы получить TPL package evidence.

## Coverage

`channel_dataset.json`:

| Канал | Пар с data | Доля |
|---|---:|---:|
| code | 30/30 | 1.0 |
| component | 30/30 | 1.0 |
| library | 30/30 | 1.0 |
| resource | 30/30 | 1.0 |
| signing | 30/30 | 1.0 |

Пар со всеми 5 каналами: 30/30 = 1.0. Требование >=60% выполнено.

## Replay `compute_channel_faithfulness`

`per_channel_metrics_v2.json`:

| Канал | n_pairs_with_data | faithfulness mean | sufficiency mean | comprehensiveness mean |
|---|---:|---:|---:|---:|
| code | 30 | 1.0 | 1.0 | 1.0 |
| component | 30 | 0.566667 | 0.566667 | 0.566667 |
| library | 30 | 0.933333 | 0.933333 | 0.933333 |
| resource | 30 | 1.0 | 1.0 | 1.0 |
| signing | 30 | 0.0 | 0.0 | 0.0 |

Главный результат: `component`, `library`, `resource` теперь имеют non-None
метрики, то есть диагностика реально покрывает 5 каналов вместо 2.

## Сравнение с волной 22

Волна 22 (`EXEC-HINT-22-CHANNEL-SPLIT`) использовала 10 полу-реальных пар:

- `code`: data на 10/10;
- `signing`: data на 10/10;
- `component`, `library`, `resource`: data на 0/10, метрики `None`.

Волна 27:

- 30 пар на F-Droid v2;
- все 5 каналов имеют data на 30/30;
- replay впервые даёт non-None metrics для `component`, `library`, `resource`.

## Воспроизведение

```bash
SIMILARITY_SKIP_REQ_CHECK=1 python3 script/build_channel_coverage_dataset.py \
  --corpus_dir /Users/valeryvpetrov/Library/Caches/phd-shared/datasets/fdroid-corpus-v2-apks \
  --out experiments/artifacts/EXEC-HINT-27-CHANNEL-COVERAGE/channel_dataset.json \
  --n_pairs 30 \
  --mix clone:8,repackage:8,similar:8,different:6 \
  --seed 42
```

Тесты:

```bash
SIMILARITY_SKIP_REQ_CHECK=1 python3 -m pytest \
  script/test_channel_coverage_dataset.py \
  script/test_hint_faithfulness_channel.py
```
