# Install the ai-product-video skill for Claude Code and/or Codex on Windows.
# Usage: powershell -ExecutionPolicy Bypass -File .\install.ps1 [claude|codex|all]   (default: all)
param([ValidateSet('claude', 'codex', 'all')][string]$Target = 'all')
$ErrorActionPreference = 'Stop'
$src = $PSScriptRoot
$name = 'ai-product-video'

function Install-To([string]$root) {
    $dest = Join-Path $root $name
    New-Item -ItemType Directory -Force -Path $root | Out-Null
    if (Test-Path $dest) { Remove-Item -Recurse -Force -Path $dest }
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    # copy everything except git metadata and caches
    robocopy $src $dest /E /XD .git .cache __pycache__ /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "copy failed ($LASTEXITCODE)" }
    Write-Host "installed -> $dest"
}

$claude = Join-Path $HOME '.claude\skills'
$codex = Join-Path $HOME '.agents\skills'
switch ($Target) {
    'claude' { Install-To $claude }
    'codex' { Install-To $codex }
    'all' { Install-To $claude; Install-To $codex }
}
Write-Host 'Restart Claude Code / Codex if the skill does not show up.'
exit 0
