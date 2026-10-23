param(
    [Parameter(Mandatory=$true)][string]$Preparation,
    [Parameter(Mandatory=$true)][string]$RootGo,
    [switch]$Execute
)
# First action after argument binding: origin includes interpreter/preflight/hash/readback/cleanup.
$d2Origin = [DateTimeOffset]::UtcNow.ToString('o')
$d2LaunchClock = [Diagnostics.Stopwatch]::StartNew()
$ErrorActionPreference = 'Stop'
if (-not $Execute) {
    [pscustomobject]@{state='proposal_no_gpu_no_reservation';preparation=$Preparation;root_go=$RootGo;
        total_seconds=900;work_stop_seconds=870;cleanup_seconds=15;outer_fallback_seconds=885} | ConvertTo-Json
    exit 0
}
$d2Process = [Diagnostics.Process]::new()
$d2Started = $false
$d2Unknown = $false
$d2Exit = 125
$d2Args = @()
try {
    $d2Preparation = [IO.Path]::GetFullPath($Preparation).TrimEnd([char[]]@('\','/'))
    $d2RootGo = [IO.Path]::GetFullPath($RootGo)
    if ($d2Preparation -match '["\r\n]' -or $d2RootGo -match '["\r\n]' -or $d2RootGo.EndsWith('\')) { throw 'Literal local paths required.' }
    $d2Guardian = Join-Path $PSScriptRoot 'd2_guardian.py'
    $d2Python = 'D:/Projects/Robots/spatial-sync/.worktrees/issue-29/experiments/fr3_contact/.venv/Scripts/python.exe'
    $d2Args = @($d2Guardian,'--preparation',$d2Preparation,'--root-go',$d2RootGo,'--origin',$d2Origin)
    $d2Info = [Diagnostics.ProcessStartInfo]::new()
    $d2Info.FileName = $d2Python
    $d2Info.Arguments = (($d2Args | ForEach-Object { '"' + $_ + '"' }) -join ' ')
    $d2Info.UseShellExecute = $false
    $d2Info.CreateNoWindow = $true
    $d2Process.StartInfo = $d2Info
    if (-not $d2Process.Start()) { throw 'Guardian failed to start.' }
    $d2Started = $true
    $d2Wait = [Math]::Max(0,[int](885000 - $d2LaunchClock.Elapsed.TotalMilliseconds))
    if (-not $d2Process.WaitForExit($d2Wait)) {
        # Current process handle only. Closing its current kill-on-close Job also kills descendants.
        # Without its Job0 receipt, cleanup is UNKNOWN and never accepted or settled automatically.
        $d2Unknown = $true
        $d2Process.Kill()
        $d2Tail = [Math]::Max(0,[int](900000 - $d2LaunchClock.Elapsed.TotalMilliseconds))
        [void]$d2Process.WaitForExit($d2Tail)
        $d2Exit = 126
    } else {
        $d2Exit = $d2Process.ExitCode
        $d2Unknown = $d2Exit -ne 0 # Detailed Job0 evidence stays in the guardian receipt, never inferred here.
    }
} catch {
    $d2Failure = $_.Exception.Message
    if ($d2Started -and -not $d2Process.HasExited) {
        $d2Unknown = $true
        $d2Process.Kill()
        $d2Tail = [Math]::Max(0,[int](900000 - $d2LaunchClock.Elapsed.TotalMilliseconds))
        [void]$d2Process.WaitForExit($d2Tail)
    }
    $d2Exit = 125
} finally {
    # Console is the last-resort partial receipt even if all evidence-file I/O blocks.
    [Console]::Error.WriteLine(([ordered]@{state='outer_process_handle_only';launch_origin_utc=$d2Origin;
        elapsed_seconds=$d2LaunchClock.Elapsed.TotalSeconds;proposed_charge_seconds=[Math]::Ceiling($d2LaunchClock.Elapsed.TotalSeconds);
        guardian_argv=$d2Args;guardian_started=$d2Started;unknown_cleanup=$d2Unknown;
        exit_code=$d2Exit;error=$d2Failure;acceptance_claim=$false;live_ledger_modified=$false} | ConvertTo-Json -Depth 5))
    $d2Process.Dispose()
}
exit $d2Exit
