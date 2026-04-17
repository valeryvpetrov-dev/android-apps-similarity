#!/usr/bin/env python3
"""LIB-002/LIB-003 — TPL detector v2 based on package-set Jaccard fingerprint.

Works with androguard >=4.0.0. Falls back to v1 prefix-match if androguard
is unavailable or APK packages are not pre-extracted.

Public interface:
  extract_apk_packages(apk_path, cache_dir) -> frozenset
  detect_tpl_in_packages(apk_packages, threshold, min_matches) -> Dict
  detect_library_like_v2(rel_path, apk_packages, threshold, min_matches) -> Optional[Tuple]

Stdlib only (no extra deps beyond androguard which is already required).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Re-use CATEGORY_LIBRARY and extract_package_path from noise_normalizer.
# Avoid circular imports by importing lazily inside functions where needed;
# at module level these are safe as noise_normalizer has no import of v2.
try:
    from noise_normalizer import CATEGORY_LIBRARY, extract_package_path
except ImportError:
    # Standalone usage: define local constants.
    CATEGORY_LIBRARY = "library_like"

    def extract_package_path(rel_path: str) -> Optional[str]:  # type: ignore[misc]
        import re
        SMALI_ROOT_RE = re.compile(r"^smali(?:_classes\d+)?$")
        parts = rel_path.split("/")
        if not parts:
            return None
        if SMALI_ROOT_RE.match(parts[0]):
            if len(parts) < 3:
                return None
            return "/".join(parts[1:-1])
        if rel_path == "AndroidManifest.xml":
            return None
        if parts[0] in {"res", "assets", "lib", "kotlin"}:
            return None
        if len(parts) > 1:
            return "/".join(parts[:-1])
        return None


# ---------------------------------------------------------------------------
# TPL Catalog v2 — 40+ library groups
# ---------------------------------------------------------------------------

TPL_CATALOG_V2: Dict[str, Dict] = {
    # --- Networking ---
    "okhttp3": {
        "category": "networking",
        "packages": frozenset({
            "okhttp3", "okhttp3.internal", "okhttp3.internal.cache",
            "okhttp3.internal.connection", "okhttp3.internal.http",
            "okhttp3.internal.io", "okhttp3.internal.platform",
            "okhttp3.logging",
        }),
    },
    "retrofit2": {
        "category": "networking",
        "packages": frozenset({
            "retrofit2", "retrofit2.adapter", "retrofit2.converter",
            "retrofit2.http", "retrofit2.internal",
        }),
    },
    "okio": {
        "category": "networking",
        "packages": frozenset({
            "okio", "okio.internal",
        }),
    },
    "ktor_client": {
        "category": "networking",
        "packages": frozenset({
            "io.ktor.client", "io.ktor.client.engine",
            "io.ktor.client.features", "io.ktor.client.plugins",
            "io.ktor.client.request", "io.ktor.client.statement",
            "io.ktor.http", "io.ktor.utils",
        }),
    },
    # --- Serialization ---
    "gson": {
        "category": "serialization",
        "packages": frozenset({
            "com.google.gson", "com.google.gson.annotations",
            "com.google.gson.internal", "com.google.gson.reflect",
            "com.google.gson.stream",
        }),
    },
    "moshi": {
        "category": "serialization",
        "packages": frozenset({
            "com.squareup.moshi", "com.squareup.moshi.adapters",
            "com.squareup.moshi.internal",
        }),
    },
    "jackson_databind": {
        "category": "serialization",
        "packages": frozenset({
            "com.fasterxml.jackson.databind",
            "com.fasterxml.jackson.core",
            "com.fasterxml.jackson.annotation",
            "com.fasterxml.jackson.databind.deser",
            "com.fasterxml.jackson.databind.ser",
        }),
    },
    "kotlinx_serialization": {
        "category": "serialization",
        "packages": frozenset({
            "kotlinx.serialization", "kotlinx.serialization.encoding",
            "kotlinx.serialization.json", "kotlinx.serialization.descriptors",
            "kotlinx.serialization.internal",
        }),
    },
    # --- Image loading ---
    "glide": {
        "category": "image",
        "packages": frozenset({
            "com.bumptech.glide", "com.bumptech.glide.load",
            "com.bumptech.glide.request", "com.bumptech.glide.manager",
            "com.bumptech.glide.module", "com.bumptech.glide.util",
        }),
    },
    "picasso": {
        "category": "image",
        "packages": frozenset({
            "com.squareup.picasso", "com.squareup.picasso3",
        }),
    },
    "fresco": {
        "category": "image",
        "packages": frozenset({
            "com.facebook.fresco", "com.facebook.drawee",
            "com.facebook.imagepipeline", "com.facebook.imageformat",
        }),
    },
    "coil": {
        "category": "image",
        "packages": frozenset({
            "coil", "coil.decode", "coil.fetch", "coil.intercept",
            "coil.memory", "coil.request", "coil.size", "coil.transform",
            "coil.util",
        }),
    },
    # --- Dependency Injection ---
    "dagger2": {
        "category": "di",
        "packages": frozenset({
            "dagger", "dagger.internal", "dagger.android",
            "dagger.android.support",
        }),
    },
    "hilt": {
        "category": "di",
        "packages": frozenset({
            "dagger.hilt", "dagger.hilt.android",
            "dagger.hilt.internal", "dagger.hilt.components",
        }),
    },
    "koin": {
        "category": "di",
        "packages": frozenset({
            "org.koin.core", "org.koin.android",
            "org.koin.androidx", "org.koin.dsl",
        }),
    },
    # --- Async/Reactive ---
    "rxjava2": {
        "category": "rx",
        "packages": frozenset({
            "io.reactivex", "io.reactivex.disposables",
            "io.reactivex.internal", "io.reactivex.observers",
            "io.reactivex.subjects", "io.reactivex.schedulers",
        }),
    },
    "rxjava3": {
        "category": "rx",
        "packages": frozenset({
            "io.reactivex.rxjava3.core", "io.reactivex.rxjava3.disposables",
            "io.reactivex.rxjava3.internal", "io.reactivex.rxjava3.observers",
            "io.reactivex.rxjava3.subjects", "io.reactivex.rxjava3.schedulers",
        }),
    },
    "kotlinx_coroutines": {
        "category": "rx",
        "packages": frozenset({
            "kotlinx.coroutines", "kotlinx.coroutines.flow",
            "kotlinx.coroutines.channels", "kotlinx.coroutines.sync",
            "kotlinx.coroutines.internal", "kotlinx.coroutines.android",
        }),
    },
    # --- UI / Animation ---
    "lottie": {
        "category": "ui",
        "packages": frozenset({
            "com.airbnb.lottie", "com.airbnb.lottie.model",
            "com.airbnb.lottie.animation", "com.airbnb.lottie.utils",
        }),
    },
    "epoxy": {
        "category": "ui",
        "packages": frozenset({
            "com.airbnb.epoxy", "com.airbnb.epoxy.preload",
        }),
    },
    "material_components": {
        "category": "ui",
        "packages": frozenset({
            "com.google.android.material",
            "com.google.android.material.button",
            "com.google.android.material.chip",
            "com.google.android.material.dialog",
            "com.google.android.material.appbar",
            "com.google.android.material.bottomnavigation",
            "com.google.android.material.bottomsheet",
            "com.google.android.material.card",
            "com.google.android.material.floatingactionbutton",
            "com.google.android.material.navigation",
            "com.google.android.material.progressindicator",
            "com.google.android.material.snackbar",
            "com.google.android.material.tabs",
            "com.google.android.material.textfield",
        }),
    },
    # --- Jetpack Compose (EXEC-075: added to fix NC-033/FPR from shared Compose code) ---
    "androidx_compose_runtime": {
        "category": "ui",
        "packages": frozenset({
            "androidx.compose.runtime",
            "androidx.compose.runtime.internal",
            "androidx.compose.runtime.snapshots",
            "androidx.compose.runtime.saveable",
        }),
    },
    "androidx_compose_ui": {
        "category": "ui",
        "packages": frozenset({
            "androidx.compose.ui",
            "androidx.compose.ui.graphics",
            "androidx.compose.ui.layout",
            "androidx.compose.ui.node",
            "androidx.compose.ui.platform",
            "androidx.compose.ui.semantics",
            "androidx.compose.ui.text",
            "androidx.compose.ui.unit",
            "androidx.compose.ui.input",
            "androidx.compose.ui.draw",
            "androidx.compose.ui.geometry",
        }),
    },
    "androidx_compose_foundation": {
        "category": "ui",
        "packages": frozenset({
            "androidx.compose.foundation",
            "androidx.compose.foundation.layout",
            "androidx.compose.foundation.lazy",
            "androidx.compose.foundation.gestures",
            "androidx.compose.foundation.shape",
            "androidx.compose.foundation.text",
        }),
    },
    "androidx_compose_material": {
        "category": "ui",
        "packages": frozenset({
            "androidx.compose.material",
            "androidx.compose.material.icons",
            "androidx.compose.material.ripple",
            "androidx.compose.material3",
            "androidx.compose.material3.tokens",
        }),
    },
    "androidx_compose_animation": {
        "category": "ui",
        "packages": frozenset({
            "androidx.compose.animation",
            "androidx.compose.animation.core",
        }),
    },
    # --- AndroidX core (EXEC-075: shared across virtually all modern apps) ---
    "androidx_core": {
        "category": "androidx_platform",
        "packages": frozenset({
            "androidx.core",
            "androidx.core.app",
            "androidx.core.content",
            "androidx.core.graphics",
            "androidx.core.os",
            "androidx.core.util",
            "androidx.core.view",
            "androidx.core.widget",
        }),
    },
    "androidx_appcompat": {
        "category": "androidx_platform",
        "packages": frozenset({
            "androidx.appcompat",
            "androidx.appcompat.app",
            "androidx.appcompat.widget",
            "androidx.appcompat.view",
            "androidx.appcompat.content",
        }),
    },
    "androidx_lifecycle": {
        "category": "androidx_platform",
        "packages": frozenset({
            "androidx.lifecycle",
            "androidx.lifecycle.viewmodel",
            "androidx.lifecycle.livedata",
            "androidx.lifecycle.runtime",
            "androidx.lifecycle.process",
        }),
    },
    "androidx_activity_fragment": {
        "category": "androidx_platform",
        "packages": frozenset({
            "androidx.activity",
            "androidx.activity.compose",
            "androidx.activity.result",
            "androidx.fragment",
            "androidx.fragment.app",
        }),
    },
    "androidx_recyclerview": {
        "category": "ui",
        "packages": frozenset({
            "androidx.recyclerview",
            "androidx.recyclerview.widget",
        }),
    },
    "androidx_workmanager": {
        "category": "androidx_platform",
        "packages": frozenset({
            "androidx.work",
            "androidx.work.impl",
            "androidx.work.impl.background",
            "androidx.work.impl.utils",
        }),
    },
    "androidx_datastore": {
        "category": "androidx_platform",
        "packages": frozenset({
            "androidx.datastore",
            "androidx.datastore.preferences",
            "androidx.datastore.core",
        }),
    },
    # --- AndroidX Media3 (EXEC-075: modern ExoPlayer, covers NC-034 audio-libs case) ---
    "androidx_media3": {
        "category": "media",
        "packages": frozenset({
            "androidx.media3",
            "androidx.media3.common",
            "androidx.media3.exoplayer",
            "androidx.media3.session",
            "androidx.media3.ui",
            "androidx.media3.extractor",
            "androidx.media3.datasource",
            "androidx.media3.decoder",
        }),
    },
    # --- Platform / Kotlin ---
    "kotlin_stdlib": {
        "category": "kotlin_platform",
        "packages": frozenset({
            "kotlin", "kotlin.collections", "kotlin.io",
            "kotlin.jvm", "kotlin.ranges", "kotlin.sequences",
            "kotlin.text", "kotlin.reflect", "kotlin.coroutines",
        }),
    },
    "kotlinx_stdlib": {
        "category": "kotlin_platform",
        "packages": frozenset({
            "kotlinx", "kotlinx.collections", "kotlinx.collections.immutable",
        }),
    },
    # --- Google / Firebase ---
    "firebase_core": {
        "category": "google",
        "packages": frozenset({
            "com.google.firebase", "com.google.firebase.components",
            "com.google.firebase.platforminfo", "com.google.firebase.heartbeatinfo",
        }),
    },
    "firebase_analytics": {
        "category": "analytics",
        "packages": frozenset({
            "com.google.firebase.analytics",
            "com.google.firebase.analytics.connector",
        }),
    },
    "firebase_crashlytics": {
        "category": "crash_reporting",
        "packages": frozenset({
            "com.google.firebase.crashlytics",
            "com.google.firebase.crashlytics.internal",
            "com.crashlytics.android", "io.fabric.sdk",
        }),
    },
    "firebase_messaging": {
        "category": "messaging",
        "packages": frozenset({
            "com.google.firebase.messaging",
            "com.google.firebase.messaging.internal",
        }),
    },
    "gms_base": {
        "category": "google",
        "packages": frozenset({
            "com.google.android.gms.common", "com.google.android.gms.tasks",
            "com.google.android.gms.internal", "com.google.android.gms.auth",
        }),
    },
    # --- Crash / Monitoring ---
    "sentry": {
        "category": "crash_reporting",
        "packages": frozenset({
            "io.sentry", "io.sentry.android", "io.sentry.protocol",
            "io.sentry.transport", "io.sentry.util",
        }),
    },
    "bugsnag": {
        "category": "crash_reporting",
        "packages": frozenset({
            "com.bugsnag.android", "com.bugsnag.android.internal",
        }),
    },
    "appcenter": {
        "category": "crash_reporting",
        "packages": frozenset({
            "com.microsoft.appcenter", "com.microsoft.appcenter.crashes",
            "com.microsoft.appcenter.analytics",
        }),
    },
    # --- Database ---
    "room": {
        "category": "database",
        "packages": frozenset({
            "androidx.room", "androidx.room.paging",
        }),
    },
    "realm": {
        "category": "database",
        "packages": frozenset({
            "io.realm", "io.realm.internal", "io.realm.annotations",
        }),
    },
    # --- Logging ---
    "timber": {
        "category": "logging",
        "packages": frozenset({
            "timber.log", "com.jakewharton.timber",
        }),
    },
    # --- Media ---
    "exoplayer2": {
        "category": "media",
        "packages": frozenset({
            "com.google.android.exoplayer2",
            "com.google.android.exoplayer2.source",
            "com.google.android.exoplayer2.ui",
            "com.google.android.exoplayer2.trackselection",
        }),
    },
    # --- Navigation ---
    "androidx_navigation": {
        "category": "navigation",
        "packages": frozenset({
            "androidx.navigation", "androidx.navigation.fragment",
            "androidx.navigation.ui",
        }),
    },
    # --- Ads ---
    "admob": {
        "category": "ads",
        "packages": frozenset({
            "com.google.android.gms.ads", "com.google.ads",
        }),
    },
    "facebook_ads": {
        "category": "ads",
        "packages": frozenset({
            "com.facebook.ads", "com.facebook.ads.internal",
        }),
    },
    # --- Crypto ---
    "bouncycastle": {
        "category": "crypto",
        "packages": frozenset({
            "org.bouncycastle.crypto", "org.bouncycastle.jce",
            "org.bouncycastle.asn1", "org.bouncycastle.util",
        }),
    },
    # --- Apache Commons ---
    "apache_commons": {
        "category": "apache_commons",
        "packages": frozenset({
            "org.apache.commons.lang", "org.apache.commons.io",
            "org.apache.commons.collections", "org.apache.commons.codec",
            "org.apache.commons.logging",
        }),
    },
    # --- Leak Detection ---
    "leakcanary": {
        "category": "leak_detection",
        "packages": frozenset({
            "leakcanary", "shark", "com.squareup.leakcanary",
        }),
    },
    # --- Architecture patterns ---
    "eventbus": {
        "category": "arch_patterns",
        "packages": frozenset({
            "org.greenrobot.eventbus", "org.greenrobot.eventbus.android",
        }),
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _smali_class_to_package(smali_name: str) -> Optional[str]:
    """Convert smali class descriptor to dotted package path.

    'Lcom/example/foo/Bar;' -> 'com.example.foo'
    'Lcom/example/Bar;'     -> 'com.example'
    'LBar;'                 -> None  (default package, skip)
    """
    if not smali_name.startswith("L") or not smali_name.endswith(";"):
        return None
    inner = smali_name[1:-1]  # strip L and ;
    parts = inner.split("/")
    if len(parts) < 2:
        return None  # default package
    package_parts = parts[:-1]  # drop class name
    return ".".join(package_parts)


def _sha256_apk(apk_path: str) -> str:
    """Compute SHA-256 of APK file (first 64 KB for large files, full for <10 MB)."""
    path = Path(apk_path)
    file_size = path.stat().st_size
    h = hashlib.sha256()
    with open(apk_path, "rb") as f:
        if file_size < 10 * 1024 * 1024:
            h.update(f.read())
        else:
            h.update(f.read(64 * 1024))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Core public API
# ---------------------------------------------------------------------------

def extract_apk_packages(
    apk_path: str,
    cache_dir: Optional[str] = None,
) -> frozenset:
    """Extract all dotted package paths from APK via androguard 4.x.

    Args:
        apk_path: Absolute path to .apk file.
        cache_dir: If given, cache result as JSON to
                   <cache_dir>/<apk_sha256>_packages.json.
                   On subsequent calls with same APK, load from cache.

    Returns:
        frozenset of dotted package strings, e.g.
        frozenset({'okhttp3', 'okhttp3.internal', 'com.example.app', ...})

    Raises:
        FileNotFoundError: if apk_path does not exist.
        RuntimeError: if androguard fails to parse APK.
    """
    if not Path(apk_path).exists():
        raise FileNotFoundError("APK file does not exist: {}".format(apk_path))

    # Cache lookup
    sha256 = _sha256_apk(apk_path)
    if cache_dir is not None:
        cache_file = Path(cache_dir) / "{}_packages.json".format(sha256)
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                return frozenset(data["packages"])
            except (KeyError, json.JSONDecodeError, OSError):
                pass  # corrupt cache — re-extract

    # Extract via androguard. Supports both 4.x and 3.x APIs.
    packages: set = set()
    _extracted = False

    # Try androguard 4.x first
    try:
        from androguard.core.apk import APK  # type: ignore[import]
        from androguard.core.dex import DEX  # type: ignore[import]
        try:
            apk_obj = APK(apk_path)
        except Exception as exc:
            raise RuntimeError("androguard 4.x failed to parse APK: {}".format(exc)) from exc

        try:
            dex_list = apk_obj.get_all_dex()
        except Exception as exc:
            raise RuntimeError("Failed to read DEX files from APK: {}".format(exc)) from exc

        for dex_bytes in dex_list:
            try:
                dex = DEX(dex_bytes)
                for cls in dex.get_classes():
                    pkg = _smali_class_to_package(cls.get_name())
                    if pkg:
                        packages.add(pkg)
            except Exception:
                continue
        _extracted = True
    except ImportError:
        pass  # fall through to 3.x

    # Try androguard 3.x fallback
    if not _extracted:
        try:
            from androguard.misc import AnalyzeAPK  # type: ignore[import]
            try:
                _a, d_list, _dx = AnalyzeAPK(apk_path)
            except Exception as exc:
                raise RuntimeError("androguard 3.x failed to parse APK: {}".format(exc)) from exc

            for dex_obj in d_list:
                try:
                    for cls in dex_obj.get_classes():
                        pkg = _smali_class_to_package(cls.get_name())
                        if pkg:
                            packages.add(pkg)
                except Exception:
                    continue
            _extracted = True
        except ImportError:
            pass

    if not _extracted:
        raise RuntimeError(
            "androguard is not installed. "
            "Install with: pip install androguard>=3.3.5"
        )

    result = frozenset(packages)

    # Write cache
    if cache_dir is not None:
        try:
            cache_path = Path(cache_dir)
            cache_path.mkdir(parents=True, exist_ok=True)
            cache_file = cache_path / "{}_packages.json".format(sha256)
            payload = {"apk_sha256": sha256, "packages": sorted(packages)}
            cache_file.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass  # caching is best-effort

    return result


def detect_tpl_in_packages(
    apk_packages: frozenset,
    threshold: float = 0.30,
    min_matches: int = 1,
) -> Dict[str, Dict]:
    """Detect TPL presence via package-set Jaccard fingerprint.

    For each TPL in TPL_CATALOG_V2, computes:
        coverage = |apk_packages ∩ tpl_packages| / |tpl_packages|

    If coverage >= threshold AND matched_count >= min_matches -> TPL detected.

    Args:
        apk_packages: frozenset of dotted package paths from APK.
        threshold: Jaccard coverage threshold (default 0.30).
        min_matches: minimum number of matching packages (default 1).

    Returns:
        Dict keyed by tpl_id. Only TPLs with coverage > 0 are included.
        Each entry:
        {
            "coverage": float,
            "matched_packages": [str, ...],
            "category": str,
            "detected": bool,
        }
    """
    results: Dict[str, Dict] = {}
    for tpl_id, tpl_meta in TPL_CATALOG_V2.items():
        tpl_pkgs: frozenset = tpl_meta["packages"]
        matched = apk_packages & tpl_pkgs
        if not matched:
            continue
        coverage = len(matched) / len(tpl_pkgs)
        detected = len(matched) >= min_matches and coverage >= threshold
        results[tpl_id] = {
            "coverage": coverage,
            "matched_packages": sorted(matched),
            "category": tpl_meta["category"],
            "detected": detected,
        }
    return results


def detect_library_like_v2(
    rel_path: str,
    apk_packages: Optional[frozenset] = None,
    tpl_detections: Optional[Dict] = None,
    threshold: float = 0.30,
    min_matches: int = 1,
) -> Optional[Tuple[str, str]]:
    """Drop-in replacement for noise_normalizer.detect_library_like().

    Args:
        rel_path: relative file path inside unpacked APK (same as v1).
        apk_packages: pre-extracted APK package set from extract_apk_packages().
                      If None -> fallback to v1 prefix-match logic.
        tpl_detections: pre-computed result from detect_tpl_in_packages().
                        If provided, used directly (avoids recomputation).
                        If None but apk_packages given, computed internally.
        threshold: Jaccard coverage threshold.
        min_matches: minimum matching packages to confirm detection.

    Returns:
        (CATEGORY_LIBRARY, reason_str) or None.
        reason_str format: "v2:tpl_id(coverage=0.45)" or "v1_fallback:prefix"

    Note:
        For best performance, pre-compute tpl_detections once per APK and
        pass it to every call. Avoid letting this function call
        detect_tpl_in_packages() on every file.
    """
    package_path = extract_package_path(rel_path)
    if package_path is None:
        return None

    # Fallback to v1 when no APK packages available
    if apk_packages is None:
        try:
            from noise_normalizer import detect_library_like as _v1_detect
            result = _v1_detect(rel_path)
            if result is not None:
                return (result[0], result[1] + " [v1_fallback]")
        except ImportError:
            pass
        return None

    # Use pre-computed detections or compute on demand (not recommended per-file)
    if tpl_detections is None:
        tpl_detections = detect_tpl_in_packages(apk_packages, threshold, min_matches)

    package_dotted = package_path.replace("/", ".")

    best_tpl: Optional[str] = None
    best_coverage: float = 0.0

    for tpl_id, info in tpl_detections.items():
        if not info["detected"]:
            continue
        for pkg in info["matched_packages"]:
            if package_dotted == pkg or package_dotted.startswith(pkg + "."):
                if info["coverage"] > best_coverage:
                    best_tpl = tpl_id
                    best_coverage = info["coverage"]

    if best_tpl is not None:
        return (CATEGORY_LIBRARY, "v2:{}(coverage={:.2f})".format(best_tpl, best_coverage))


# ---------------------------------------------------------------------------
# Compatibility API (drop-in for library_view.py v1)
# ---------------------------------------------------------------------------

def extract_library_features_v2(apk_path: str, cache_dir: Optional[str] = None) -> Dict:
    """Compat wrapper: same output shape as library_view.extract_library_features().

    Works from raw APK file (not unpacked dir) via androguard.
    Returns dict with keys: libraries, library_ratio, total_packages.
    """
    packages = extract_apk_packages(apk_path, cache_dir=cache_dir)
    tpl_hits = detect_tpl_in_packages(packages)
    detected = {k: v for k, v in tpl_hits.items() if v["detected"]}
    libraries = {
        tpl_id: {
            "coverage": info["coverage"],
            "matched_packages": info["matched_packages"],
            "category": info["category"],
        }
        for tpl_id, info in detected.items()
    }
    return {
        "libraries": libraries,
        "app_packages": packages,
        "library_ratio": len(detected) / max(len(tpl_hits), 1),
        "total_packages": len(packages),
        "v2": True,
    }


def compare_libraries_v2(features_a: Dict, features_b: Dict) -> Dict:
    """Compat wrapper: compare two v2 feature dicts.

    Returns Jaccard on detected TPL sets + shared/only_a/only_b.
    """
    libs_a = set(features_a.get("libraries", {}).keys())
    libs_b = set(features_b.get("libraries", {}).keys())
    shared = libs_a & libs_b
    union = libs_a | libs_b
    jaccard = len(shared) / len(union) if union else 0.0
    return {
        "jaccard": jaccard,
        "shared_libraries": sorted(shared),
        "only_in_a": sorted(libs_a - libs_b),
        "only_in_b": sorted(libs_b - libs_a),
        "library_count_a": len(libs_a),
        "library_count_b": len(libs_b),
        "v2": True,
    }


def library_explanation_hints_v2(comparison: Dict) -> List[Dict]:
    """Compat wrapper: produce hint dicts from compare_libraries_v2() result."""
    hints = []
    for lib in comparison.get("only_in_b", []):
        hints.append({"type": "NewLibraryAdded", "library": lib, "source": "library_v2"})
    for lib in comparison.get("only_in_a", []):
        hints.append({"type": "LibraryRemoved", "library": lib, "source": "library_v2"})
    return hints

    return None
