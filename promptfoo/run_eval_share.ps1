param(
    [string]$Config = "redteam-eval.yaml",
    [switch]$Share,
    [switch]$UploadLatest
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")
$EnvPath = Join-Path $ProjectRoot ".env"

function Load-DotEnv {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        return
    }

    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) {
            return
        }

        $parts = $line.Split("=", 2)
        if ($parts.Count -ne 2) {
            return
        }

        $name = $parts[0].Trim()
        $value = $parts[1].Trim()

        if (($value.StartsWith("'") -and $value.EndsWith("'")) -or ($value.StartsWith('"') -and $value.EndsWith('"'))) {
            $value = $value.Substring(1, $value.Length - 2)
        }

        if ($name) {
            [System.Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

Load-DotEnv -Path $EnvPath
Set-Location $ScriptDir

if (-not $env:PROMPTFOO_API_KEY) {
    throw "PROMPTFOO_API_KEY is missing. Add it to project .env first."
}

Write-Host "Authenticating promptfoo..."
promptfoo auth login -k $env:PROMPTFOO_API_KEY

$evalArgs = @("eval", "--config", $Config)
if ($Share) {
    $evalArgs += "--share"
}

Write-Host "Running promptfoo $($evalArgs -join ' ')"
promptfoo @evalArgs

if ($UploadLatest) {
    Write-Host "Uploading latest eval..."
    promptfoo share
}
