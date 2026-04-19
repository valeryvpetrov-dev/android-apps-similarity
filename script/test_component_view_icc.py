#!/usr/bin/env python3
"""EXEC-085 tests for ICC tuple extraction and similarity.

Covers:
    1. ``extract_icc_tuples`` returns a non-empty list when the manifest has
       at least one intent-filter.
    2. Every produced ICC tuple has all 8 positional fields in the expected
       order (``src_role, action, category, data_scheme, data_host,
       data_mime, exported, priority_bucket``).
    3. Cartesian product over multiple ``<action>`` × ``<category>`` inside
       one intent-filter (2 × 2 = 4 tuples).
    4. ``priority_bucket`` classification (0 → default, >0 → high,
       <0 → low).
    5. ``compare_icc`` on identical tuple sets returns ``icc_jaccard_score``
       of 1.0.
    6. ``compare_icc`` on disjoint tuple sets returns
       ``icc_jaccard_score`` of 0.0.
    7. ``compare_components`` result contains the three required score
       fields: ``component_jaccard_score``, ``icc_jaccard_score``,
       ``combined_component_icc_score``.
"""
from __future__ import annotations

import os
import tempfile
import unittest

from component_view import (
    ICC_TUPLE_FIELDS,
    compare_components,
    compare_icc,
    component_explanation_hints,
    extract_component_features,
    extract_icc_tuples,
)

# ---------------------------------------------------------------------------
# Synthetic manifests
# ---------------------------------------------------------------------------

# Minimal manifest with a single LAUNCHER activity filter.
MANIFEST_LAUNCHER = """\
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.launcher">
    <application>
        <activity android:name=".MainActivity" android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
"""

# Manifest with 2 actions × 2 categories inside a single intent-filter.
MANIFEST_CARTESIAN = """\
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.cart">
    <application>
        <activity android:name=".ShareActivity" android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.SEND" />
                <action android:name="android.intent.action.SEND_MULTIPLE" />
                <category android:name="android.intent.category.DEFAULT" />
                <category android:name="android.intent.category.BROWSABLE" />
            </intent-filter>
        </activity>
    </application>
</manifest>
"""

# Manifest with three receivers covering default / high / low priority.
MANIFEST_PRIORITIES = """\
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.prio">
    <application>
        <receiver android:name=".DefaultReceiver">
            <intent-filter>
                <action android:name="com.example.ACTION_DEFAULT" />
            </intent-filter>
        </receiver>
        <receiver android:name=".HighReceiver">
            <intent-filter android:priority="2147483647">
                <action android:name="com.example.ACTION_HIGH" />
            </intent-filter>
        </receiver>
        <receiver android:name=".LowReceiver">
            <intent-filter android:priority="-100">
                <action android:name="com.example.ACTION_LOW" />
            </intent-filter>
        </receiver>
    </application>
</manifest>
"""

# Manifest with DIFFERENT actions (disjoint from the LAUNCHER one above).
MANIFEST_DISJOINT = """\
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.disjoint">
    <application>
        <service android:name=".SyncService" android:exported="false">
            <intent-filter>
                <action android:name="com.example.SYNC" />
                <category android:name="com.example.CATEGORY_SYNC" />
            </intent-filter>
        </service>
    </application>
</manifest>
"""

# Manifest without <application> - should produce zero tuples.
MANIFEST_EMPTY = """\
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.empty">
</manifest>
"""


def _write_manifest(tmpdir: str, content: str) -> str:
    path = os.path.join(tmpdir, "AndroidManifest.xml")
    with open(path, "w") as f:
        f.write(content)
    return tmpdir


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestExtractIccTuples(unittest.TestCase):

    def test_nonempty_for_filtered_manifest(self):
        """1. A manifest with an intent-filter yields at least one tuple."""
        with tempfile.TemporaryDirectory() as d:
            _write_manifest(d, MANIFEST_LAUNCHER)
            feat = extract_component_features(d)
        tuples = extract_icc_tuples(feat)
        self.assertGreater(len(tuples), 0)
        self.assertEqual(len(tuples), 1)

    def test_tuple_has_all_8_fields_in_order(self):
        """2. Every tuple has 8 fields in the canonical order."""
        with tempfile.TemporaryDirectory() as d:
            _write_manifest(d, MANIFEST_LAUNCHER)
            feat = extract_component_features(d)
        tuples = extract_icc_tuples(feat)
        self.assertEqual(len(ICC_TUPLE_FIELDS), 8)
        for t in tuples:
            self.assertEqual(len(t), 8)
        (src_role, action, category, data_scheme,
         data_host, data_mime, exported, priority_bucket) = tuples[0]
        self.assertEqual(src_role, "activity")
        self.assertEqual(action, "android.intent.action.MAIN")
        self.assertEqual(category, "android.intent.category.LAUNCHER")
        self.assertEqual(data_scheme, "")
        self.assertEqual(data_host, "")
        self.assertEqual(data_mime, "")
        self.assertIs(exported, True)
        self.assertEqual(priority_bucket, "default")

    def test_cartesian_product_of_actions_and_categories(self):
        """3. 2 actions × 2 categories produces 4 tuples."""
        with tempfile.TemporaryDirectory() as d:
            _write_manifest(d, MANIFEST_CARTESIAN)
            feat = extract_component_features(d)
        tuples = extract_icc_tuples(feat)
        self.assertEqual(len(tuples), 4)
        # All four combinations present.
        actions = {t[1] for t in tuples}
        categories = {t[2] for t in tuples}
        self.assertEqual(actions, {
            "android.intent.action.SEND",
            "android.intent.action.SEND_MULTIPLE",
        })
        self.assertEqual(categories, {
            "android.intent.category.DEFAULT",
            "android.intent.category.BROWSABLE",
        })
        # Verify the Cartesian product coverage.
        pairs = {(t[1], t[2]) for t in tuples}
        self.assertEqual(len(pairs), 4)

    def test_priority_bucket_classification(self):
        """4. priority 0 -> default, >0 -> high, <0 -> low."""
        with tempfile.TemporaryDirectory() as d:
            _write_manifest(d, MANIFEST_PRIORITIES)
            feat = extract_component_features(d)
        tuples = extract_icc_tuples(feat)
        by_action = {t[1]: t[7] for t in tuples}
        self.assertEqual(by_action["com.example.ACTION_DEFAULT"], "default")
        self.assertEqual(by_action["com.example.ACTION_HIGH"], "high")
        self.assertEqual(by_action["com.example.ACTION_LOW"], "low")


class TestCompareIcc(unittest.TestCase):

    def test_identical_sets_score_1(self):
        """5. Identical tuple sets give icc_jaccard_score = 1.0."""
        with tempfile.TemporaryDirectory() as d:
            _write_manifest(d, MANIFEST_CARTESIAN)
            feat = extract_component_features(d)
        tuples = extract_icc_tuples(feat)
        result = compare_icc(tuples, tuples)
        self.assertEqual(result["icc_jaccard_score"], 1.0)
        self.assertEqual(result["matched"], len(set(tuples)))
        self.assertEqual(result["union"], len(set(tuples)))

    def test_disjoint_sets_score_0(self):
        """6. Disjoint tuple sets give icc_jaccard_score = 0.0."""
        with tempfile.TemporaryDirectory() as d1:
            _write_manifest(d1, MANIFEST_LAUNCHER)
            feat_a = extract_component_features(d1)
        with tempfile.TemporaryDirectory() as d2:
            _write_manifest(d2, MANIFEST_DISJOINT)
            feat_b = extract_component_features(d2)
        t_a = extract_icc_tuples(feat_a)
        t_b = extract_icc_tuples(feat_b)
        # Sanity: both sides have content but they don't share a tuple.
        self.assertGreater(len(t_a), 0)
        self.assertGreater(len(t_b), 0)
        self.assertEqual(set(t_a) & set(t_b), set())
        result = compare_icc(t_a, t_b)
        self.assertEqual(result["icc_jaccard_score"], 0.0)
        self.assertEqual(result["matched"], 0)
        self.assertEqual(result["union"], len(set(t_a)) + len(set(t_b)))

    def test_both_empty_score_1(self):
        """Edge case: two empty inputs give score 1.0 with zero counts."""
        result = compare_icc([], [])
        self.assertEqual(result["icc_jaccard_score"], 1.0)
        self.assertEqual(result["matched"], 0)
        self.assertEqual(result["union"], 0)


class TestCompareComponentsIccIntegration(unittest.TestCase):

    def test_result_has_all_three_score_fields(self):
        """7. compare_components returns component_jaccard_score,
        icc_jaccard_score, combined_component_icc_score."""
        with tempfile.TemporaryDirectory() as d1:
            _write_manifest(d1, MANIFEST_LAUNCHER)
            feat_a = extract_component_features(d1)
        with tempfile.TemporaryDirectory() as d2:
            _write_manifest(d2, MANIFEST_DISJOINT)
            feat_b = extract_component_features(d2)
        cmp = compare_components(feat_a, feat_b)
        self.assertIn("component_jaccard_score", cmp)
        self.assertIn("icc_jaccard_score", cmp)
        self.assertIn("combined_component_icc_score", cmp)
        # combined = mean of the two primary scores.
        expected = (
            cmp["component_jaccard_score"] + cmp["icc_jaccard_score"]
        ) / 2
        self.assertAlmostEqual(
            cmp["combined_component_icc_score"], expected, places=10,
        )

    def test_identical_apps_icc_score_1(self):
        with tempfile.TemporaryDirectory() as d:
            _write_manifest(d, MANIFEST_CARTESIAN)
            feat = extract_component_features(d)
        cmp = compare_components(feat, feat)
        self.assertAlmostEqual(cmp["icc_jaccard_score"], 1.0, places=10)
        self.assertAlmostEqual(
            cmp["combined_component_icc_score"], 1.0, places=10,
        )

    def test_icc_hint_emitted_when_tuples_differ(self):
        """component_explanation_hints emits an icc_overlap hint when the
        two sides have ICC tuples and differ at the ICC level."""
        with tempfile.TemporaryDirectory() as d1:
            _write_manifest(d1, MANIFEST_LAUNCHER)
            feat_a = extract_component_features(d1)
        with tempfile.TemporaryDirectory() as d2:
            _write_manifest(d2, MANIFEST_DISJOINT)
            feat_b = extract_component_features(d2)
        cmp = compare_components(feat_a, feat_b)
        hints = component_explanation_hints(cmp)
        icc_hints = [h for h in hints if h.get("subtype") == "icc_overlap"]
        self.assertEqual(len(icc_hints), 1)
        self.assertIn("icc_jaccard_score", icc_hints[0])
        self.assertAlmostEqual(
            icc_hints[0]["icc_jaccard_score"],
            cmp["icc_jaccard_score"],
            places=10,
        )

    def test_no_icc_hint_when_no_tuples(self):
        """Manifest without filters -> no icc_overlap hint."""
        with tempfile.TemporaryDirectory() as d1:
            _write_manifest(d1, MANIFEST_EMPTY)
            feat_a = extract_component_features(d1)
        with tempfile.TemporaryDirectory() as d2:
            _write_manifest(d2, MANIFEST_EMPTY)
            feat_b = extract_component_features(d2)
        cmp = compare_components(feat_a, feat_b)
        hints = component_explanation_hints(cmp)
        icc_hints = [h for h in hints if h.get("subtype") == "icc_overlap"]
        self.assertEqual(len(icc_hints), 0)


if __name__ == "__main__":
    unittest.main()
