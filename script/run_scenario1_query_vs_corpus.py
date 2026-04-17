#!/usr/bin/env python3
"""Сценарий 1: поиск похожих на входной APK в корпусе.

Для каждого известного клона (pair label=clone в fdroid-corpus-v2-pairs.csv)
берём первый APK как запрос, сравниваем со всеми остальными 349 APK из
корпуса, сортируем по убыванию оценки TLSH, записываем позицию истинного
клона в итоговом списке.

Входы:
  --pairs: fdroid-corpus-v2-pairs.csv
  --feature-cache: директория с *.pkl (TLSH хеши всех APK)
  --output: путь к итоговому CSV

Метрики на выходе:
  - Recall@1, Recall@5, Recall@10 (доля запросов, где истинный клон в топ-N)
  - MRR (средняя взаимная позиция)
  - Медиана позиции истинного клона

Это сценарий 1 из system/canonical-use-cases.md. Правильный формат оценки
системы в её основном режиме применения.
"""
from __future__ import annotations

import argparse
import csv
import pickle
import sys
from pathlib import Path
from statistics import median
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from code_view_v2 import compare_code_v2


def load_cache(cache_dir: Path, apk_name: str) -> Optional[str]:
    """Загрузить кешированный TLSH хеш для APK по имени файла."""
    path = cache_dir / (apk_name + '.pkl')
    if not path.exists():
        return None
    try:
        data = pickle.load(open(path, 'rb'))
        return data.get('tlsh_hash')
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--pairs', required=True, type=Path)
    ap.add_argument('--feature-cache', required=True, type=Path)
    ap.add_argument('--output', required=True, type=Path)
    args = ap.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)

    # Загрузить пары
    with open(args.pairs) as f:
        pairs = list(csv.DictReader(f))
    clone_pairs = [p for p in pairs if p['label'] == 'clone']
    print(f'Загружено {len(clone_pairs)} пар клонов из {len(pairs)} размеченных пар.')

    # Собрать все уникальные APK из корпуса
    corpus_apks = set()
    for p in pairs:
        corpus_apks.add(p['apk1'])
        corpus_apks.add(p['apk2'])
    corpus_apks = sorted(corpus_apks)
    print(f'Всего уникальных APK в корпусе: {len(corpus_apks)}.')

    # Предзагрузить TLSH хеши всех APK корпуса
    cache: dict[str, Optional[str]] = {}
    for apk in corpus_apks:
        cache[apk] = load_cache(args.feature_cache, apk)
    missing = sum(1 for v in cache.values() if v is None)
    print(f'Загружено {len(cache) - missing} хешей, пропущено {missing} (нет в кеше).')

    # Для каждого запроса-клона — сравнить со всеми остальными APK
    results: list[dict] = []
    skipped = 0
    for p in clone_pairs:
        query = p['apk1']
        true_clone = p['apk2']
        q_hash = cache.get(query)
        if q_hash is None:
            skipped += 1
            continue

        # Вычислить оценку для каждого кандидата
        scored: list[tuple[str, float]] = []
        for cand in corpus_apks:
            if cand == query:
                continue
            c_hash = cache.get(cand)
            if c_hash is None:
                continue
            r = compare_code_v2(q_hash, c_hash)
            scored.append((cand, r['score']))

        # Сортировать по убыванию оценки
        scored.sort(key=lambda t: -t[1])

        # Найти позицию истинного клона
        true_rank = None
        true_score = None
        for idx, (cand, score) in enumerate(scored, start=1):
            if cand == true_clone:
                true_rank = idx
                true_score = score
                break

        if true_rank is None:
            skipped += 1
            continue

        top1 = scored[0] if scored else (None, 0.0)
        top5_min = scored[4][1] if len(scored) > 4 else 0.0
        top10_min = scored[9][1] if len(scored) > 9 else 0.0

        results.append({
            'query_apk': query,
            'true_clone_apk': true_clone,
            'true_clone_rank': true_rank,
            'true_clone_score': round(true_score, 4),
            'top1_apk': top1[0],
            'top1_score': round(top1[1], 4),
            'top5_min_score': round(top5_min, 4),
            'top10_min_score': round(top10_min, 4),
            'total_candidates': len(scored),
        })

    print(f'Обработано запросов: {len(results)}, пропущено: {skipped}.')

    # Записать CSV
    if results:
        with open(args.output, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            w.writeheader()
            w.writerows(results)
        print(f'Записано -> {args.output}')

    # Метрики
    ranks = [r['true_clone_rank'] for r in results]
    n = len(ranks)
    r1 = sum(1 for r in ranks if r <= 1) / n if n else 0
    r5 = sum(1 for r in ranks if r <= 5) / n if n else 0
    r10 = sum(1 for r in ranks if r <= 10) / n if n else 0
    mrr = sum(1.0 / r for r in ranks) / n if n else 0
    med_rank = median(ranks) if ranks else 0

    print()
    print('=== Метрики сценария 1 (поиск похожих) ===')
    print(f'Запросов обработано:          {n}')
    print(f'Полнота@1  (Recall@1):        {r1:.4f}')
    print(f'Полнота@5  (Recall@5):        {r5:.4f}')
    print(f'Полнота@10 (Recall@10):       {r10:.4f}')
    print(f'Средняя взаимная позиция:      {mrr:.4f}')
    print(f'Медиана позиции истинного клона: {med_rank}')
    print(f'Максимальная позиция:          {max(ranks) if ranks else 0}')
    print(f'Доля позиций >10:              {sum(1 for r in ranks if r > 10)/n:.4f}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
