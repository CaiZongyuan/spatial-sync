"""Publish a complete Cell Passage round using existing public Plan HTTP routes."""

import argparse
import json
import re
from copy import deepcopy
from pathlib import Path

from apps.cli.lw.client import GatewayClient, GatewayError

TARGET_FIELDS = ("sessionId", "sceneRevision", "epoch")


def _save(path, document):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf8"
    )
    temporary.replace(path)


async def prepare_round(client, recipe, output, *, workcell="/World/Lab/LiquidHandler"):
    """Create all ordered stage parts, without starting a device action.

    A chosen public Plan ID lets a lost create response be queried without
    resubmission. A partial preparation remains visible and is not runnable proof.
    """
    if (recipe["schema"] != "lw.cell-passage-recipe/1"
            or {row["stage"] for row in recipe["plans"]} != set(range(1, 8))
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", recipe["roundId"])):
        raise ValueError("Preparation requires seven ordered stages and a bounded round identity")
    expected = []
    for stage in range(1, 8):
        parts = [row for row in recipe["plans"] if row["stage"] == stage]
        if any(row["parts"] != len(parts) for row in parts):
            raise ValueError("Incomplete or conflicting stage part count")
        expected.extend((stage, number) for number in range(1, len(parts) + 1))
    if [(row["stage"], row["part"]) for row in recipe["plans"]] != expected:
        raise ValueError("Stage parts must be complete, unique and ordered")
    session = await client.status()
    values = (await client.get_state([workcell]))["entities"][workcell]["values"]
    if values["controlMode"] != "auto" or not values["controlSafe"] or session["state"] != "RUNNING":
        raise ValueError("Prepare only in a running, safely stopped automatic-control session")
    if any(values[name] != step["do"]["params"][field]
           for row in recipe["plans"] for step in row["document"]["steps"] if "do" in step
           for name, field in (("controlOwner", "owner"), ("controlEpoch", "epoch"))):
        raise ValueError("Recipe owner/epoch differs from the source control generation")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    path = output / "round.json"
    record = {"schema": "lw.cell-passage-round/1", "roundId": recipe["roundId"], "status": "preparing",
              "target": {name: session[name] for name in TARGET_FIELDS}, "workcell": workcell,
              "controlOwner": values["controlOwner"], "controlEpoch": values["controlEpoch"],
              "initialMaterialRevision": values["materialRevision"],
              "initialMaterialSnapshot": values["materialSnapshot"], "plans": [],
              "mechanicallyQualified": False,
              "environmentChecksAvailable": recipe["environmentChecksAvailable"]}
    _save(path, record)
    try:
        for row in recipe["plans"]:
            document = deepcopy(row["document"])
            document["id"] = (
                f"cp-{recipe['roundId']}-stage{row['stage']}-part{row['part']}-of{row['parts']}"
            )
            record["admissionAttempt"] = document["id"]
            _save(path, record)
            try:
                plan = await client.plan_create(document, actor="cli:cell-passage")
            except GatewayError as error:
                if error.status_code is not None:
                    raise
                # Only observe this known identity; never submit the create again.
                plan = await client.plan_show(document["id"])
            if any(plan[name] != expected for name, expected in record["target"].items()):
                raise ValueError("Plan target changed during round preparation")
            record["plans"].append({"id": plan["id"], "stage": row["stage"], "part": row["part"],
                                    "parts": row["parts"], "revision": plan["revision"]})
            record.pop("admissionAttempt")
            _save(path, record)
        current = await client.status()
        observed = (await client.get_state([workcell]))["entities"][workcell]["values"]
        if (any(current[name] != expected for name, expected in record["target"].items())
                or observed["materialRevision"] != record["initialMaterialRevision"]
                or observed["controlOwner"] != record["controlOwner"]
                or observed["controlEpoch"] != record["controlEpoch"]
                or observed["controlMode"] != "auto"):
            raise ValueError("Session, control or material changed while preparing the round")
        record["status"] = "prepared"
    except BaseException:
        record["status"] = "preparation_interrupted"
        _save(path, record)
        raise
    _save(path, record)
    return record


async def _main(args):
    recipe = json.loads(args.recipe.read_text(encoding="utf8"))
    async with GatewayClient(url=args.url, session=args.session) as client:
        record = await prepare_round(client, recipe, args.out, workcell=args.workcell)
        print(json.dumps({"status": record["status"], "plans": record["plans"],
                          "round": str(args.out / "round.json"), "mechanicallyQualified": False}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8023")
    parser.add_argument("--session", required=True)
    parser.add_argument("--recipe", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--workcell", default="/World/Lab/LiquidHandler")
    import asyncio

    asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    main()
