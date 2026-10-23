"""Validate the three CI executions against an independent complete collection."""

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

SCOPES = {"core": ("tests/contract/", "tests/unit/", "tests/cli/"), "gateway": ("tests/gateway/",)}
SHARDS = {"core": 1, "gateway": 2}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def identities(nodes):
    result = []
    for node in nodes:
        parts = node.split("::")
        require(len(parts) >= 2 and parts[0].endswith(".py"), f"Invalid node: {node}")
        result.append((".".join([parts[0][:-3].replace("/", "."), *parts[1:-1]]), parts[-1]))
    return result


def validate(root):
    records = [json.loads(path.read_text(encoding="utf-8")) for path in root.rglob("collection-*.json")]
    require(records, "No collection manifests")
    for row in records:
        require(row["schemaVersion"] == 1 and row["exitStatus"] == 0, "Incomplete/failed collection")
        for key in ("collected", "selected", "deselected"):
            require(len(row[key]) == len(set(row[key])), f"Duplicate {key}")
    references = [row for row in records if row["lane"] == "reference"]
    require(len(references) == 1, "Need one independent complete collection")
    reference = references[0]
    all_nodes = set(reference["collected"])
    require(
        reference["collectOnly"] and reference["shardIndex"] == reference["shardCount"] == 1,
        "Reference must be unsharded collect-only",
    )
    require(set(reference["selected"]) == all_nodes and not reference["deselected"], "Filtered reference")
    prefixes = sum(SCOPES.values(), ())
    require(
        all(any(node.startswith(prefix) for prefix in prefixes) for node in all_nodes),
        "Foreign reference node",
    )
    require(
        all(any(node.startswith(prefix) for node in all_nodes) for prefix in prefixes),
        "Missing test directory",
    )
    require(all(row["lane"] in {*SCOPES, "reference"} for row in records), "Unknown lane")
    all_executed = []
    summaries = []
    expected_xmls = set()
    for lane, count in SHARDS.items():
        lane_nodes = {node for node in all_nodes if node.startswith(SCOPES[lane])}
        lane_records = [row for row in records if row["lane"] == lane]
        require(
            {row["shardIndex"] for row in lane_records} == set(range(1, count + 1)), "Missing/extra shard"
        )
        for index in range(1, count + 1):
            workers = [row for row in lane_records if row["shardIndex"] == index]
            first = workers[0]
            worker_count = first["workerCount"]
            require(worker_count >= 1 and len(workers) == worker_count, "Missing/duplicate worker manifest")
            expected_workers = {f"gw{n}" for n in range(worker_count)}
            require({row["worker"] for row in workers} == expected_workers, "Unexpected worker identities")
            selected = {
                node
                for node in lane_nodes
                if int.from_bytes(hashlib.sha256(node.encode("utf-8")).digest(), "big") % count == index - 1
            }
            for row in workers:
                require(row["shardCount"] == count and not row["collectOnly"], "Wrong shard configuration")
                require(row["workerCount"] == row["expectedWorkers"] == worker_count, "Worker count mismatch")
                require(set(row["collected"]) == lane_nodes, "Lane differs from complete collection")
                require(
                    set(row["selected"]) == selected and set(row["deselected"]) == lane_nodes - selected,
                    "Wrong sha256 selection/deselection",
                )
            filename = f"{lane}-{index}-of-{count}.xml"
            expected_xmls.add(filename)
            xmls = list(root.rglob(filename))
            require(len(xmls) == 1, f"Need one JUnit: {filename}")
            cases = list(ET.parse(xmls[0]).iter("testcase"))
            executed = [(case.get("classname"), case.get("name")) for case in cases]
            require(len(executed) == len(set(executed)), "Duplicate JUnit identity")
            require(set(executed) == set(identities(selected)), f"JUnit differs from selection: {filename}")
            require(
                not any(case.find("failure") is not None or case.find("error") is not None for case in cases),
                "Failed JUnit case",
            )
            all_executed.extend(selected)
            summaries.append(
                {
                    "lane": lane,
                    "index": index,
                    "count": count,
                    "workers": worker_count,
                    "collected": len(lane_nodes),
                    "selected": len(selected),
                    "deselected": len(lane_nodes - selected),
                    "skipped": sum(case.find("skipped") is not None for case in cases),
                }
            )
    require({path.name for path in root.rglob("*.xml")} == expected_xmls, "Unexpected JUnit files")
    require(
        len(all_executed) == len(set(all_executed)) and set(all_executed) == all_nodes,
        "Executions overlap or omit complete-collection nodes",
    )
    return {"completeNodes": len(all_nodes), "executedOnce": len(all_executed), "shards": summaries}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(validate(args.root), sort_keys=True))
    except (ValueError, KeyError, OSError, ET.ParseError) as error:
        parser.exit(1, f"Python CI coverage failed: {error}\n")


if __name__ == "__main__":
    main()
