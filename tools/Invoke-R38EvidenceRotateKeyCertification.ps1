#requires -Version 7.2
[CmdletBinding()]
param(
    [string]$ExpectedHead = "54e0e9daf95e1b163cb98bdf5c33195ef537d908",
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
        [Parameter(Mandatory)]
        [string]$FilePath,
        [Parameter()]
        [string[]]$ArgumentList = @(),
        [Parameter()]
        [string]$WorkingDirectory = (Get-Location).Path
    )

    Write-Host ("> {0} {1}" -f $FilePath, ($ArgumentList -join " ")) -ForegroundColor DarkGray
    $process = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $ArgumentList `
        -WorkingDirectory $WorkingDirectory `
        -NoNewWindow `
        -Wait `
        -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Komut başarısız oldu (exit=$($process.ExitCode)): $FilePath $($ArgumentList -join ' ')"
    }
}

function Get-CommandPath {
    param([Parameter(Mandatory)][string]$Name)

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "Gerekli komut bulunamadı: $Name"
    }
    return $command.Source
}

function Get-GitOutput {
    param(
        [Parameter(Mandatory)]
        [string[]]$Arguments,
        [Parameter(Mandatory)]
        [string]$WorkingDirectory
    )

    $output = & git -C $WorkingDirectory @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "git komutu başarısız oldu: git -C $WorkingDirectory $($Arguments -join ' ')`n$output"
    }
    return (($output | Out-String).Trim())
}

$null = Get-CommandPath -Name "git"
$null = Get-CommandPath -Name "docker"

$dockerInfo = & docker info --format "{{json .ServerVersion}}" 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop hazır değil. Docker Desktop'ı açıp yeniden çalıştırın.`n$dockerInfo"
}

New-Item -ItemType Directory -Path $WorkingRoot -Force | Out-Null
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$repoRoot = Join-Path $WorkingRoot "Syntavra-r38-evidence-rotate-key-cert-$timestamp"

Write-Host "`n=== TEMİZ EXACT-HEAD CLONE ===" -ForegroundColor Cyan
Invoke-Checked -FilePath "git" -ArgumentList @(
    "clone",
    "--no-tags",
    "--filter=blob:none",
    "--branch", $Branch,
    $Repository,
    $repoRoot
)

$head = Get-GitOutput -Arguments @("rev-parse", "HEAD") -WorkingDirectory $repoRoot
if ($head -ne $ExpectedHead) {
    throw "Exact head uyuşmuyor. Beklenen=$ExpectedHead Gerçek=$head"
}

$remoteLine = (& git ls-remote $Repository "refs/heads/$Branch" 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($remoteLine)) {
    throw "Remote branch head okunamadı: $Branch`n$remoteLine"
}
$remoteHead = ($remoteLine -split "\s+")[0]
if ($remoteHead -ne $ExpectedHead) {
    throw "Remote branch işlem başlamadan değişti. Beklenen=$ExpectedHead Remote=$remoteHead"
}

$validationScript = Join-Path $repoRoot ".r38-evidence-rotate-key-certify.sh"
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

python -m pip install --disable-pip-version-check -e . pytest

python tools/repair_r38_evidence_rotate_key_contract.py
python tools/advance_r38_evidence_rotate_key_inventory.py
cargo fmt --all
cargo check --locked -p syntavra-cli --bin syntavra
git diff --binary > /tmp/evidence-rotation-first.diff
python tools/repair_r38_evidence_rotate_key_contract.py
python tools/advance_r38_evidence_rotate_key_inventory.py
cargo fmt --all
git diff --binary > /tmp/evidence-rotation-second.diff
cmp /tmp/evidence-rotation-first.diff /tmp/evidence-rotation-second.diff

cargo build --locked -p syntavra-cli --bin syntavra
SYNTAVRA_R38_SELECTOR="$PWD/target/debug/syntavra" \
  python -m pytest -q \
    tests/runtime/test_native_compress_describe_r38.py \
    tests/runtime/test_native_compress_put_r38.py \
    tests/runtime/test_native_compress_get_r38.py \
    tests/runtime/test_native_compress_verify_r38.py \
    tests/runtime/test_native_evidence_get_r38.py \
    tests/runtime/test_native_evidence_rotate_key_r38.py

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
assert rust["native_public_command_count"] == 166, rust
assert rust["missing_native_public_command_count"] == 79, rust
assert rust["native_coverage_ppm"] == 677551, rust
assert rust["python_launcher_bridge_command_count"] == 0, rust
assert "evidence rotate-key" in rust["native_public_commands"], rust

missing = json.loads(
    Path("contracts/engine/r38-missing-native-commands-v1.json").read_text(
        encoding="utf-8"
    )
)
assert missing["native_public_command_count"] == 166, missing
assert missing["missing_native_public_command_count"] == 79, missing
assert "evidence rotate-key" not in missing["missing_native_public_commands"], missing

selector = Path("crates/syntavra-cli/src/bin/syntavra.rs").read_text(
    encoding="utf-8"
)
assert "const NATIVE_COMMAND_COUNT: u64 = 166;" in selector
print(
    json.dumps(
        {
            "ok": True,
            "native": 166,
            "missing": 79,
            "coverage_ppm": 677551,
            "bridge": 0,
        },
        sort_keys=True,
    )
)
PY

cargo check --locked -p syntavra-cli --bin syntavra
git diff --check
printf '\nR38_EVIDENCE_ROTATE_KEY_CERTIFICATION_OK\n'
'@

[System.IO.File]::WriteAllText(
    $validationScript,
    ($validationSource -replace "`r`n", "`n"),
    [System.Text.UTF8Encoding]::new($false)
)

Write-Host "`n=== DOCKER CERTIFICATION ===" -ForegroundColor Cyan
$dockerVolume = "${repoRoot}:/workspace"
try {
    Invoke-Checked -FilePath "docker" -ArgumentList @(
        "run",
        "--rm",
        "--mount", "type=bind,source=$repoRoot,target=/workspace",
        "--workdir", "/workspace",
        "--env", "PYTHONPATH=/workspace",
        $ContainerImage,
        "bash",
        "/workspace/.r38-evidence-rotate-key-certify.sh"
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
if ([int]$contract.rust_surface.native_public_command_count -ne 166) {
    throw "Final native count 166 değil."
}
if ([int]$contract.rust_surface.missing_native_public_command_count -ne 79) {
    throw "Final missing count 79 değil."
}
if ([int]$contract.rust_surface.python_launcher_bridge_command_count -ne 0) {
    throw "Bridge count sıfır değil."
}

Write-Host "`nGenerated diff:" -ForegroundColor Green
& git -C $repoRoot diff --stat
if ($LASTEXITCODE -ne 0) {
    throw "git diff --stat başarısız oldu."
}

if ($Push) {
    Write-Host "`n=== ATOMIC COMMIT + PUSH ===" -ForegroundColor Cyan
    $remoteLine = (& git ls-remote $Repository "refs/heads/$Branch" 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($remoteLine)) {
        throw "Push öncesi remote branch okunamadı.`n$remoteLine"
    }
    $remoteHead = ($remoteLine -split "\s+")[0]
    if ($remoteHead -ne $ExpectedHead) {
        throw "Remote branch validation sırasında değişti. Beklenen=$ExpectedHead Remote=$remoteHead"
    }

    Invoke-Checked -FilePath "git" -ArgumentList @("-C", $repoRoot, "add", "-A")
    Invoke-Checked -FilePath "git" -ArgumentList @("-C", $repoRoot, "diff", "--cached", "--check")
    Invoke-Checked -FilePath "git" -ArgumentList @(
        "-C", $repoRoot,
        "commit", "-m", "R38: certify native evidence key rotation inventory"
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
Write-Host "Native inventory target: 166/245; missing=79; coverage_ppm=677551; bridge=0" -ForegroundColor Green
