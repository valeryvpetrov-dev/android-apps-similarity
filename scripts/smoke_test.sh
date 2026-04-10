#!/usr/bin/env bash
# SOTA-009 Reproducibility smoke test
# Verifies environment and runs one representative APK comparison.
#
# Usage (from project root):
#   bash scripts/smoke_test.sh
#
# Expected output:
#   PASS: androguard importable
#   PASS: tlsh importable
#   PASS: code_view_v2 hash extracted for NonOptimized APK
#   PASS: compare_code_v2 returned score in [0,1]
#   SMOKE TEST PASSED

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SCRIPT_PY_DIR="$PROJECT_ROOT/script"

APK_A="$PROJECT_ROOT/apk/simple_app/simple_app-releaseNonOptimized.apk"
APK_B="$PROJECT_ROOT/apk/snake/snake.apk"

echo "=== SOTA-009 Smoke Test ==="
echo "Project root: $PROJECT_ROOT"
echo "Python: $(python3 --version 2>&1)"
echo ""

# --- Check dependencies ---
python3 -c "import androguard; print('PASS: androguard importable (version: ' + androguard.__version__ + ')')" || {
    echo "FAIL: androguard not installed. Run: pip install androguard"
    exit 1
}

python3 -c "import tlsh; print('PASS: tlsh importable')" || {
    echo "FAIL: py-tlsh not installed. Run: pip install py-tlsh"
    exit 1
}

# --- Check APK files ---
for apk in "$APK_A" "$APK_B"; do
    if [ ! -f "$apk" ]; then
        echo "FAIL: APK not found: $apk"
        exit 1
    fi
done
echo "PASS: test APKs present"

# --- Run extraction + comparison ---
python3 - << PYEOF
import sys
sys.path.insert(0, '$SCRIPT_PY_DIR')
sys.path.insert(0, '$PROJECT_ROOT')

from pathlib import Path
try:
    from script.code_view_v2 import extract_opcode_ngram_tlsh, compare_code_v2
except ImportError:
    from code_view_v2 import extract_opcode_ngram_tlsh, compare_code_v2

apk_a = Path('$APK_A')
apk_b = Path('$APK_B')

h_a = extract_opcode_ngram_tlsh(apk_a)
if h_a is None:
    print('FAIL: hash extraction returned None for', apk_a.name)
    sys.exit(1)
print('PASS: code_view_v2 hash extracted for', apk_a.name, '(len={})'.format(len(h_a)))

h_b = extract_opcode_ngram_tlsh(apk_b)
if h_b is None:
    print('FAIL: hash extraction returned None for', apk_b.name)
    sys.exit(1)
print('PASS: code_view_v2 hash extracted for', apk_b.name)

result = compare_code_v2(h_a, h_b)
score = result['score']
if not (0.0 <= score <= 1.0):
    print('FAIL: score out of range:', score)
    sys.exit(1)
print('PASS: compare_code_v2 returned score={:.4f} status={}'.format(score, result['status']))
PYEOF

echo ""
echo "=== SMOKE TEST PASSED ==="
