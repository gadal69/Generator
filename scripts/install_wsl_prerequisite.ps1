$ErrorActionPreference = 'Stop'
$workspaceRoot = Split-Path -Parent $PSScriptRoot
$statusPath = Join-Path $workspaceRoot 'docs\WSL_INSTALL_STATUS.json'
$logPath = Join-Path $workspaceRoot 'docs\WSL_INSTALL_LOG.txt'
$result = @{ status = 'STARTED'; started_at = (Get-Date).ToString('o'); automatic_restart = $false }
try {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Administrator privileges are required.'
    }
    Start-Transcript -Path $logPath -Append | Out-Null
    & "$env:SystemRoot\System32\wsl.exe" --install --no-distribution --web-download
    $result.exit_code = $LASTEXITCODE
    $result.status = if ($LASTEXITCODE -in @(0, 3010)) { 'INSTALL_COMMAND_COMPLETED_CHECK_RESTART' } else { 'INSTALL_FAILED' }
    Stop-Transcript | Out-Null
} catch {
    $result.status = 'FAILED'
    $result.error = $_.Exception.Message
} finally {
    $result.finished_at = (Get-Date).ToString('o')
    $result | ConvertTo-Json | Set-Content -LiteralPath $statusPath -Encoding UTF8
}
