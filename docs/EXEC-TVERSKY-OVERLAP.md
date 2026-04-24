# EXEC-TVERSKY-OVERLAP

`script/library_view_v2.py` now returns four library-set similarity channels:

- `score_jaccard`: symmetric baseline `|A∩B| / |A∪B|`
- `score_tversky_asym_ab`: `T(A, B)` with `alpha=0.9`, `beta=0.1`
- `score_tversky_asym_ba`: `T(A, B)` with `alpha=0.1`, `beta=0.9`
- `score_overlap`: symmetric Szymkiewicz-Simpson overlap `|A∩B| / min(|A|, |B|)`

The legacy `jaccard` key is preserved unchanged for backward compatibility.

Motivation: when one detected library set is mostly contained in the other,
Jaccard may stay modest while `score_overlap` and the asymmetric Tversky
channels expose piggybacking-style inclusion more clearly.
