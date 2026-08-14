#requires -Version 7.2
[CmdletBinding()]
param(
    [string]$ExpectedHead = "",
    [string]$Branch = "agent/full-dual-engine-runtime",
    [string]$Repository = "https://github.com/Naveax/Syntavra.git",
    [string]$WorkingRoot = (Join-Path $HOME "Downloads"),
    [string]$ContainerImage = "ghcr.io/astral-sh/uv:python3.13-bookworm-slim",
    [switch]$Push
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Invoke-Checked {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [string]$WorkingDirectory = (Get-Location).Path
    )

    Write-Host ("> {0} {1}" -f $FilePath, ($ArgumentList -join " ")) -ForegroundColor DarkGray
    Push-Location -LiteralPath $WorkingDirectory
    try {
        & $FilePath @ArgumentList
        if ($LASTEXITCODE -ne 0) {
            throw "Komut başarısız oldu (exit=$LASTEXITCODE): $FilePath $($ArgumentList -join ' ')"
        }
    }
    finally {
        Pop-Location
    }
}

function Get-RequiredCommand {
    param([Parameter(Mandatory)][string]$Name)

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "Gerekli komut bulunamadı: $Name"
    }
    return $command.Source
}

function Get-GitOutput {
    param(
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][string]$WorkingDirectory
    )

    $output = & git -C $WorkingDirectory @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "git komutu başarısız oldu: git -C $WorkingDirectory $($Arguments -join ' ')`n$output"
    }
    return (($output | Out-String).Trim())
}

function Get-RemoteBranchHead {
    param(
        [Parameter(Mandatory)][string]$Remote,
        [Parameter(Mandatory)][string]$RemoteBranch,
        [int]$MaxAttempts = 4
    )

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        $line = (& git ls-remote $Remote "refs/heads/$RemoteBranch" 2>&1 | Out-String).Trim()
        $code = $LASTEXITCODE
        if ($code -eq 0 -and -not [string]::IsNullOrWhiteSpace($line)) {
            $head = ($line -split "\s+")[0]
            if ($head -match '^[0-9a-f]{40}$') {
                return $head
            }
        }

        if ($attempt -eq $MaxAttempts) {
            throw "Remote branch head okunamadı: $RemoteBranch (attempt=$attempt/$MaxAttempts, exit=$code)`n$line"
        }

        Write-Warning "Remote head sorgusu başarısız oldu (attempt=$attempt/$MaxAttempts, exit=$code). Yeniden deneniyor."
        Start-Sleep -Seconds (3 * $attempt)
    }
}

function Invoke-GitCloneWithRetry {
    param(
        [Parameter(Mandatory)][string]$Remote,
        [Parameter(Mandatory)][string]$RemoteBranch,
        [Parameter(Mandatory)][string]$Destination,
        [int]$MaxAttempts = 4
    )

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        if (Test-Path -LiteralPath $Destination) {
            Remove-Item -LiteralPath $Destination -Recurse -Force
        }

        Write-Host "Clone attempt $attempt/$MaxAttempts" -ForegroundColor DarkGray
        & git clone `
            --no-tags `
            --single-branch `
            --branch $RemoteBranch `
            $Remote `
            $Destination

        $code = $LASTEXITCODE
        if ($code -eq 0) {
            return
        }

        if (Test-Path -LiteralPath $Destination) {
            Remove-Item -LiteralPath $Destination -Recurse -Force -ErrorAction SilentlyContinue
        }

        if ($attempt -eq $MaxAttempts) {
            throw "Git clone başarısız oldu (attempt=$attempt/$MaxAttempts, exit=$code)."
        }

        Write-Warning "Git clone başarısız oldu (attempt=$attempt/$MaxAttempts, exit=$code). Temiz clone yeniden deneniyor."
        Start-Sleep -Seconds (5 * $attempt)
    }
}

$null = Get-RequiredCommand -Name "git"
$null = Get-RequiredCommand -Name "docker"

$dockerInfo = & docker info --format "{{json .ServerVersion}}" 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop hazır değil. Docker Desktop'ı açıp yeniden çalıştırın.`n$dockerInfo"
}

$initialRemoteHead = Get-RemoteBranchHead -Remote $Repository -RemoteBranch $Branch
if ([string]::IsNullOrWhiteSpace($ExpectedHead)) {
    $ExpectedHead = $initialRemoteHead
}
elseif ($ExpectedHead -ne $initialRemoteHead) {
    throw "Verilen exact head remote branch ile uyuşmuyor. Beklenen=$ExpectedHead Remote=$initialRemoteHead"
}

Write-Host "Pinned exact head: $ExpectedHead" -ForegroundColor Green

New-Item -ItemType Directory -Path $WorkingRoot -Force | Out-Null
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$repoRoot = Join-Path $WorkingRoot "Syntavra-r38-artifact-stats-cert-$timestamp"
$validationScript = Join-Path $env:TEMP "r38-artifact-stats-cert-$timestamp.sh"

Write-Host "`n=== TEMİZ EXACT-HEAD CLONE ===" -ForegroundColor Cyan
Invoke-GitCloneWithRetry `
    -Remote $Repository `
    -RemoteBranch $Branch `
    -Destination $repoRoot

$head = Get-GitOutput -Arguments @("rev-parse", "HEAD") -WorkingDirectory $repoRoot
if ($head -ne $ExpectedHead) {
    throw "Exact head uyuşmuyor. Beklenen=$ExpectedHead Gerçek=$head"
}
$remoteHead = Get-RemoteBranchHead -Remote $Repository -RemoteBranch $Branch
if ($remoteHead -ne $ExpectedHead) {
    throw "Remote branch işlem başlamadan değişti. Beklenen=$ExpectedHead Remote=$remoteHead"
}

$validationSource = @'
#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
export PYTHONPATH=/workspace
export RUSTUP_TOOLCHAIN=1.82.0

apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl build-essential pkg-config libssl-dev git
rm -rf /var/lib/apt/lists/*

curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
  | sh -s -- -y --profile minimal --default-toolchain 1.82.0 --component rustfmt
. "$HOME/.cargo/env"
git config --global --add safe.directory /workspace

uv pip install --system --disable-pip-version-check -e . pytest

python tools/repair_r38_run_artifact_stats_contract.py
python tools/advance_r38_run_artifact_stats_inventory.py
cargo fmt --all
cargo check --locked -p syntavra-cli --bin syntavra
git diff --binary > /tmp/artifact-stats-first.diff
python tools/repair_r38_run_artifact_stats_contract.py
python tools/advance_r38_run_artifact_stats_inventory.py
cargo fmt --all
git diff --binary > /tmp/artifact-stats-second.diff
cmp /tmp/artifact-stats-first.diff /tmp/artifact-stats-second.diff

cargo build --locked -p syntavra-cli --bin syntavra
SYNTAVRA_R38_SELECTOR="$PWD/target/debug/syntavra" \
  python -m pytest -q \
    tests/runtime/test_native_compress_describe_r38.py \
    tests/runtime/test_native_compress_put_r38.py \
    tests/runtime/test_native_compress_get_r38.py \
    tests/runtime/test_native_compress_verify_r38.py \
    tests/runtime/test_native_evidence_get_r38.py \
    tests/runtime/test_native_evidence_rotate_key_r38.py \
    tests/runtime/test_native_run_artifact_put_r38.py \
    tests/runtime/test_native_run_artifact_query_r38.py \
    tests/runtime/test_native_run_artifact_stats_r38.py

python tools/validate_r38_regression_closure.py

reproduce_metadata() {
  python tools/repair_r38_selector_option_values.py
  python tools/repair_r38_windows_rollout_identity.py
  python tools/repair_r38_native_expansion_sync.py
  python tools/repair_r38_backup_create_contract.py
  python tools/repair_r38_backup_evidence_parity.py
  python tools/advance_r38_backup_create_inventory.py
  python tools/repair_r38_backup_verify_contract.py
  python tools/advance_r38_backup_verify_inventory.py
  python tools/repair_r38_backup_restore_contract.py
  python tools/advance_r38_backup_restore_inventory.py
  python tools/repair_r38_compress_describe_contract.py
  python tools/advance_r38_compress_describe_inventory.py
  python tools/repair_r38_compress_put_contract.py
  python tools/advance_r38_compress_put_inventory.py
  python tools/repair_r38_compress_get_contract.py
  python tools/advance_r38_compress_get_inventory.py
  python tools/repair_r38_compress_verify_contract.py
  python tools/advance_r38_compress_verify_inventory.py
  python tools/repair_r38_evidence_get_contract.py
  python tools/advance_r38_evidence_get_inventory.py
  python tools/repair_r38_evidence_rotate_key_contract.py
  python tools/advance_r38_evidence_rotate_key_inventory.py
  python tools/repair_r38_run_artifact_put_contract.py
  python tools/advance_r38_run_artifact_put_inventory.py
  python tools/repair_r38_run_artifact_query_contract.py
  python tools/advance_r38_run_artifact_query_inventory.py
  python tools/repair_r38_run_artifact_stats_contract.py
  python tools/advance_r38_run_artifact_stats_inventory.py
  python tools/repair_r38_native_expansion_sync.py
  python tools/sync_r38_generated_metadata.py
  cargo fmt --all
}

reproduce_metadata
git diff --binary > /tmp/generated-first.diff
reproduce_metadata
git diff --binary > /tmp/generated-second.diff
cmp /tmp/generated-first.diff /tmp/generated-second.diff
python tools/refresh_manifest.py

python - <<'PY'
import json
from pathlib import Path

contract = json.loads(
    Path("contracts/engine/dual-engine-public-surface-v2.json").read_text(
        encoding="utf-8"
    )
)
rust = contract["rust_surface"]
assert contract["python_surface"]["public_command_count"] == 245, contract
assert rust["native_public_command_count"] == 169, rust
assert rust["missing_native_public_command_count"] == 76, rust
assert rust["native_coverage_ppm"] == 689795, rust
assert rust["python_launcher_bridge_command_count"] == 0, rust
assert "run artifact-put" in rust["native_public_commands"], rust
assert "run artifact-query" in rust["native_public_commands"], rust
assert "run artifact-stats" in rust["native_public_commands"], rust

missing = json.loads(
    Path("contracts/engine/r38-missing-native-commands-v1.json").read_text(
        encoding="utf-8"
    )
)
assert missing["python_public_command_count"] == 245, missing
assert missing["native_public_command_count"] == 169, missing
assert missing["missing_native_public_command_count"] == 76, missing
assert missing["python_launcher_bridge_command_count"] == 0, missing
assert "run artifact-stats" not in missing["missing_native_public_commands"], missing

selector = Path("crates/syntavra-cli/src/bin/syntavra.rs").read_text(
    encoding="utf-8"
)
assert "const NATIVE_COMMAND_COUNT: u64 = 169;" in selector

manifest = Path("MANIFEST.sha256").read_text(encoding="utf-8")
assert ".r38-evidence-rotate-key-certify.sh" not in manifest
assert ".r38-artifact-put" not in manifest
assert ".r38-artifact-query" not in manifest
assert ".r38-artifact-stats" not in manifest

print(
    json.dumps(
        {
            "ok": True,
            "native": 169,
            "missing": 76,
            "coverage_ppm": 689795,
            "bridge": 0,
        },
        sort_keys=True,
    )
)
PY

cargo check --locked -p syntavra-cli --bin syntavra
git diff --check
printf '\nR38_ARTIFACT_STATS_CERTIFICATION_OK\n'
'@

[System.IO.File]::WriteAllText(
    $validationScript,
    ($validationSource -replace "`r`n", "`n"),
    [System.Text.UTF8Encoding]::new($false)
)

Write-Host "`n=== DOCKER CERTIFICATION ===" -ForegroundColor Cyan
try {
    Invoke-Checked -FilePath "docker" -ArgumentList @(
        "run",
        "--rm",
        "--mount", "type=bind,source=$repoRoot,target=/workspace",
        "--mount", "type=bind,source=$validationScript,target=/certify.sh,readonly",
        "--workdir", "/workspace",
        "--env", "PYTHONPATH=/workspace",
        $ContainerImage,
        "bash",
        "/certify.sh"
    )
}
finally {
    Remove-Item -LiteralPath $validationScript -Force -ErrorAction SilentlyContinue
}

Write-Host "`n=== HOST-SIDE CLOSURE CHECK ===" -ForegroundColor Cyan
$afterHead = Get-GitOutput -Arguments @("rev-parse", "HEAD") -WorkingDirectory $repoRoot
if ($afterHead -ne $ExpectedHead) {
    throw "Validation clone HEAD'i beklenmedik biçimde değişti: $afterHead"
}
$status = Get-GitOutput -Arguments @("status", "--short") -WorkingDirectory $repoRoot
if ([string]::IsNullOrWhiteSpace($status)) {
    throw "Certification başarılı göründü ancak generated closure diff oluşmadı."
}
Invoke-Checked -FilePath "git" -ArgumentList @("-C", $repoRoot, "diff", "--check")

$contractPath = Join-Path $repoRoot "contracts\engine\dual-engine-public-surface-v2.json"
$contract = Get-Content -LiteralPath $contractPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([int]$contract.rust_surface.native_public_command_count -ne 169) {
    throw "Final native count 169 değil."
}
if ([int]$contract.rust_surface.missing_native_public_command_count -ne 76) {
    throw "Final missing count 76 değil."
}
if ([int]$contract.rust_surface.native_coverage_ppm -ne 689795) {
    throw "Final coverage ppm 689795 değil."
}
if ([int]$contract.rust_surface.python_launcher_bridge_command_count -ne 0) {
    throw "Bridge count sıfır değil."
}

Write-Host "`nGenerated diff:" -ForegroundColor Green
& git -C $repoRoot --no-pager diff --stat
if ($LASTEXITCODE -ne 0) {
    throw "git diff --stat başarısız oldu."
}

if ($Push) {
    Write-Host "`n=== ATOMIC COMMIT + PUSH ===" -ForegroundColor Cyan
    $remoteHead = Get-RemoteBranchHead -Remote $Repository -RemoteBranch $Branch
    if ($remoteHead -ne $ExpectedHead) {
        throw "Remote branch validation sırasında değişti. Beklenen=$ExpectedHead Remote=$remoteHead"
    }

    Invoke-Checked -FilePath "git" -ArgumentList @("-C", $repoRoot, "add", "-A")
    Invoke-Checked -FilePath "git" -ArgumentList @("-C", $repoRoot, "diff", "--cached", "--check")
    Invoke-Checked -FilePath "git" -ArgumentList @(
        "-C", $repoRoot,
        "-c", "user.name=github-actions[bot]",
        "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
        "commit", "-m", "R38: certify native artifact stats inventory"
    )
    Invoke-Checked -FilePath "git" -ArgumentList @(
        "-C", $repoRoot,
        "push", "origin", "HEAD:$Branch"
    )

    $finalHead = Get-GitOutput -Arguments @("rev-parse", "HEAD") -WorkingDirectory $repoRoot
    Write-Host "Certified exact head: $finalHead" -ForegroundColor Green
}
else {
    Write-Host "`nPush uygulanmadı. Atomik publish için aynı komutu -Push ile çalıştırın." -ForegroundColor Yellow
}

Write-Host "Validation clone: $repoRoot" -ForegroundColor Green
Write-Host "Native inventory target: 169/245; missing=76; coverage_ppm=689795; bridge=0" -ForegroundColor Green
