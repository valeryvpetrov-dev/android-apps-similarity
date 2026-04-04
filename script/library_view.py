#!/usr/bin/env python3
"""BOR-004 Library detection layer for M_static.

Replaces the hardcoded 22-prefix list in noise_normalizer.py with a
comprehensive 150+ prefix catalog organised by domain, library
fingerprinting (package/class counts per matched prefix), Jaccard and
weighted similarity scoring, and explanation hints.

Stdlib only.  No JDK / Android SDK / LibScout profiles required.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

LIBRARY_CATALOG: Dict[str, Dict[str, List[str]]] = {
    "android_platform": {
        "description": "Android framework and support libraries",
        "prefixes": [
            "android",
            "androidx",
            "com.android",
            "android.support",
        ],
    },
    "google": {
        "description": "Google first-party libraries",
        "prefixes": [
            "com.google.android.gms",
            "com.google.android.play",
            "com.google.android.datatransport",
            "com.google.firebase",
            "com.google.protobuf",
            "com.google.common",
            "com.google.auto",
            "com.google.errorprone",
            "com.google.j2objc",
            "com.google.crypto.tink",
            "com.google.flatbuffers",
            "com.google.mlkit",
            "com.google.zxing",
            "com.google.accompanist",
            "com.google.devtools",
        ],
    },
    "networking": {
        "description": "HTTP clients and networking",
        "prefixes": [
            "okhttp3",
            "okio",
            "retrofit2",
            "com.squareup.okhttp",
            "com.squareup.okhttp3",
            "org.apache.http",
            "com.android.volley",
            "com.koushikdutta.async",
            "com.loopj.android",
            "io.ktor",
            "com.github.kittinunf.fuel",
            "cz.msebera.android",
        ],
    },
    "serialization": {
        "description": "JSON/XML/binary serialization",
        "prefixes": [
            "com.google.gson",
            "com.fasterxml.jackson",
            "org.json",
            "com.squareup.moshi",
            "kotlinx.serialization",
            "org.simpleframework.xml",
            "com.alibaba.fastjson",
            "org.msgpack",
            "com.google.code.gson",
            "org.codehaus.jackson",
            "com.jayway.jsonpath",
        ],
    },
    "image": {
        "description": "Image loading and processing",
        "prefixes": [
            "com.bumptech.glide",
            "com.squareup.picasso",
            "coil",
            "com.facebook.fresco",
            "com.facebook.drawee",
            "com.facebook.imagepipeline",
            "com.nostra13.universalimageloader",
            "jp.wasabeef",
            "com.github.chrisbanes",
            "pl.droidsonroids.gif",
            "com.caverock.androidsvg",
        ],
    },
    "di": {
        "description": "Dependency injection frameworks",
        "prefixes": [
            "dagger",
            "javax.inject",
            "com.google.inject",
            "org.koin",
            "toothpick",
            "com.google.dagger",
            "me.tatarka.inject",
            "anvil",
        ],
    },
    "rx": {
        "description": "Reactive/async frameworks",
        "prefixes": [
            "io.reactivex",
            "io.reactivex.rxjava3",
            "rx",
            "kotlinx.coroutines",
            "org.reactivestreams",
            "io.projectreactor",
            "com.uber.autodispose",
            "com.jakewharton.rxbinding",
            "com.jakewharton.rxbinding2",
            "com.jakewharton.rxbinding3",
            "com.jakewharton.rxbinding4",
            "com.jakewharton.rxrelay",
            "com.jakewharton.rxrelay2",
            "com.jakewharton.rxrelay3",
        ],
    },
    "ui": {
        "description": "UI component libraries",
        "prefixes": [
            "com.airbnb.lottie",
            "com.airbnb.epoxy",
            "com.airbnb.paris",
            "com.google.android.material",
            "androidx.compose",
            "butterknife",
            "com.github.bumptech",
            "com.hannesdorfmann",
            "com.github.PhilJay",
            "com.github.mikephil.charting",
            "com.journeyapps.barcodescanner",
            "me.relex",
            "com.scwang.smart",
            "com.github.CymChad",
            "com.chad.library",
            "de.hdodenhof",
            "com.makeramen",
            "com.nineoldandroids",
            "com.daimajia",
            "com.youth.banner",
            "com.flyco",
            "com.contrarywind",
            "com.bigkoo",
        ],
    },
    "analytics": {
        "description": "Analytics and tracking SDKs",
        "prefixes": [
            "com.google.firebase.analytics",
            "com.flurry",
            "com.mixpanel",
            "com.amplitude",
            "com.segment",
            "com.adjust.sdk",
            "com.appsflyer",
            "ly.count.android",
            "com.localytics",
            "io.branch",
            "com.mparticle",
            "com.snowplowanalytics",
            "com.yandex.metrica",
        ],
    },
    "ads": {
        "description": "Advertising SDKs",
        "prefixes": [
            "com.google.android.gms.ads",
            "com.facebook.ads",
            "com.unity3d.ads",
            "com.applovin",
            "com.mopub",
            "com.ironsource",
            "com.vungle",
            "com.chartboost",
            "com.inmobi",
            "com.adcolony",
            "com.tapjoy",
            "com.startapp",
            "com.bytedance.sdk.openadsdk",
            "com.smaato",
            "net.pubnative",
        ],
    },
    "testing": {
        "description": "Testing frameworks (usually stripped in release but may remain)",
        "prefixes": [
            "junit",
            "org.junit",
            "org.mockito",
            "org.robolectric",
            "androidx.test",
            "org.hamcrest",
            "org.assertj",
            "com.google.truth",
            "io.mockk",
            "org.powermock",
            "com.nhaarman.mockitokotlin2",
        ],
    },
    "logging": {
        "description": "Logging libraries",
        "prefixes": [
            "timber",
            "com.jakewharton.timber",
            "org.slf4j",
            "ch.qos.logback",
            "org.apache.logging",
            "com.orhanobut.logger",
            "io.github.microutils",
        ],
    },
    "database": {
        "description": "Database and ORM libraries",
        "prefixes": [
            "androidx.room",
            "io.realm",
            "org.greenrobot.greendao",
            "com.j256.ormlite",
            "androidx.sqlite",
            "net.sqlcipher",
            "com.couchbase.lite",
            "io.objectbox",
            "com.raizlabs.dbflow5",
            "com.raizlabs.android.dbflow",
            "com.github.nicbell",
        ],
    },
    "crypto": {
        "description": "Cryptography libraries",
        "prefixes": [
            "org.bouncycastle",
            "org.spongycastle",
            "javax.crypto",
            "com.google.crypto",
            "org.conscrypt",
        ],
    },
    "kotlin_platform": {
        "description": "Kotlin runtime and standard library",
        "prefixes": [
            "kotlin",
            "kotlinx",
        ],
    },
    "java_platform": {
        "description": "Java platform classes",
        "prefixes": [
            "java",
            "javax",
            "sun",
            "dalvik",
        ],
    },
    "jetbrains": {
        "description": "JetBrains annotations and utilities",
        "prefixes": [
            "org.intellij",
            "org.jetbrains",
        ],
    },
    "crash_reporting": {
        "description": "Crash and performance monitoring",
        "prefixes": [
            "com.crashlytics",
            "io.fabric",
            "com.newrelic",
            "com.datadog",
            "io.sentry",
            "com.bugsnag",
            "com.instabug",
            "com.microsoft.appcenter",
        ],
    },
    "social": {
        "description": "Social login and sharing SDKs",
        "prefixes": [
            "com.facebook",
            "com.twitter",
            "com.vk",
            "com.kakao",
            "com.linecorp",
            "jp.line.android",
        ],
    },
    "maps_location": {
        "description": "Maps and location SDKs",
        "prefixes": [
            "com.mapbox",
            "org.osmdroid",
            "com.baidu.location",
            "com.baidu.mapapi",
            "com.amap.api",
            "com.huawei.hms.maps",
        ],
    },
    "media": {
        "description": "Audio/video playback and processing",
        "prefixes": [
            "com.google.android.exoplayer",
            "com.google.android.exoplayer2",
            "com.pierfrancescosoffritti",
            "tv.danmaku.ijk",
            "org.videolan.libvlc",
            "com.danikula.videocache",
        ],
    },
    "storage_cloud": {
        "description": "Cloud storage and file transfer",
        "prefixes": [
            "com.amazonaws",
            "com.azure",
            "com.qiniu",
            "com.aliyun",
            "com.tencent.cos",
        ],
    },
    "messaging": {
        "description": "Push messaging and in-app messaging",
        "prefixes": [
            "com.google.firebase.messaging",
            "com.onesignal",
            "com.pusher",
            "io.socket",
            "com.huawei.hms.push",
            "com.xiaomi.push",
        ],
    },
    "webview_bridge": {
        "description": "WebView bridges and hybrid frameworks",
        "prefixes": [
            "org.xwalk",
            "com.pichillilorenzo.flutter_inappwebview",
            "org.chromium",
            "com.nicbell",
        ],
    },
    "permissions": {
        "description": "Runtime permission libraries",
        "prefixes": [
            "pub.devrel.easypermissions",
            "com.yanzhenjie.permission",
            "com.karumi.dexter",
            "com.tbruyelle.rxpermissions2",
            "permissions.dispatcher",
        ],
    },
    "navigation": {
        "description": "Navigation and routing",
        "prefixes": [
            "androidx.navigation",
            "com.alibaba.android.arouter",
            "cafe.adriel.voyager",
        ],
    },
    "arch_patterns": {
        "description": "Architecture pattern libraries (MVP/MVVM/MVI)",
        "prefixes": [
            "org.greenrobot.eventbus",
            "com.squareup.workflow",
            "com.airbnb.mvrx",
            "com.arkivanov.decompose",
            "com.arkivanov.mvikotlin",
            "com.badoo.reaktive",
        ],
    },
    "apache_commons": {
        "description": "Apache Commons libraries",
        "prefixes": [
            "org.apache.commons",
            "org.apache",
        ],
    },
    "huawei": {
        "description": "Huawei Mobile Services",
        "prefixes": [
            "com.huawei.hms",
            "com.huawei.agconnect",
        ],
    },
    "tencent": {
        "description": "Tencent SDKs (WeChat, QQ, Bugly, etc.)",
        "prefixes": [
            "com.tencent",
        ],
    },
    "leak_detection": {
        "description": "Memory leak detection",
        "prefixes": [
            "com.squareup.leakcanary",
            "leakcanary",
            "shark",
        ],
    },
}

# Flattened index: prefix -> (library_id, category)
# Built once at import time.  Sorted longest-prefix-first so that more
# specific prefixes match before shorter ones.
_PREFIX_INDEX: List[Tuple[str, str, str]] = []


def _build_prefix_index() -> List[Tuple[str, str, str]]:
    """Return sorted list of (prefix_dotted, library_id, category)."""
    entries: List[Tuple[str, str, str]] = []
    for category, meta in LIBRARY_CATALOG.items():
        for prefix in meta["prefixes"]:
            lib_id = prefix
            entries.append((prefix, lib_id, category))
    entries.sort(key=lambda t: len(t[0]), reverse=True)
    return entries


_PREFIX_INDEX = _build_prefix_index()

SMALI_ROOT_RE = re.compile(r"^smali(?:_classes\d+)?$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ensure_input_dir(path_str: str) -> Path:
    apk_path = Path(path_str).expanduser().resolve()
    if not apk_path.exists():
        raise FileNotFoundError("APK directory does not exist: {}".format(apk_path))
    if not apk_path.is_dir():
        raise NotADirectoryError("APK path is not a directory: {}".format(apk_path))
    return apk_path


def _package_from_smali_rel(rel_parts: List[str]) -> Optional[str]:
    """Extract dotted package name from smali-relative path parts.

    Example: ['smali', 'com', 'google', 'gson', 'Gson.smali']
             -> 'com.google.gson'
    """
    if len(rel_parts) < 3:
        return None
    if not SMALI_ROOT_RE.match(rel_parts[0]):
        return None
    # parts[1:-1] are package segments, last part is the class file
    package_segments = rel_parts[1:-1]
    if not package_segments:
        return None
    return ".".join(package_segments)


def _match_prefix(package_dotted: str) -> Optional[Tuple[str, str, str]]:
    """Match a dotted package against the catalog.

    Returns (prefix, library_id, category) or None.
    """
    for prefix, lib_id, category in _PREFIX_INDEX:
        if package_dotted == prefix or package_dotted.startswith(prefix + "."):
            return prefix, lib_id, category
    return None


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

def extract_library_features(apk_unpacked_dir: str) -> Dict:
    """Walk smali directories, fingerprint library and app packages.

    Returns::

        {
            "libraries": {
                lib_id: {
                    "prefix": str,
                    "package_count": int,
                    "class_count": int,
                    "category": str,
                }
            },
            "app_packages": set of dotted package names,
            "library_ratio": float,   # library_classes / total_classes
            "total_classes": int,
        }
    """
    apk_path = ensure_input_dir(apk_unpacked_dir)

    # Accumulators
    lib_packages: Dict[str, Set[str]] = {}     # lib_id -> set of packages
    lib_classes: Dict[str, int] = {}            # lib_id -> class count
    lib_meta: Dict[str, Tuple[str, str]] = {}   # lib_id -> (prefix, category)
    app_packages: Set[str] = set()
    total_classes = 0
    library_class_count = 0

    for entry in sorted(apk_path.rglob("*.smali")):
        if not entry.is_file():
            continue
        rel_parts = entry.relative_to(apk_path).parts
        if not rel_parts or not SMALI_ROOT_RE.match(rel_parts[0]):
            continue

        total_classes += 1
        package_dotted = _package_from_smali_rel(list(rel_parts))
        if package_dotted is None:
            continue

        match = _match_prefix(package_dotted)
        if match is not None:
            prefix, lib_id, category = match
            lib_packages.setdefault(lib_id, set()).add(package_dotted)
            lib_classes[lib_id] = lib_classes.get(lib_id, 0) + 1
            lib_meta[lib_id] = (prefix, category)
            library_class_count += 1
        else:
            app_packages.add(package_dotted)

    libraries: Dict[str, Dict] = {}
    for lib_id in sorted(lib_classes):
        prefix, category = lib_meta[lib_id]
        libraries[lib_id] = {
            "prefix": prefix,
            "package_count": len(lib_packages[lib_id]),
            "class_count": lib_classes[lib_id],
            "category": category,
        }

    library_ratio = library_class_count / total_classes if total_classes > 0 else 0.0

    return {
        "libraries": libraries,
        "app_packages": app_packages,
        "library_ratio": library_ratio,
        "total_classes": total_classes,
    }


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def compare_libraries(features_a: Dict, features_b: Dict) -> Dict:
    """Compare two library feature sets.

    Returns::

        {
            "library_jaccard_score": float,   # Jaccard on lib_id sets
            "weighted_library_score": float,  # class-count-weighted overlap
            "shared": [lib_id, ...],
            "a_only": [lib_id, ...],
            "b_only": [lib_id, ...],
            "library_ratio_a": float,
            "library_ratio_b": float,
        }
    """
    libs_a: Dict[str, Dict] = features_a["libraries"]
    libs_b: Dict[str, Dict] = features_b["libraries"]

    set_a: Set[str] = set(libs_a)
    set_b: Set[str] = set(libs_b)

    intersection = set_a & set_b
    union = set_a | set_b

    # Plain Jaccard
    if not union:
        jaccard = 1.0
    else:
        jaccard = len(intersection) / len(union)

    # Weighted overlap: for shared libs take min class_count / max class_count
    # as a per-library weight, then average across union.
    weighted_sum = 0.0
    weight_total = 0.0
    for lib_id in union:
        count_a = libs_a[lib_id]["class_count"] if lib_id in libs_a else 0
        count_b = libs_b[lib_id]["class_count"] if lib_id in libs_b else 0
        max_count = max(count_a, count_b, 1)
        weight = max_count  # importance proportional to size
        if lib_id in intersection:
            overlap = min(count_a, count_b) / max_count
        else:
            overlap = 0.0
        weighted_sum += overlap * weight
        weight_total += weight

    weighted_score = weighted_sum / weight_total if weight_total > 0.0 else 1.0

    return {
        "library_jaccard_score": jaccard,
        "weighted_library_score": weighted_score,
        "shared": sorted(intersection),
        "a_only": sorted(set_a - set_b),
        "b_only": sorted(set_b - set_a),
        "library_ratio_a": features_a["library_ratio"],
        "library_ratio_b": features_b["library_ratio"],
    }


# ---------------------------------------------------------------------------
# Explanation hints
# ---------------------------------------------------------------------------

def library_explanation_hints(comparison: Dict) -> List[Dict]:
    """Generate LibraryImpact hints from a comparison result."""
    hints: List[Dict] = []

    jaccard = comparison["library_jaccard_score"]
    weighted = comparison["weighted_library_score"]

    if jaccard < 0.5:
        hints.append({
            "type": "LibraryImpact",
            "action": "low_overlap",
            "detail": (
                "Library set Jaccard is {:.3f} -- the two APKs use "
                "substantially different third-party libraries"
            ).format(jaccard),
        })
    elif jaccard >= 0.9:
        hints.append({
            "type": "LibraryImpact",
            "action": "high_overlap",
            "detail": (
                "Library set Jaccard is {:.3f} -- nearly identical "
                "third-party stacks"
            ).format(jaccard),
        })

    if abs(jaccard - weighted) > 0.15:
        hints.append({
            "type": "LibraryImpact",
            "action": "weight_divergence",
            "detail": (
                "Weighted score ({:.3f}) diverges from Jaccard ({:.3f}); "
                "shared libraries differ significantly in class count"
            ).format(weighted, jaccard),
        })

    ratio_a = comparison["library_ratio_a"]
    ratio_b = comparison["library_ratio_b"]
    if ratio_a > 0.7 or ratio_b > 0.7:
        hints.append({
            "type": "LibraryImpact",
            "action": "library_dominated",
            "detail": (
                "One or both APKs are >70% library code "
                "(A: {:.1%}, B: {:.1%}); similarity score "
                "may be dominated by shared libraries"
            ).format(ratio_a, ratio_b),
        })

    for lib_id in comparison.get("a_only", []):
        hints.append({
            "type": "LibraryImpact",
            "action": "a_only",
            "library": lib_id,
            "detail": "Library present only in APK A",
        })

    for lib_id in comparison.get("b_only", []):
        hints.append({
            "type": "LibraryImpact",
            "action": "b_only",
            "library": lib_id,
            "detail": "Library present only in APK B",
        })

    return hints


# ---------------------------------------------------------------------------
# Catalog stats (for quick audit)
# ---------------------------------------------------------------------------

def catalog_stats() -> Dict:
    """Return summary statistics about the built-in catalog."""
    all_prefixes: Set[str] = set()
    category_counts: Dict[str, int] = {}
    for category, meta in LIBRARY_CATALOG.items():
        prefixes = meta["prefixes"]
        category_counts[category] = len(prefixes)
        all_prefixes.update(prefixes)
    return {
        "total_unique_prefixes": len(all_prefixes),
        "category_count": len(LIBRARY_CATALOG),
        "per_category": category_counts,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="library_view",
        description=(
            "BOR-004: Library detection view for M_static.  Compare two "
            "unpacked APK directories by their third-party library fingerprints."
        ),
    )
    sub = parser.add_subparsers(dest="command")

    compare_p = sub.add_parser("compare", help="Compare libraries of two APKs")
    compare_p.add_argument("apk_a", help="First unpacked APK directory")
    compare_p.add_argument("apk_b", help="Second unpacked APK directory")
    compare_p.add_argument("--output", help="Write JSON result to file")

    extract_p = sub.add_parser("extract", help="Extract library features for one APK")
    extract_p.add_argument("apk", help="Unpacked APK directory")
    extract_p.add_argument("--output", help="Write JSON result to file")

    sub.add_parser("catalog", help="Print catalog statistics")

    return parser.parse_args()


def _serialize_features(features: Dict) -> Dict:
    """Convert feature dict to JSON-safe form (sets -> sorted lists)."""
    return {
        "libraries": features["libraries"],
        "app_package_count": len(features["app_packages"]),
        "app_packages_sample": sorted(features["app_packages"])[:30],
        "library_ratio": features["library_ratio"],
        "total_classes": features["total_classes"],
    }


def write_output(payload: Dict, output_path: Optional[str]) -> None:
    payload_json = json.dumps(payload, indent=2, ensure_ascii=False)
    if output_path:
        target = Path(output_path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload_json + os.linesep, encoding="utf-8")
        return
    print(payload_json)


def main() -> int:
    args = parse_args()

    if args.command == "catalog":
        write_output(catalog_stats(), None)
        return 0

    if args.command == "extract":
        try:
            features = extract_library_features(args.apk)
            write_output(_serialize_features(features), args.output)
        except (FileNotFoundError, NotADirectoryError, OSError) as exc:
            raise SystemExit(str(exc))
        return 0

    if args.command == "compare":
        try:
            features_a = extract_library_features(args.apk_a)
            features_b = extract_library_features(args.apk_b)
            comparison = compare_libraries(features_a, features_b)
            hints = library_explanation_hints(comparison)

            payload = {
                "apk_a": str(Path(args.apk_a).expanduser().resolve()),
                "apk_b": str(Path(args.apk_b).expanduser().resolve()),
                "features_a": _serialize_features(features_a),
                "features_b": _serialize_features(features_b),
                "comparison": comparison,
                "explanation_hints": hints,
            }
            write_output(payload, args.output)
        except (FileNotFoundError, NotADirectoryError, OSError) as exc:
            raise SystemExit(str(exc))
        return 0

    # No subcommand
    write_output(catalog_stats(), None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
