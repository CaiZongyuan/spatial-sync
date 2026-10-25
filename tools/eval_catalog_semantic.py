"""Evaluate catalog semantic recall against the versioned SF1b corpus.

The default deterministic stand-in is a CI contract check, not real-model
quality evidence. A real CPU multilingual embedder can be selected with
``--embedder fastembed|sentence-transformers --model <name-or-path>``; that
run must use ``uv run --with ... --no-sync`` or an existing local model path so
the committed lockfile is untouched.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import os
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.catalog import AssetCatalog, build_semantic_index  # noqa: E402
from packages.catalog.models import load_catalog  # noqa: E402

FILTERS = {
    "kind": "kind",
    "provider": "provider",
    "usage": "usage",
    "licenseAdmitted": "license_admitted",
    "installed": "installed",
    "minSourceBytes": "min_source_bytes",
    "maxSourceBytes": "max_source_bytes",
    "minDimensionM": "min_dimension_m",
    "maxDimensionM": "max_dimension_m",
}


class EmptyLibrary:
    versions = {}


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def _search_filters(query):
    return {FILTERS[key]: value for key, value in query.get("filters", {}).items()}


def _run_query(catalog, query, limit=10):
    started = time.perf_counter()
    response = catalog.search(
        EmptyLibrary(), q=query["text"], scope="online", limit=limit,
        **_search_filters(query),
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    return response, elapsed_ms


def _hit(query, response):
    relevant = set(query["relevant"])
    if not relevant:
        return None
    top5 = [item["id"] for item in response["results"][:5]]
    return bool(relevant.intersection(top5))


def _exact_version(query, response):
    if query["kind"] != "exact_id" or not query["relevant"]:
        return None
    return bool(response["results"]) and response["results"][0]["id"] == query["relevant"][0]


def _hard_filter_ok(query, response):
    if not query.get("filters") and query["expect"] != "hard_filters":
        return None
    for item in response["results"]:
        matched = item.get("match", {})
        if matched.get("unsatisfied") or matched.get("unknown"):
            return False
    if query["expect"] == "hard_filters":
        return not response["results"] and bool(
            response.get("zeroResult")
            and response["zeroResult"]["reason"] == "hard_filters"
        )
    return True


def _zero_ok(query, response):
    if query["expect"] != "zero":
        return None
    return not response["results"] and bool(
        response.get("zeroResult") and response["zeroResult"]["reason"] == "no_query_match"
    )


def _grouped(queries, records):
    groups = {}
    for language in ("zh", "en"):
        for split in ("all", "tuned", "retained"):
            selected = [
                query for query in queries
                if query["language"] == language and (split == "all" or query["split"] == split)
            ]
            hits = [record["hit"] for record in records
                    if record["language"] == language
                    and (split == "all" or record["split"] == split)
                    and record["hit"] is not None]
            denominator = len(hits)
            successes = sum(1 for value in hits if value)
            exact = [record["exactVersion"] for record in records
                     if record["language"] == language
                     and (split == "all" or record["split"] == split)
                     and record["exactVersion"] is not None]
            conditions = [record["hardFilter"] for record in records
                          if record["language"] == language
                          and (split == "all" or record["split"] == split)
                          and record["hardFilter"] is not None]
            zero = [record["zero"] for record in records
                    if record["language"] == language
                    and (split == "all" or record["split"] == split)
                    and record["zero"] is not None]
            groups[f"{language}:{split}"] = {
                "queries": len(selected),
                "top5": {
                    "successes": successes,
                    "denominator": denominator,
                    "rate": (successes / denominator) if denominator else None,
                },
                "exactVersion": {
                    "successes": sum(1 for value in exact if value),
                    "denominator": len(exact),
                    "rate": (sum(1 for value in exact if value) / len(exact)) if exact else None,
                },
                "hardFilter": {
                    "successes": sum(1 for value in conditions if value),
                    "denominator": len(conditions),
                    "rate": (sum(1 for value in conditions if value) / len(conditions))
                    if conditions else None,
                },
                "zero": {
                    "successes": sum(1 for value in zero if value),
                    "denominator": len(zero),
                },
            }
    return groups


def _environment():
    values = {
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "machine": platform.machine(),
        "cpuCount": os.cpu_count(),
        "python": platform.python_version(),
    }
    try:
        import numpy

        values["numpy"] = numpy.__version__
    except ImportError:
        values["numpy"] = None
    for name in ("fastembed", "sentence-transformers", "onnxruntime"):
        try:
            values[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            values[name] = None
    return values


def _benchmark(catalog, queries, count, runs):
    """Repeat the fixture catalog in a temporary file to estimate hot-query latency."""
    if count <= len(catalog.entries):
        return None
    base = json.loads(Path(catalog.path).read_text(encoding="utf-8"))
    entries = list(base["entries"])
    index = 0
    while len(entries) < count:
        source = json.loads(json.dumps(entries[index % len(base["entries"])]))
        suffix = f"_{index:05d}"
        source["packageId"] = source["packageId"] + suffix
        source["localAssetId"] = source["localAssetId"] + "-" + str(index)
        for language in ("en", "zh"):
            source["names"][language] = source["names"][language] + " " + str(index)
        entries.append(source)
        index += 1
    base["entries"] = entries[:count]
    with tempfile.TemporaryDirectory(prefix="lw-semantic-bench-") as directory:
        path = Path(directory) / "catalog.json"
        path.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
        snapshot, loaded = load_catalog(path)
        started = time.perf_counter()
        semantic = build_semantic_index(
            loaded, snapshot.id, embedder=catalog.semantic.embedder,
            threshold=catalog.semantic.threshold,
            min_coverage=catalog.semantic.min_coverage,
        )
        build_ms = (time.perf_counter() - started) * 1000
        bench = AssetCatalog(path)
        bench.startup()
        bench.semantic = semantic
        timings = []
        for _ in range(max(1, runs)):
            for query in queries:
                _, elapsed = _run_query(bench, query)
                timings.append(elapsed)
        return {
            "records": len(loaded),
            "indexBuildMs": round(build_ms, 3),
            "queries": len(queries) * max(1, runs),
            "p50Ms": round(statistics.median(timings), 3) if timings else None,
            "p95Ms": round(_percentile(timings, 0.95), 3) if timings else None,
            "coldStartReportedSeparately": True,
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default=str(
        ROOT / "tests/fixtures/catalog/semantic/catalog.json"))
    parser.add_argument("--queries", default=str(
        ROOT / "tests/fixtures/catalog/semantic/queries.json"))
    parser.add_argument("--embedder", default="stand-in",
                        choices=("stand-in", "fastembed", "sentence-transformers"))
    parser.add_argument("--model", default=None)
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--min-coverage", type=float, default=None)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--repeat", type=int, default=2,
                        help="Warm query repetitions per query; the last run is measured.")
    parser.add_argument("--benchmark-records", type=int, default=0)
    parser.add_argument("--benchmark-runs", type=int, default=3)
    parser.add_argument("--require-top5", type=float, default=None,
                        help="Exit non-zero if a retained all-language top-5 rate is below this.")
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)

    catalog_path = Path(args.catalog)
    queries_path = Path(args.queries)
    snapshot, entries = load_catalog(catalog_path)
    corpus = json.loads(queries_path.read_text(encoding="utf-8"))
    if corpus["catalog"]["snapshot"] != snapshot.id:
        raise SystemExit(
            f"Corpus snapshot {corpus['catalog']['snapshot']} does not match {snapshot.id}"
        )
    started = time.perf_counter()
    semantic = build_semantic_index(
        entries, snapshot.id, spec=args.embedder, model=args.model,
        threshold=args.threshold, min_coverage=args.min_coverage,
    )
    build_ms = (time.perf_counter() - started) * 1000
    catalog = AssetCatalog(catalog_path)
    catalog.startup()
    catalog.semantic = semantic

    records = []
    timings = []
    for query in corpus["queries"]:
        response = None
        elapsed = 0.0
        for _ in range(max(1, args.repeat)):
            response, elapsed = _run_query(catalog, query, limit=args.limit)
        timings.append(elapsed)
        records.append({
            "id": query["id"],
            "language": query["language"],
            "split": query["split"],
            "kind": query["kind"],
            "expect": query["expect"],
            "top5": [item["id"] for item in response["results"][:5]],
            "relevant": query["relevant"],
            "hit": _hit(query, response),
            "exactVersion": _exact_version(query, response),
            "hardFilter": _hard_filter_ok(query, response),
            "zero": _zero_ok(query, response),
            "semanticApplied": response["semantic"]["applied"],
            "semanticReason": response["semantic"]["reason"],
            "zeroReason": (response.get("zeroResult") or {}).get("reason"),
            "latencyMs": round(elapsed, 3),
        })

    groups = _grouped(corpus["queries"], records)
    summary = {
        "schema": "lw.catalog-semantic-eval-result/1",
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "command": [sys.executable, *sys.argv],
        "corpus": {
            "path": str(queries_path),
            "schema": corpus["schema"],
            "snapshot": snapshot.id,
            "records": len(entries),
            "realRecords": corpus["catalog"]["realRecords"],
            "syntheticRecords": corpus["catalog"]["syntheticRecords"],
            "queries": len(corpus["queries"]),
            "retained": corpus["splits"]["retained"],
            "labels": corpus["labels"],
        },
        "environment": _environment(),
        "semantic": {
            "model": semantic.embedder.info.document(),
            "index": semantic.document()["index"],
            "threshold": semantic.threshold,
            "minCoverage": semantic.min_coverage,
            "coverageBypass": semantic.coverage_bypass,
            "indexBuildMs": round(build_ms, 3),
            "coldStart": True,
        },
        "metrics": groups,
        "latency": {
            "queryCount": len(timings),
            "p50Ms": round(statistics.median(timings), 3),
            "p95Ms": round(_percentile(timings, 0.95), 3),
            "scope": (
                "this run's corpus and host; the Linux/WSL2 budget and a measured baseline "
                "are in docs/VERIFICATION.md"
            ),
        },
        "zeroResultCases": [
            {
                "id": record["id"], "language": record["language"], "split": record["split"],
                "kind": record["kind"], "expect": record["expect"],
                "actual": record["zeroReason"],
                "passed": record["zero"] if record["zero"] is not None else record["hardFilter"],
            }
            for record in records if record["zero"] is not None or record["kind"] == "unknown"
        ],
        "queries": records,
    }
    if args.benchmark_records:
        summary["benchmark"] = _benchmark(
            catalog, corpus["queries"], args.benchmark_records, args.benchmark_runs
        )
    rendered = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered if not args.output else json.dumps({
        "output": str(args.output),
        "model": summary["semantic"]["model"],
        "metrics": groups,
        "latency": summary["latency"],
        "benchmark": summary.get("benchmark"),
    }, ensure_ascii=False, indent=2))

    if args.require_top5 is not None:
        for language in ("zh", "en"):
            retained = groups[f"{language}:retained"]["top5"]["rate"]
            if retained is None or retained < args.require_top5:
                raise SystemExit(
                    f"{language} retained top-5 {retained} is below {args.require_top5}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
