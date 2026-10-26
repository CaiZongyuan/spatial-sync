"""Evaluate frozen, independently labelled queries against product metadata.

Inputs must be actual recipe/store records. This tool never invents records,
translations, tags or relevance labels. A separate --query-file lets a reviewer
retain held-out labels until the implementation is frozen.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.catalog import AssetCatalog, build_semantic_index
from packages.catalog.maintained import adapt_entry
from packages.catalog.semantic import load_embedder
from packages.catalog.views import canonical_json
from packages.catalog_metadata import MetadataStore
from tools.eval_catalog_semantic import FILTERS, _environment


def percentile(values, fraction=.95):
    return sorted(values)[max(0, math.ceil(fraction * len(values)) - 1)] if values else None


def build_catalog(documents, embedder, vocabulary):
    catalog = AssetCatalog(ROOT / "catalog/simready.json", embedder=embedder)
    catalog.startup()
    legacy = catalog.by_id
    entries = tuple(adapt_entry(value, legacy.get(value["id"]), vocabulary=vocabulary)
                    for value in documents)
    identity = "sha256:" + hashlib.sha256(canonical_json(documents).encode()).hexdigest()
    catalog.snapshot = replace(catalog.snapshot, id=identity, entry_count=len(entries),
                               coverage="Fixed product-recipe metadata evaluation corpus.")
    catalog.entries = entries
    catalog.by_id = {entry.id: entry for entry in entries}
    counts = Counter(entry.package_id for entry in entries)
    catalog.by_package = {entry.package_id: entry for entry in entries if counts[entry.package_id] == 1}
    catalog._historical = {}
    catalog.semantic = build_semantic_index(entries, identity, embedder=embedder)
    return catalog


def run_query(catalog, query):
    filters = {**FILTERS, "category": "category", "tag": "tag", "sort": "sort"}
    arguments = {filters[key]: value for key, value in query.get("filters", {}).items()}
    started = time.perf_counter()
    response = catalog.search(None, q=query.get("text", ""), scope="online", limit=5, **arguments)
    duration = (time.perf_counter() - started) * 1000
    result_ids = [value["id"] for value in response["results"]]
    relevant = query.get("relevant", [])
    hard_ok = all(not value["match"]["unknown"] and not value["match"]["unsatisfied"]
                  for value in response["results"])
    zero_reason = (response.get("zeroResult") or {}).get("reason")
    if query.get("expect") == "hard_filters":
        hard_ok = hard_ok and not result_ids and zero_reason == "hard_filters"
    exact_ok = None
    if query.get("kind") == "exact_id":
        exact_ok = bool(result_ids) and bool(relevant) and result_ids[0] == relevant[0]
    zero_ok = not result_ids if query.get("expect") == "zero" else None
    return {"id": query["id"], "language": query["language"], "split": query.get("split", "unspecified"),
            "kind": query.get("kind"), "top5": result_ids,
            "hit": bool(set(result_ids) & set(relevant)) if relevant else None,
            "exact": exact_ok, "hardConditions": hard_ok, "zero": zero_ok,
            "zeroReason": zero_reason, "semanticApplied": response["semantic"]["applied"],
            "latencyMs": duration}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--metadata-root", type=Path)
    inputs.add_argument("--records-file", type=Path, help="Canonical records produced by the product recipes")
    parser.add_argument("--selection", type=Path, help="Frozen representative exact identities")
    parser.add_argument("--query-file", type=Path, required=True)
    parser.add_argument("--embedder", default="stand-in")
    parser.add_argument("--model")
    parser.add_argument("--benchmark-records", type=int, default=0)
    parser.add_argument("--benchmark-runs", type=int, default=5)
    parser.add_argument("--require-top5", type=float)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.benchmark_records < 0 or not 1 <= args.benchmark_runs <= 20:
        parser.error("Use a nonnegative benchmark count and 1..20 runs")
    if args.metadata_root:
        store = MetadataStore(args.metadata_root)
        published = store.latest_release()
        if published is None:
            parser.error("Metadata store has no published release")
        documents = store.entries(published["id"])
        vocabulary = store.vocabulary(published["vocabularyVersion"])
    else:
        value = json.loads(args.records_file.read_text(encoding="utf-8-sig"))
        documents = value if isinstance(value, list) else value["entries"]
        vocabulary = value.get("vocabulary") if isinstance(value, dict) else None
        published = None
    for value in documents:
        expected = f"{value['collectionId']}/{quote(value['upstreamId'], safe='._-')}@{value['revision']}"
        if value.get("id") != expected or not value.get("inputDigest"):
            parser.error("Each record must retain its product identity and complete input digest")
    by_id = {value["id"]: value for value in documents}
    if len(by_id) != len(documents):
        parser.error("Input repeats a physical identity")
    selected = documents
    if args.selection:
        selection = json.loads(args.selection.read_text(encoding="utf-8-sig"))
        identifiers = [record["id"] for record in selection["records"]]
        missing = [identifier for identifier in identifiers if identifier not in by_id]
        if missing:
            parser.error(f"Product metadata is missing {len(missing)} frozen selection identities: "
                         f"{missing[:3]}")
        selected = [by_id[identifier] for identifier in identifiers]
    queries = json.loads(args.query_file.read_text(encoding="utf-8-sig"))["queries"]
    model_started = time.perf_counter()
    embedder = load_embedder(args.embedder, args.model)
    model_ms = (time.perf_counter() - model_started) * 1000
    build_started = time.perf_counter()
    catalog = build_catalog(selected, embedder, vocabulary)
    build_ms = (time.perf_counter() - build_started) * 1000
    rows = [run_query(catalog, query) for query in queries]
    metrics = {}
    for language in sorted({row["language"] for row in rows}):
        for split in sorted({row["split"] for row in rows if row["language"] == language}):
            group = [row for row in rows if row["language"] == language and row["split"] == split]
            hits = [row["hit"] for row in group if row["hit"] is not None]
            metrics[language + ":" + split] = {"top5Hits": sum(hits), "top5Denominator": len(hits),
                "top5Rate": sum(hits) / len(hits) if hits else None,
                "exactCorrect": sum(row["exact"] is True for row in group),
                "exactDenominator": sum(row["exact"] is not None for row in group),
                "hardConditionsCorrect": sum(row["hardConditions"] is True for row in group),
                "hardConditionsDenominator": len(group),
                "zeroCorrect": sum(row["zero"] is True for row in group),
                "zeroDenominator": sum(row["zero"] is not None for row in group)}
    report = {"schema": "lw.catalog-metadata-quality-result/1", "environment": _environment(),
              "inputs": {"release": published, "records": len(selected),
                         "collections": dict(Counter(value["collectionId"] for value in selected)),
                         "dataDigest": catalog.snapshot.id,
                         "queryFileSha256": hashlib.sha256(args.query_file.read_bytes()).hexdigest()},
              "semantic": catalog.semantic.document(), "modelLoadMs": model_ms,
              "coldQualityBuildMs": build_ms, "metrics": metrics, "queries": rows}
    if args.benchmark_records:
        if args.benchmark_records > len(documents):
            parser.error("Benchmark requires that many actual product records; records are never replicated")
        benchmark_entries = sorted(documents, key=lambda value: value["id"])[:args.benchmark_records]
        started = time.perf_counter()
        benchmark = build_catalog(benchmark_entries, embedder, vocabulary)
        benchmark_build_ms = (time.perf_counter() - started) * 1000
        timings = [run_query(benchmark, query)["latencyMs"]
                   for _ in range(args.benchmark_runs) for query in queries]
        report["benchmark"] = {"records": len(benchmark_entries), "genuineProductRecords": True,
                               "index": benchmark.semantic.document(),
                               "buildMsModelAlreadyLoaded": benchmark_build_ms,
                               "queries": len(timings), "p50Ms": statistics.median(timings),
                               "p95Ms": percentile(timings),
                               "scope": "Loaded product search core; excludes HTTP/browser"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "metrics": metrics,
                      "benchmark": report.get("benchmark")}, ensure_ascii=False))
    if any(row["exact"] is False or row["hardConditions"] is False or row["zero"] is False for row in rows):
        return 1
    if args.require_top5 is not None and any(
        value["top5Rate"] is not None and value["top5Rate"] < args.require_top5
        for value in metrics.values()
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
