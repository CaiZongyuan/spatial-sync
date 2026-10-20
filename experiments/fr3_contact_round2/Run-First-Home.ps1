param([switch]$Execute)
$ErrorActionPreference = 'Stop'
$round2Preparation = 'D:/Projects/Robots/spatial-sync/.scratch/rsi-platform/issue-29-round2/first-home-prep-v4'
$round2IdentityPath = Join-Path $round2Preparation 'prepared_identity.json'
$round2Identity = Get-Content -Raw -Encoding UTF8 -LiteralPath $round2IdentityPath | ConvertFrom-Json
$round2Phase = 'frozen-home-all-first-' + [DateTimeOffset]::UtcNow.ToString('yyyyMMdd-HHmmssfff')
$round2Evidence = [IO.Path]::GetFullPath((Join-Path $round2Identity.archive $round2Phase))
$round2Payload = @($round2Identity.canonical_python, $round2Identity.audit_entry, 'play', '--rl_library', 'rsl_rl',
    '--task', 'Lab-FR3-Pick-Tube-v0', '--num_envs', '1024', '--checkpoint', $round2Identity.checkpoint,
    '--external_callback', 'round2_home.install', 'presets=newton_mjwarp')
$round2Inner = @($round2Identity.primary_python, $round2Identity.run_owned, '--phase', $round2Phase,
    '--evidence', $round2Evidence, '--cwd', $round2Evidence, '--source-commit', $round2Identity.source_commit,
    '--timeout-seconds', '870', '--') + $round2Payload
if (-not $Execute) {
    [pscustomobject]@{state='dry_run_no_gpu';command=$round2Inner;hard_seconds=900;cleanup_seconds=15;reservation_seconds=930} | ConvertTo-Json -Depth 4
    exit 0
}
$round2Gpu = & nvidia-smi --query-gpu=uuid,memory.free --format=csv,noheader,nounits
if ($LASTEXITCODE -ne 0) { throw 'GPU inventory is unavailable; do not launch.' }
$round2GpuValues = @($round2Gpu -split ',')
if ($round2GpuValues.Count -ne 2 -or $round2GpuValues[0].Trim() -ne 'GPU-ea968db7-d722-8e6a-d1d7-70adca2d5248' -or [int]$round2GpuValues[1].Trim() -lt 4096) {
    throw 'Expected GPU or 4GiB free VRAM unavailable; leave consumers intact and reschedule.'
}
New-Item -ItemType Directory -Path $round2Evidence -ErrorAction Stop | Out-Null
foreach ($round2File in @('frozen_home_trace.py', 'trace_contract.py', 'round2_home.py', 'owned_status.py', 'prepared_identity.json')) {
    Copy-Item -LiteralPath (Join-Path $round2Preparation $round2File) -Destination (Join-Path $round2Evidence $round2File) -ErrorAction Stop
}
$round2Origin = [DateTimeOffset]::UtcNow.ToString('o')
$round2Reservation = [ordered]@{owner='issue29_round2';phase=$round2Phase;category='diagnostics';reservation_seconds=930;started_utc=$round2Origin;payload=$round2Payload;state='reserved_execution_pending';actual_seconds=$null;cleanup_confirmed=$false}
$round2Stream = [IO.File]::Open((Join-Path $round2Evidence 'budget-reservation.json'), [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
$round2Writer = [IO.StreamWriter]::new($round2Stream,[Text.UTF8Encoding]::new($false))
try { $round2Writer.Write(($round2Reservation | ConvertTo-Json -Depth 4)) } finally { $round2Writer.Dispose() }
$env:FR3_DIAG_EXECUTION_STARTED_UTC = $round2Origin
& $round2Identity.primary_python (Join-Path $round2Preparation 'bounded_watchdog.py') --evidence $round2Evidence --identity (Join-Path $round2Evidence 'prepared_identity.json') -- @round2Inner
exit $LASTEXITCODE
