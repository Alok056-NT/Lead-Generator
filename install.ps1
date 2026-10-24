# Lead Generator installer for Windows (PowerShell 5.1 or newer).
#
# One-line install (paste into PowerShell):
#   irm https://raw.githubusercontent.com/Alok056-NT/Lead-Generator/main/install.ps1 | iex
#
# Safe to run again: it upgrades the existing installation in place.
#
# Optional settings (set before running, for example: $env:LEADGEN_REF = "v1.0.0"):
#   LEADGEN_REF         branch or tag to install (default: main)
#   LEADGEN_SOURCE      install from this pip source instead (a folder or URL; for testing)
#   LEADGEN_VENV        where to put the private Python environment
#   LEADGEN_YES=1       answer "yes" to the Claude Desktop question
#   LEADGEN_NO_CONNECT=1  do not offer to connect Claude Desktop

$ErrorActionPreference = 'Continue'

$Repo = 'Alok056-NT/Lead-Generator'
$Ref = if ($env:LEADGEN_REF) { $env:LEADGEN_REF } else { 'main' }
$VenvDir = if ($env:LEADGEN_VENV) { $env:LEADGEN_VENV } else { Join-Path $env:LOCALAPPDATA 'LeadGenerator\venv' }

function Say($text) { Write-Host $text }
function Fail($text) { Write-Host ''; Write-Host $text -ForegroundColor Red; Write-Host ''; return }

function Test-PythonCommand($exe, $extra) {
    # True when this command runs Python 3.11 or newer.
    try {
        $out = & $exe @extra -c "import sys; print('ok' if sys.version_info >= (3, 11) else 'old')" 2>$null
        return ($LASTEXITCODE -eq 0 -and "$out".Trim() -eq 'ok')
    } catch {
        return $false
    }
}

function Find-Python {
    # Returns @(exe, extra-args) for a suitable Python, or $null.
    $candidates = @(
        @('py', @('-3.13')), @('py', @('-3.12')), @('py', @('-3.11')), @('py', @('-3')),
        @('python3', @()), @('python', @())
    )
    foreach ($c in $candidates) {
        if (-not (Get-Command $c[0] -ErrorAction SilentlyContinue)) { continue }
        if (Test-PythonCommand $c[0] $c[1]) { return ,$c }
    }
    return $null
}

Say ''
Say 'Lead Generator installer'
Say '------------------------'

# 1. Python
$py = Find-Python
if (-not $py) {
    Fail 'Python 3.11 or newer is needed and was not found.'
    Say 'I will not install system software without asking you. To install Python, run:'
    Say ''
    Say '    winget install --id Python.Python.3.12 -e'
    Say ''
    Say 'Then close this window, open a new PowerShell window and run the installer again.'
    return
}
$pyExe = $py[0]
$pyArgs = $py[1]
Say ('Python found: ' + ((& $pyExe @pyArgs --version 2>&1) -join ' '))

# 2. Private environment (a venv keeps this away from your other Python programs)
$venvPython = Join-Path $VenvDir 'Scripts\python.exe'
$venvOk = $false
if (Test-Path $venvPython) {
    & $venvPython -c "import sys" 2>$null
    $venvOk = ($LASTEXITCODE -eq 0)
}
if (-not $venvOk) {
    Say "Creating the private Python environment in $VenvDir ..."
    New-Item -ItemType Directory -Force -Path (Split-Path $VenvDir) | Out-Null
    & $pyExe @pyArgs -m venv --clear $VenvDir
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) {
        Fail 'I could not create the Python environment. Check that you can write to the folder above.'
        return
    }
} else {
    Say "Using the existing environment in $VenvDir (upgrading in place)."
}

# 3. Install / upgrade Lead Generator
& $venvPython -m pip install --quiet --upgrade pip 2>&1 | Out-Null
if ($env:LEADGEN_SOURCE) {
    $requirement = $env:LEADGEN_SOURCE
} elseif (Get-Command git -ErrorAction SilentlyContinue) {
    $requirement = "lead-generator @ git+https://github.com/$Repo.git@$Ref"
} else {
    # No git on this computer: GitHub's zip download works without it.
    $requirement = "lead-generator @ https://github.com/$Repo/archive/$Ref.zip"
}
Say 'Installing Lead Generator (this can take a few minutes the first time) ...'
# Remove the old copy first so an upgrade always takes the new files (its libraries stay).
& $venvPython -m pip uninstall --yes lead-generator 2>&1 | Out-Null
& $venvPython -m pip install --quiet --upgrade $requirement
if ($LASTEXITCODE -ne 0) {
    Fail 'The installation failed. Check your internet connection and run the installer again.'
    return
}
$leadgen = Join-Path $VenvDir 'Scripts\leadgen.exe'
if (-not (Test-Path $leadgen)) {
    Fail 'The installation finished but the leadgen program is missing. Run the installer again.'
    return
}

# 4. Health check
Say ''
Say 'Checking the installation ...'
& $leadgen doctor
$doctorOk = ($LASTEXITCODE -eq 0)

# 4b. A "Lead Generator" button on the Desktop and in the Start menu (a click opens the app)
function New-LeadGenShortcut($path) {
    try {
        $shell = New-Object -ComObject WScript.Shell
        $link = $shell.CreateShortcut($path)
        $link.TargetPath = Join-Path $VenvDir 'Scripts\pythonw.exe'
        $link.Arguments = '-m leadgen app'
        $link.WorkingDirectory = $env:USERPROFILE
        $link.Description = 'Lead Generator'
        $link.IconLocation = (Join-Path $VenvDir 'Scripts\pythonw.exe') + ',0'
        $link.Save()
        return $true
    } catch {
        return $false
    }
}
$madeButton = $false
if (Test-Path (Join-Path $VenvDir 'Scripts\pythonw.exe')) {
    $desktopLink = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Lead Generator.lnk'
    $menuLink = Join-Path ([Environment]::GetFolderPath('Programs')) 'Lead Generator.lnk'
    $first = New-LeadGenShortcut $desktopLink
    $second = New-LeadGenShortcut $menuLink
    $madeButton = ($first -or $second)
}
Say ''
if ($madeButton) {
    Say 'Created the Lead Generator button on your Desktop and in the Start menu.'
} else {
    Say 'I could not create the Lead Generator button. You can still open the app with: leadgen app'
}

# 5. Offer to connect Claude Desktop
if (-not $env:LEADGEN_NO_CONNECT) {
    Say ''
    $answer = 'y'
    if (-not $env:LEADGEN_YES) {
        $reply = Read-Host 'Connect Claude Desktop to Lead Generator now? (Y/n)'
        if ($reply -and $reply.Trim().ToLower().StartsWith('n')) { $answer = 'n' }
    }
    if ($answer -eq 'y') {
        & $leadgen connect claude
    } else {
        Say 'Skipped. You can do it later with:'
        Say ('    "' + $leadgen + '" connect claude')
    }
}

# 6. Next steps
Say ''
Say '=============================================================='
if ($doctorOk) { Say 'Lead Generator is installed.' } else { Say 'Lead Generator is installed, but the health check found a problem (see above).' }
Say '=============================================================='
Say ''
Say 'What to do next:'
Say '  1. Click the Lead Generator button on your Desktop (or find it in the Start menu).'
Say '  2. If Claude Desktop is open, quit it completely (right-click its icon in the'
Say '     system tray, then Quit) and open it again.'
Say '  3. In a new chat, ask:  What is my Lead Generator status?'
Say '  4. To add your free API keys on a simple web page, run:'
Say ('       "' + $leadgen + '" dashboard --open')
Say '     (the page opens at http://127.0.0.1:8765 - or ask Claude to "open the dashboard")'
Say '  5. To use ChatGPT instead, run:'
Say ('       "' + $leadgen + '" connect chatgpt')
Say '  6. To use Claude Code, run:'
Say ('       "' + $leadgen + '" connect claude-code')
Say ''
Say 'Run this installer again at any time to upgrade.'
$workspace = if ($env:LEADGEN_HOME) { $env:LEADGEN_HOME } else { Join-Path $env:USERPROFILE 'LeadGenerator' }
Say ('Your leads and keys stay in ' + $workspace + ' and are not touched by upgrades.')
Say ''
