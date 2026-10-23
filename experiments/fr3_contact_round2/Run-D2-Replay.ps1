param(
    [Parameter(Mandatory=$true)][string]$Preparation,
    [Parameter(Mandatory=$true)][string]$RootGo,
    [switch]$Execute
)
$ErrorActionPreference = 'Stop'
$d2Preparation = [IO.Path]::GetFullPath($Preparation)
$d2RequestPath = Join-Path $d2Preparation 'EXECUTION_REQUEST.json'
$d2Request = Get-Content -Raw -Encoding UTF8 -LiteralPath $d2RequestPath | ConvertFrom-Json
$d2RequestHash = (Get-FileHash -LiteralPath $d2RequestPath -Algorithm SHA256).Hash.ToLowerInvariant()
$d2Identity = Get-Content -Raw -Encoding UTF8 -LiteralPath $d2Request.identity | ConvertFrom-Json
$d2LedgerPath = Join-Path $d2Identity.archive 'budget-ledger.live.json'
if (-not $Execute) {
    [pscustomobject]@{state='proposal_no_gpu_no_reservation';request_sha256=$d2RequestHash;
        source_commit=$d2Request.source_commit;arms=@('A','B');reservation_seconds=900;
        identity=$d2Request.identity;root_go_required=$RootGo} | ConvertTo-Json -Depth 5
    exit 0
}
# Only PM may create the external, reviewed GO and active ledger reservation.
# This launcher never modifies the live ledger; PM must settle its proposed receipt.
$d2Go = Get-Content -Raw -Encoding UTF8 -LiteralPath $RootGo | ConvertFrom-Json
$d2Ledger = Get-Content -Raw -Encoding UTF8 -LiteralPath $d2LedgerPath | ConvertFrom-Json
$d2Now = [DateTimeOffset]::UtcNow
if ($d2Go.scope -ne 'D2_AB_ONCE' -or $d2Go.request_sha256 -ne $d2RequestHash -or
    $d2Go.source_commit -ne $d2Request.source_commit -or $d2Go.reservation_seconds -ne 900 -or
    [DateTimeOffset]::Parse($d2Go.not_before_utc) -gt $d2Now -or
    [DateTimeOffset]::Parse($d2Go.expires_utc) -le $d2Now -or
    $d2Go.phase -notmatch '^d2-fixed-actions-[0-9]{8}-[0-9]{9}$') {
    throw 'Missing, stale, or mismatched exact D2 Root GO; no launch.'
}
$d2Reservation = $d2Ledger.active_reservation
if ($d2Reservation.phase -ne $d2Go.phase -or $d2Reservation.request_sha256 -ne $d2RequestHash -or
    $d2Reservation.reservation_seconds -ne 900 -or
    @($d2Ledger.unknown_or_unresolved_usage).Count -ne 0 -or
    $d2Ledger.actual_diagnostics_seconds -ne 92 -or $d2Ledger.actual_full_training_seconds -ne 0 -or
    $d2Ledger.remaining_final_audit_reservation_seconds -ne 1200 -or
    ($d2Ledger.diagnostics_limit_seconds - $d2Ledger.actual_diagnostics_seconds - 1200) -lt 900) {
    throw 'Expected settled D1 ledger and this exact PM reservation; leave it unchanged.'
}
$d2Head = (& git -C $d2Identity.source_repository rev-parse HEAD).Trim()
$d2Dirty = & git -C $d2Identity.source_repository status --porcelain
if ($LASTEXITCODE -ne 0 -or $d2Head -ne $d2Request.source_commit -or $d2Dirty) {
    throw 'Candidate source changed after review.'
}
if ((Get-FileHash -LiteralPath $d2Request.identity -Algorithm SHA256).Hash.ToLowerInvariant() -ne $d2Request.identity_sha256) {
    throw 'Identity manifest differs from reviewed request.'
}
$d2Pair = Join-Path $d2Identity.archive $d2Go.phase
New-Item -ItemType Directory -Path $d2Pair -ErrorAction Stop | Out-Null
$d2Clock = [Diagnostics.Stopwatch]::StartNew()
$d2Results = @()
$d2Cleanup = @{}
$d2Error = $null
try {
    foreach ($d2Arm in @('A','B')) {
        # A's exact baseline and Job-zero proof are prerequisites for B.
        if ($d2Arm -eq 'B' -and ($d2Results.Count -ne 1 -or -not $d2Results[0].qualified)) {
            throw 'A did not requalify the frozen baseline; B is forbidden.'
        }
        if (900 - $d2Clock.Elapsed.TotalSeconds -lt 450) { throw 'Less than450s remain; do not launch another arm.' }
        $d2InventoryInfo = [Diagnostics.ProcessStartInfo]::new()
        $d2InventoryInfo.FileName = 'nvidia-smi'
        $d2InventoryInfo.Arguments = '--query-gpu=uuid,memory.free --format=csv,noheader,nounits'
        $d2InventoryInfo.UseShellExecute = $false
        $d2InventoryInfo.CreateNoWindow = $true
        $d2InventoryInfo.RedirectStandardOutput = $true
        $d2InventoryInfo.RedirectStandardError = $true
        $d2Inventory = [Diagnostics.Process]::new()
        $d2Inventory.StartInfo = $d2InventoryInfo
        try {
            if (-not $d2Inventory.Start()) { throw 'Read-only GPU inventory failed to start.' }
            $d2Cleanup['inventory-' + $d2Arm] = $false
            if (-not $d2Inventory.WaitForExit(10000)) {
                $d2Inventory.Kill() # Only the handle created immediately above.
                $d2Cleanup['inventory-' + $d2Arm] = $d2Inventory.WaitForExit(5000)
                throw 'GPU inventory exceeded10s; stop, no arm launch.'
            }
            $d2Cleanup['inventory-' + $d2Arm] = $true
            if ($d2Inventory.ExitCode -ne 0) { throw 'Read-only GPU inventory failed.' }
            $d2Gpu = $d2Inventory.StandardOutput.ReadToEnd()
        } finally { $d2Inventory.Dispose() }
        $d2GpuValues = @($d2Gpu -split ',')
        if ($d2GpuValues.Count -ne 2 -or $d2GpuValues[0].Trim() -ne 'GPU-ea968db7-d722-8e6a-d1d7-70adca2d5248' -or
            [int]$d2GpuValues[1].Trim() -lt 4096) { throw 'Expected GPU/free VRAM unavailable; preserve all consumers.' }
        if (900 - $d2Clock.Elapsed.TotalSeconds -lt 450) { throw 'Less than450s remain after inventory; stop.' }
        $d2Phase = $d2Go.phase + '-' + $d2Arm
        $d2Evidence = Join-Path $d2Identity.archive $d2Phase
        $d2ArmClock = [Diagnostics.Stopwatch]::StartNew()
        $d2Origin = [DateTimeOffset]::UtcNow.ToString('o')
        New-Item -ItemType Directory -Path $d2Evidence -ErrorAction Stop | Out-Null
        foreach ($d2File in @('frozen_home_trace.py','trace_contract.py','round2_home.py','owned_status.py','d2_replay.py','prepared_identity.json')) {
            Copy-Item -LiteralPath (Join-Path $d2Preparation $d2File) -Destination (Join-Path $d2Evidence $d2File) -ErrorAction Stop
        }
        $d2Payload = @($d2Identity.canonical_python,$d2Identity.audit_entry,'play','--rl_library','rsl_rl',
            '--task','Lab-FR3-Pick-Tube-v0','--num_envs','1024','--checkpoint',$d2Identity.checkpoint,
            '--external_callback','d2_replay.install','presets=newton_mjwarp')
        $d2Inner = @($d2Identity.primary_python,$d2Identity.run_owned,'--phase',$d2Phase,
            '--evidence',$d2Evidence,'--cwd',$d2Evidence,'--source-commit',$d2Identity.source_commit,
            '--timeout-seconds','420','--') + $d2Payload
        $env:FR3_DIAG_EXECUTION_STARTED_UTC = $d2Origin
        $env:FR3_D2_ARM = $d2Arm
        $d2Cleanup[$d2Arm] = $false
        & $d2Identity.primary_python (Join-Path $d2Preparation 'bounded_watchdog.py') --evidence $d2Evidence --identity (Join-Path $d2Evidence 'prepared_identity.json') -- @d2Inner
        $d2Exit = $LASTEXITCODE
        $d2Watch = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $d2Evidence 'watchdog-result.json') | ConvertFrom-Json
        $d2Cleanup[$d2Arm] = $d2Watch.all_owned_job_processes_exited -eq $true -and $d2Watch.job_active_processes_after_cleanup -eq 0
        $d2TerminalPath = Join-Path $d2Evidence ($d2Phase + '.terminal.json')
        $d2Terminal = Get-Content -Raw -Encoding UTF8 -LiteralPath $d2TerminalPath | ConvertFrom-Json
        $d2Qualification = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $d2Evidence 'd2-qualification.json') | ConvertFrom-Json
        $d2Qualified = $d2Exit -eq 0 -and $d2Watch.status -eq 'normal_exit' -and
            $d2Watch.all_owned_job_processes_exited -eq $true -and $d2Watch.job_active_processes_after_cleanup -eq 0 -and
            $d2Terminal.exit_code -eq 0 -and $d2Terminal.payload_verdict -eq 'process_exit_only' -and
            $d2Qualification.arm -eq $d2Arm -and $d2Qualification.state -eq 'qualified' -and
            $d2ArmClock.Elapsed.TotalSeconds -le 450
        $d2Results += [pscustomobject]@{arm=$d2Arm;evidence=$d2Evidence;qualified=$d2Qualified;
            elapsed_seconds=$d2ArmClock.Elapsed.TotalSeconds;owned_job_zero=$d2Watch.all_owned_job_processes_exited;
            qualification=$d2Qualification}
        if (-not $d2Qualified) { throw 'Arm failed/inconclusive, missing terminal evidence, or exceeded its window; stop, no retry.' }
    }
} catch {
    $d2Error = $_.Exception.Message
} finally {
    # This is a proposed accounting receipt, never a live-ledger settlement or new GO.
    $d2Finished = [DateTimeOffset]::UtcNow.ToString('o')
    $d2Receipt = [ordered]@{owner='issue29_round2';phase=$d2Go.phase;state='needs_pm_settlement';
        request_sha256=$d2RequestHash;source_commit=$d2Request.source_commit;finished_utc=$d2Finished;
        observed_resource_elapsed_seconds=$d2Clock.Elapsed.TotalSeconds;
        proposed_charge_seconds=[Math]::Ceiling($d2Clock.Elapsed.TotalSeconds);reservation_seconds=900;
        arms=$d2Results;error=$d2Error;cleanup_by_created_resource=$d2Cleanup;
        unknown_cleanup=(@($d2Cleanup.Values | Where-Object { -not $_ }).Count -gt 0);
        acceptance_claim=$false;training_authorized=$false;live_ledger_modified=$false}
    $d2ReceiptPath = Join-Path $d2Pair 'budget-settlement.proposed.json'
    $d2Stream = [IO.File]::Open($d2ReceiptPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write)
    $d2Writer = [IO.StreamWriter]::new($d2Stream,[Text.UTF8Encoding]::new($false))
    try { $d2Writer.Write(($d2Receipt | ConvertTo-Json -Depth 12)) } finally { $d2Writer.Dispose() }
}
if ($d2Error) { throw $d2Error }
