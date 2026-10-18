param(
    [ValidateSet('Setup', 'Probe', 'Generate', 'Train', 'Audit')]
    [string]$Stage = 'Probe',
    [string]$Evidence = '',
    [string]$Checkpoint = ''
)

$ErrorActionPreference = 'Stop'
$taskProject = $PSScriptRoot
$taskRepository = (Resolve-Path (Join-Path $taskProject '../..')).Path
if (!$Evidence) {
    $taskPrimaryLine = git -C $taskRepository worktree list --porcelain | Select-Object -First 1
    $taskPrimary = $taskPrimaryLine.Substring('worktree '.Length)
    $Evidence = Join-Path $taskPrimary '.scratch/rsi-platform/issue-29'
}
$Evidence = [IO.Path]::GetFullPath($Evidence)
New-Item -ItemType Directory -Force -Path $Evidence | Out-Null
$env:PXR_WORK_THREAD_LIMIT = '1'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$env:PYTHONPATH = Split-Path $taskProject
$env:FR3_CONTACT_ASSETS = Join-Path $Evidence 'assets'
$env:FR3_CONTACT_RESETS = Join-Path $Evidence 'reset_poses.pt'

if ($Stage -eq 'Setup') {
    uv sync --frozen --project $taskProject
    if ($LASTEXITCODE -ne 0) { throw 'Isolated training environment setup failed' }
    uv run --no-sync --project $taskProject python -m fr3_contact.prepare_assets `
        --repository $taskRepository --output $env:FR3_CONTACT_ASSETS
    exit $LASTEXITCODE
}

$taskArguments = switch ($Stage) {
    'Probe' {
        @('python', '-m', 'fr3_contact.generate_resets', '--output', (Join-Path $Evidence 'probe.pt'),
          '--num_envs', '2', '--poses_per_phase', '2', '--max_batches', '1', '--probe_until', 'lift',
          '--device', 'cuda:0')
    }
    'Generate' {
        @('python', '-m', 'fr3_contact.generate_resets', '--output', $env:FR3_CONTACT_RESETS,
          '--num_envs', '128', '--poses_per_phase', '128', '--max_batches', '4', '--device', 'cuda:0')
    }
    'Train' {
        if (!(Test-Path -LiteralPath $env:FR3_CONTACT_RESETS)) { throw 'Generate valid FR3 resets first' }
        $taskCommand = @('isaaclab', 'train', '--rl_library', 'rsl_rl', '--task', 'Lab-FR3-Pick-Tube-v0',
          '--num_envs', '2048', '--max_iterations', '800', '--seed', '42', '--run_name', 'fr3_contact',
          '--external_callback', 'fr3_contact.register', 'presets=newton_mjwarp')
        if ($Checkpoint) { $taskCommand += @('--checkpoint', $Checkpoint) }
        $taskCommand
    }
    'Audit' {
        if (!$Checkpoint) { throw 'Audit requires an explicit checkpoint path' }
        @('isaaclab', 'play', '--rl_library', 'rsl_rl', '--task', 'Lab-FR3-Pick-Tube-v0',
          '--num_envs', '1024', '--checkpoint', $Checkpoint,
          '--external_callback', 'fr3_contact.evaluation.install_episode_counter', 'presets=newton_mjwarp')
    }
}
Push-Location $Evidence
try {
    uv run --no-sync --project $taskProject @taskArguments
    $taskExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $taskExitCode
