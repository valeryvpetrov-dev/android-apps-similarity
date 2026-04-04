#!/usr/bin/env python3
"""Tests for component_view (BOR-003 CPlugin).

Uses synthetic AndroidManifest.xml files in temporary directories.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from component_view import (
    compare_components,
    component_explanation_hints,
    extract_component_features,
)

MANIFEST_A = """\
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.appA">

    <uses-sdk android:minSdkVersion="21" android:targetSdkVersion="33" />

    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.CAMERA" />

    <uses-feature android:name="android.hardware.camera" />

    <application>
        <activity android:name=".MainActivity">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
        <activity android:name=".SettingsActivity" />

        <service android:name=".SyncService" />

        <receiver android:name=".BootReceiver">
            <intent-filter>
                <action android:name="android.intent.action.BOOT_COMPLETED" />
            </intent-filter>
        </receiver>

        <provider
            android:name=".DataProvider"
            android:authorities="com.example.appA.provider" />
    </application>
</manifest>
"""

MANIFEST_B = """\
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.appB">

    <uses-sdk android:minSdkVersion="23" android:targetSdkVersion="34" />

    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.ACCESS_FINE_LOCATION" />

    <uses-feature android:name="android.hardware.camera" />
    <uses-feature android:name="android.hardware.location.gps" />

    <application>
        <activity android:name=".MainActivity">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
        <activity android:name=".ProfileActivity" />

        <service android:name=".SyncService" />
        <service android:name=".LocationService" />

        <receiver android:name=".BootReceiver" />

        <provider
            android:name=".DataProvider"
            android:authorities="com.example.appB.provider" />
        <provider
            android:name=".FileProvider"
            android:authorities="com.example.appB.fileprovider" />
    </application>
</manifest>
"""

# Minimal manifest with no application element.
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


class TestExtractComponentFeatures(unittest.TestCase):

    def test_basic_extraction_a(self):
        with tempfile.TemporaryDirectory() as d:
            _write_manifest(d, MANIFEST_A)
            feat = extract_component_features(d)

        self.assertEqual(feat["package"], "com.example.appA")
        self.assertEqual(feat["min_sdk"], 21)
        self.assertEqual(feat["target_sdk"], 33)

        activity_names = {a["name"] for a in feat["activities"]}
        self.assertEqual(activity_names, {".MainActivity", ".SettingsActivity"})

        service_names = {s["name"] for s in feat["services"]}
        self.assertEqual(service_names, {".SyncService"})

        receiver_names = {r["name"] for r in feat["receivers"]}
        self.assertEqual(receiver_names, {".BootReceiver"})

        provider_names = {p["name"] for p in feat["providers"]}
        self.assertEqual(provider_names, {".DataProvider"})

        self.assertIn("android.permission.INTERNET", feat["permissions"])
        self.assertIn("android.permission.CAMERA", feat["permissions"])
        self.assertEqual(len(feat["permissions"]), 2)

        self.assertIn("android.hardware.camera", feat["features"])
        self.assertEqual(len(feat["features"]), 1)

    def test_intent_filters_parsed(self):
        with tempfile.TemporaryDirectory() as d:
            _write_manifest(d, MANIFEST_A)
            feat = extract_component_features(d)

        main_activity = next(
            a for a in feat["activities"] if a["name"] == ".MainActivity"
        )
        self.assertIn("intent_filters", main_activity)
        filters = main_activity["intent_filters"]
        self.assertEqual(len(filters), 1)
        self.assertIn("android.intent.action.MAIN", filters[0]["action"])
        self.assertIn("android.intent.category.LAUNCHER", filters[0]["category"])

    def test_provider_authorities(self):
        with tempfile.TemporaryDirectory() as d:
            _write_manifest(d, MANIFEST_A)
            feat = extract_component_features(d)

        provider = feat["providers"][0]
        self.assertEqual(provider["authorities"], "com.example.appA.provider")

    def test_empty_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            _write_manifest(d, MANIFEST_EMPTY)
            feat = extract_component_features(d)

        self.assertEqual(feat["package"], "com.example.empty")
        self.assertIsNone(feat["min_sdk"])
        self.assertIsNone(feat["target_sdk"])
        self.assertEqual(feat["activities"], [])
        self.assertEqual(feat["services"], [])
        self.assertEqual(feat["receivers"], [])
        self.assertEqual(feat["providers"], [])
        self.assertEqual(len(feat["permissions"]), 0)
        self.assertEqual(len(feat["features"]), 0)


class TestCompareComponents(unittest.TestCase):

    def setUp(self):
        self.dir_a = tempfile.mkdtemp()
        self.dir_b = tempfile.mkdtemp()
        _write_manifest(self.dir_a, MANIFEST_A)
        _write_manifest(self.dir_b, MANIFEST_B)
        self.feat_a = extract_component_features(self.dir_a)
        self.feat_b = extract_component_features(self.dir_b)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir_a, ignore_errors=True)
        shutil.rmtree(self.dir_b, ignore_errors=True)

    def test_aggregate_score_range(self):
        cmp = compare_components(self.feat_a, self.feat_b)
        score = cmp["component_jaccard_score"]
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_activity_jaccard(self):
        """A has {Main, Settings}, B has {Main, Profile} => J = 1/3."""
        cmp = compare_components(self.feat_a, self.feat_b)
        act_j = cmp["per_type"]["activities"]["jaccard"]
        self.assertAlmostEqual(act_j, 1 / 3, places=5)

    def test_service_jaccard(self):
        """A has {Sync}, B has {Sync, Location} => J = 1/2."""
        cmp = compare_components(self.feat_a, self.feat_b)
        svc_j = cmp["per_type"]["services"]["jaccard"]
        self.assertAlmostEqual(svc_j, 0.5, places=5)

    def test_receiver_jaccard(self):
        """Both have {BootReceiver} => J = 1.0."""
        cmp = compare_components(self.feat_a, self.feat_b)
        rcv_j = cmp["per_type"]["receivers"]["jaccard"]
        self.assertAlmostEqual(rcv_j, 1.0, places=5)

    def test_provider_jaccard(self):
        """A has {DataProvider}, B has {DataProvider, FileProvider} => J = 1/2."""
        cmp = compare_components(self.feat_a, self.feat_b)
        prv_j = cmp["per_type"]["providers"]["jaccard"]
        self.assertAlmostEqual(prv_j, 0.5, places=5)

    def test_permission_jaccard(self):
        """A={INTERNET, CAMERA}, B={INTERNET, LOCATION} => J = 1/3."""
        cmp = compare_components(self.feat_a, self.feat_b)
        perm_j = cmp["per_type"]["permissions"]["jaccard"]
        self.assertAlmostEqual(perm_j, 1 / 3, places=5)

    def test_diff_added_removed(self):
        cmp = compare_components(self.feat_a, self.feat_b)
        act_diff = cmp["per_type"]["activities"]["diff"]
        self.assertIn(".MainActivity", act_diff["shared"])
        self.assertIn(".ProfileActivity", act_diff["added"])
        self.assertIn(".SettingsActivity", act_diff["removed"])

    def test_identical_comparison(self):
        cmp = compare_components(self.feat_a, self.feat_a)
        self.assertAlmostEqual(cmp["component_jaccard_score"], 1.0, places=5)
        for key in ("activities", "services", "receivers", "providers", "permissions"):
            self.assertAlmostEqual(
                cmp["per_type"][key]["jaccard"], 1.0, places=5,
            )

    def test_empty_vs_populated(self):
        with tempfile.TemporaryDirectory() as d:
            _write_manifest(d, MANIFEST_EMPTY)
            feat_empty = extract_component_features(d)

        cmp = compare_components(feat_empty, self.feat_a)
        # Activities: 0/2 = 0.0, but both empty sets => 1.0 for receivers etc.
        self.assertAlmostEqual(
            cmp["per_type"]["activities"]["jaccard"], 0.0, places=5,
        )

    def test_weighted_aggregate_manual(self):
        """Verify aggregate matches manual weighted sum."""
        cmp = compare_components(self.feat_a, self.feat_b)
        expected = (
            0.4 * cmp["per_type"]["activities"]["jaccard"]
            + 0.2 * cmp["per_type"]["services"]["jaccard"]
            + 0.2 * cmp["per_type"]["receivers"]["jaccard"]
            + 0.1 * cmp["per_type"]["providers"]["jaccard"]
            + 0.1 * cmp["per_type"]["permissions"]["jaccard"]
        )
        self.assertAlmostEqual(
            cmp["component_jaccard_score"], expected, places=10,
        )


class TestExplanationHints(unittest.TestCase):

    def setUp(self):
        self.dir_a = tempfile.mkdtemp()
        self.dir_b = tempfile.mkdtemp()
        _write_manifest(self.dir_a, MANIFEST_A)
        _write_manifest(self.dir_b, MANIFEST_B)
        feat_a = extract_component_features(self.dir_a)
        feat_b = extract_component_features(self.dir_b)
        self.cmp = compare_components(feat_a, feat_b)
        self.hints = component_explanation_hints(self.cmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir_a, ignore_errors=True)
        shutil.rmtree(self.dir_b, ignore_errors=True)

    def test_hints_non_empty(self):
        self.assertGreater(len(self.hints), 0)

    def test_all_hints_have_required_keys(self):
        for hint in self.hints:
            self.assertIn("type", hint)
            self.assertIn("subtype", hint)
            self.assertIn("component_type", hint)
            self.assertIn("name", hint)
            self.assertEqual(hint["type"], "ComponentChange")

    def test_added_activity_present(self):
        added_activities = [
            h for h in self.hints
            if h["component_type"] == "activities" and h["subtype"] == "added"
        ]
        names = {h["name"] for h in added_activities}
        self.assertIn(".ProfileActivity", names)

    def test_removed_permission_present(self):
        removed_perms = [
            h for h in self.hints
            if h["component_type"] == "permissions" and h["subtype"] == "removed"
        ]
        names = {h["name"] for h in removed_perms}
        self.assertIn("android.permission.CAMERA", names)

    def test_no_hints_for_identical(self):
        feat_a = extract_component_features(self.dir_a)
        cmp = compare_components(feat_a, feat_a)
        hints = component_explanation_hints(cmp)
        self.assertEqual(len(hints), 0)


if __name__ == "__main__":
    unittest.main()
