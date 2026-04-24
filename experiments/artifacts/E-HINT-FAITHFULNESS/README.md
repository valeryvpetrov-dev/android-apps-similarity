# E-HINT-FAITHFULNESS

Артефакт для автоматической оценки `hint`/`evidence`-объяснений без ручной разметки.

## Что считается

- `faithfulness`: корреляция между важностью признака в hint и `|Δscore|` после его маскировки.
- `sufficiency`: retained-score метрика для `hint_only_features`; значение `1.0` означает, что hint сам по себе сохраняет весь score линейного суррогата.
- `comprehensiveness`: `score_full - score_without_hint`.

Реализация: [`script/hint_faithfulness.py`](/tmp/wave19-F-submodule/script/hint_faithfulness.py).

## Артефакты

- Основной отчёт: [`report.json`](/tmp/wave19-F-submodule/experiments/artifacts/E-HINT-FAITHFULNESS/report.json)
- Semi-real pairwise config: [`semi-real-config.json`](/tmp/wave19-F-submodule/experiments/artifacts/E-HINT-FAITHFULNESS/semi-real-config.json)
- Semi-real candidate list: [`semi-real-enriched-candidates.json`](/tmp/wave19-F-submodule/experiments/artifacts/E-HINT-FAITHFULNESS/semi-real-enriched-candidates.json)
- Semi-real pairwise output: [`semi-real-pairwise.json`](/tmp/wave19-F-submodule/experiments/artifacts/E-HINT-FAITHFULNESS/semi-real-pairwise.json)

## Как воспроизвести

Если доступен реальный CSV-корпус `experiments/artifacts/E-HINT-004/deep-184-annotated.csv`, достаточно:

```bash
python3 script/hint_faithfulness.py \
  --input-csv experiments/artifacts/E-HINT-004/deep-184-annotated.csv \
  --output-json experiments/artifacts/E-HINT-FAITHFULNESS/report.json
```

Если CSV отсутствует, текущий semi-real прогон воспроизводится так:

```bash
python3 - <<'PY'
import json
from itertools import combinations
from pathlib import Path

root = Path("/tmp/wave19-F-submodule")
artifact_dir = root / "experiments" / "artifacts" / "E-HINT-FAITHFULNESS"
apk_paths = sorted((root / "apk").rglob("*.apk"))
selected = list(combinations(apk_paths, 2))[:20]

config = {
    "stages": {
        "pairwise": {
            "features": ["code", "metadata"],
            "metric": "jaccard",
            "threshold": 0.0,
        }
    }
}
enriched = {
    "enriched_candidates": [
        {
            "pair_id": f"SEMIREAL-{index:03d}",
            "app_a": {"app_id": left.stem, "apk_path": str(left)},
            "app_b": {"app_id": right.stem, "apk_path": str(right)},
        }
        for index, (left, right) in enumerate(selected, start=1)
    ]
}

(artifact_dir / "semi-real-config.json").write_text(
    json.dumps(config, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
(artifact_dir / "semi-real-enriched-candidates.json").write_text(
    json.dumps(enriched, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY

SIMILARITY_SKIP_REQ_CHECK=1 python3 script/pairwise_runner.py \
  --config experiments/artifacts/E-HINT-FAITHFULNESS/semi-real-config.json \
  --enriched experiments/artifacts/E-HINT-FAITHFULNESS/semi-real-enriched-candidates.json \
  --output experiments/artifacts/E-HINT-FAITHFULNESS/semi-real-pairwise.json \
  --processes-count 1 \
  --threads-count 1

python3 script/hint_faithfulness.py \
  --input-csv experiments/artifacts/E-HINT-004/deep-184-annotated.csv \
  --pairwise-json experiments/artifacts/E-HINT-FAITHFULNESS/semi-real-pairwise.json \
  --output-json experiments/artifacts/E-HINT-FAITHFULNESS/report.json
```

## Прогон на реальных/полу-реальных данных

`experiments/artifacts/E-HINT-004/deep-184-annotated.csv` в этой ветке отсутствует, поэтому использован semi-real источник: 5 локальных APK из `apk/` и все 10 попарных комбинаций между ними.

Методика:

- Pairwise-слой запускался через [`script/pairwise_runner.py`](/tmp/wave19-F-submodule/script/pairwise_runner.py) в quick-path с `features=[code, metadata]`, `metric=jaccard`, `threshold=0.0`.
- Источником объяснения служит текущий pairwise output `evidence`, а не synthetic hand-made JSON.
- [`script/hint_faithfulness.py`](/tmp/wave19-F-submodule/script/hint_faithfulness.py) конвертирует каждый pairwise row в один evaluable hint:
  `pair_features = {pair:full_similarity_score, layer_score:*, signature_match:*}`,
  `hint_features = explanation_hints/evidence`,
  `hint_only_features = subset(pair_features)`.
- Итоговый `report.json` теперь содержит две независимые секции: `synthetic_run` и `real_data_run`.

Итог:

- `real_data_run.source.type = pairwise_json`
- `real_data_run.n_hints = 10`
- `faithfulness_mean = 1.0`, `faithfulness_median = 1.0`, `faithfulness_stddev = 0.0`
- `sufficiency_mean = 0.666667`, `sufficiency_median = 0.666667`, `sufficiency_stddev = 0.0`
- `comprehensiveness_mean = 0.666667`, `comprehensiveness_median = 0.666667`, `comprehensiveness_stddev = 0.0`

## Ограничения

- Это semi-real, а не полный реальный корпус из `E-HINT-004`: CSV `deep-184-annotated.csv` отсутствует.
- `describe_pair.py` в текущем репозитории отсутствует, поэтому использован ближайший живой путь: `pairwise_runner.py` + `evidence`-driven explanation export.
- Источник объяснения здесь не типизированные hints из протокола ручной оценки, а текущие `evidence`/`explanation_hints` формата `layer_score:*` и `signature_match:*`.
- Прогон идёт по quick-path (`code + metadata`) и не покрывает enhanced component/resource/library ветки.
- Для текущего линейного суррогата `faithfulness` на evidence-driven hints почти тривиален, а `sufficiency` и `comprehensiveness` совпадают по массе hinted subset. Поэтому этот блок годится как честный semi-real supporting signal, но не как замена реального размеченного корпуса.
