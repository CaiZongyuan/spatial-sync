"""Existing receipts only: exact39 cells and same-boundary raw v3/v4 tables."""
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE/"spatial-issue102-repaired-v4-20261023T094815Z"


def main():
    raw=json.loads((ROOT/"work/receipt.json").read_text())
    analysis=json.loads((ROOT/"offline-assessment.json").read_text())
    old=json.loads((HERE/"spatial-issue102-repaired-v3-20261023T062002Z/existing-client-connection-readout.json").read_text())
    new=json.loads((ROOT/"existing-client-connection-readout.json").read_text())
    rows=[]
    for check in raw["budgetChecks"]:
        if "repeat" in check:
            d=raw["measurements"]["repeats"][check["repeat"]-1][check["phase"]]["http"][check["metric"]]
            label=f"R{check['repeat']}/{check['phase']}/{check['metric']}"
        elif check["metric"]=="wait":d=raw["measurements"]["waitWakeLatency"];label="registered wait"
        else:
            index=sum(1 for row in rows if row["metric"]=="observation")
            d=raw["measurements"]["observationLatency"][index];label="observation Reader"+str(index+1)
        rows.append(check|{"label":label,"samples":d["samples"],"p50Ms":d["p50Ms"],"maxMs":d["maxMs"]})
    assert len(rows)==39 and sum(row["passed"] for row in rows)==3
    points=json.loads((ROOT/"offline-request-boundaries.json").read_text())
    server=json.loads((ROOT/"work/server-common-trace.json").read_text())
    ticks=[(event[5]["deadlineNs"],event[0]) for event in server["events"] if event[2]=="gateway.loop.late"]
    slow=[]
    for row in points:
        a=row["points"]["clientRequestBodySent"];b=row["points"]["asgiEntry"]
        if a and b and b[0]-a[0]>=5_000_000:
            slow.append({"rid":row["rid"],"gapMs":(b[0]-a[0])/1e6,"intersectsGatewayLateDeadlineInterval":any(start<=b[0] and end>=a[0] for start,end in ticks)})
    overlap={"scope":"existing same-ID body-send-complete to ASGI-entry wall gaps>=5ms; no new measurement","slowRequests":len(slow),"withLateDeadlineIntervalOverlap":sum(row["intersectsGatewayLateDeadlineInterval"] for row in slow),"notCausality":True,"rows":slow}
    (ROOT/"exact39-and-residual-gap.json").write_text(json.dumps({"latencyChecks":rows,"residualGap":overlap},indent=2)+"\n",encoding="utf-8")
    lines=["# v4 detailed qualification and same-boundary readout", "", "Qualified full8db+overlay work/trace, but3/39 latency checks pass and36 fail. This is not latest-main or §12 overall acceptance. All earlier v3 invalid/zero-call RED records are unchanged.", "", "## Exact39 original latency checks", "", "| Cell | N | P50 ms | P95 ms | Limit ms | Result |", "|---|---:|---:|---:|---:|---|"]
    for row in rows:lines.append(f"| {row['label']} | {row['samples']} | {row['p50Ms']:.3f} | {row['p95Ms']:.3f} | {row['budgetMs']} | {'PASS' if row['passed'] else 'RED'} |")
    lines += ["", "## Identical interval definitions, raw distributions only", "", "Columns are wall P50/P95 then local client-thread CPU P50/P95 (ms). v3 fails cadence qualification; it is not an accepted baseline or total speedup comparison. No percentile subtraction/addition is used.", "", "| Actual endpoint interval | v3 raw wall | v3 raw CPU | v4 wall | v4 CPU |", "|---|---:|---:|---:|---:|"]
    for key in ("transport_to_headers_start_total","headers_receive_region","body_complete_to_response_close_start","response_closed_region","response_closed_complete_to_client_body_close","client_body_close_to_send_return"):
        values=[]
        for item in (old["spans"][key],new["spans"][key]):
            for part in ("wall","sameClientThreadCPU"):
                d=item[part];values.append(f"{d['p50Ms']:.3f}/{d['p95Ms']:.3f}")
        lines.append("| "+key+" | "+" | ".join(values)+" |")
    lines += ["", "The optional-import component proof plus these candidate observations support disappearance of the previously long client pre-header and close-surrounding intervals in this configuration. They do not assign a percentage of total request time or establish a stable whole-request speedup. response_closed is still not a TCP teardown event, and CPU is not exclusive function/coroutine CPU.", "", "## Actual package identity by role", "", "| Role | PID | Original79 checked | Extra version | Actual extra origin |", "|---|---:|---:|---|---|"]
    for role,meta in zip(("work preflight","outer","gateway"),analysis["overlayActors"]):
        lines.append(f"| {role} | {meta['pid']} | {len(meta['original79Pins'])} unchanged | {meta['extraPackageVersion']} | {meta['extraPackageOrigin']} |")
    lines += ["", "All roles carry identical whole-tree1bff43ab…2f0e8 and14 member hashes/sizes, unchanged paho ZIP6db9…8feee and declared official sniffio wheel2f6d…525a2. Full79 rows are in offline-assessment.json/actor files. Allocation preflight additionally records actual paho module origin under the candidate ZIP; base's same-origin guard passed before Source. Per-role metadata stores paho ZIP hash and exact expected locator, not a separate79-module-import inventory. No native modules were imported merely to manufacture79 origin strings.", "", "## Remaining visible gap", "", "V4 pre-header P95 is0.224ms and post-ASGI-final-send->client return P95 is1.584ms. Yet response-headers receive P95 is17.890ms (client CPU0.601ms), and same-ID request-body-send-complete->ASGI-entry P95 is15.844ms; ASGI-entry->final-send P95 is3.394ms. Thus existing endpoints already place much of the remaining observed tail before ASGI app entry while the client awaits headers, not in the removed close gaps. This is a dispatch/transport/resumption boundary, not a named-function diagnosis.", "", f"{len(slow)} measured request pre-ASGI gaps are>=5ms; {overlap['withLateDeadlineIntervalOverlap']} intersect a recorded gateway late-deadline interval. Overlap is consistent with gateway-loop dispatch delay, but does not separate Source work, other coroutine work, OS/host scheduling or network dispatch, and is not causal proof. Raw IDs are retained in exact39-and-residual-gap.json.", "", "No new harness is needed to locate that next visible boundary. The best next step is source-only mapping of existing gateway tick/event/app-entry scheduling and review of the aligned raw deadline intervals, before selecting any separately frozen discriminator. No extra Source run, product dependency/default change, pyproject/lock edit or #85 edit was performed. Consumer/latest-main validation remains future Root scope."]
    (ROOT/"DETAILED-READOUT.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps({"exactChecks":len(rows),"pass":sum(row["passed"] for row in rows),"red":sum(not row["passed"] for row in rows),"slowPreASGIRequests":len(slow),"overlappingGatewayLateInterval":overlap["withLateDeadlineIntervalOverlap"]}))


if __name__=="__main__":main()
