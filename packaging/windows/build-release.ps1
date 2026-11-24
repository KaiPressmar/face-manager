param(
    [switch]$RequireSigned
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent (Split-Path -Parent $scriptDir)
$distDir = Join-Path $projectRoot "dist"
$version = (Get-Content (Join-Path $projectRoot "VERSION") -Raw).Trim()
$buildDir = Join-Path $projectRoot "build"
$specPath = Join-Path $projectRoot "packaging/windows/face-manager.spec"
$desktopRequirements = Join-Path $projectRoot "backend/requirements-desktop.txt"
$gpuRequirements = Join-Path $projectRoot "backend/requirements-desktop-gpu.txt"
$versionInfoPath = Join-Path $buildDir "windows-version-info.txt"
$dependencyInventoryPath = Join-Path $buildDir "dependency-inventory.json"
$buildVenv = Join-Path $buildDir "windows-venv"
$buildPython = Join-Path $buildVenv "Scripts/python.exe"

Set-Location $projectRoot

function Invoke-PipInstallWithRetry {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [int]$MaxAttempts = 3
    )

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        try {
            & $buildPython -m pip install @Arguments
            return
        } catch {
            if ($attempt -eq $MaxAttempts) {
                throw
            }

            $delaySeconds = 15 * $attempt
            Write-Warning "pip install attempt $attempt failed; retrying in $delaySeconds seconds"
            Start-Sleep -Seconds $delaySeconds
        }
    }
}

python -c "import sys, sysconfig; sys.exit('Use x64 CPython 3.12 to build the Windows installer.') if sys.version_info[:2] != (3, 12) or sysconfig.get_platform() != 'win-amd64' else None"

if (Test-Path $distDir) {
    Remove-Item -Recurse -Force $distDir
}
if (Test-Path $buildDir) {
    Remove-Item -Recurse -Force $buildDir
}
New-Item -ItemType Directory -Force $buildDir | Out-Null
python -m venv $buildVenv

npm --prefix frontend ci
npm --prefix frontend run build

& $buildPython scripts/inventory-dependencies.py --project-root $projectRoot --output $dependencyInventoryPath
& $buildPython packaging/windows/generate-version-info.py --version $version --output $versionInfoPath

Invoke-PipInstallWithRetry @("--upgrade", "pip")
Invoke-PipInstallWithRetry @("-r", "backend/requirements.txt", "-r", $desktopRequirements, "-r", $gpuRequirements)
& $buildPython -m pip check

& $buildPython -m PyInstaller --noconfirm --clean $specPath

$appExe = Join-Path $distDir "FaceManager/FaceManager.exe"
if (-not (Test-Path $appExe)) {
    throw "PyInstaller did not create $appExe"
}
& $buildPython packaging/windows/cuda_runtime.py (Split-Path -Parent $appExe)
& $buildPython packaging/windows/openvino_runtime.py (Split-Path -Parent $appExe)

$appVersionInfo = (Get-Item $appExe).VersionInfo
if ($appVersionInfo.ProductVersion -ne $version) {
    throw "FaceManager.exe product version is '$($appVersionInfo.ProductVersion)', expected '$version'"
}
if ($appVersionInfo.ProductName -ne "Face Manager") {
    throw "FaceManager.exe product name is '$($appVersionInfo.ProductName)', expected 'Face Manager'"
}

$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) {
    throw "Inno Setup 6 is not installed at $iscc"
}

& $iscc `
    "/DAppVersion=$version" `
    "/DSourceDir=$projectRoot" `
    "/DOutputDir=$distDir" `
    (Join-Path $projectRoot "packaging/windows/FaceManager.iss")

$installerPath = Join-Path $distDir "FaceManager-Setup-$version.exe"
if (-not (Test-Path $installerPath)) {
    throw "Inno Setup did not create $installerPath"
}
if ((Get-Item $installerPath).Length -ge 2GB) {
    throw "The Windows installer must stay below GitHub's 2 GiB release asset limit."
}

$installerVersionInfo = (Get-Item $installerPath).VersionInfo
$installerProductVersion = $installerVersionInfo.ProductVersion.Trim()
if ($installerProductVersion -ne $version) {
    throw "Installer product version is '$installerProductVersion', expected '$version'"
}

if ($env:CI -eq "true") {
    $smokeDirectory = Join-Path $buildDir "installed-smoke"
    $setup = Start-Process -PassThru -Wait -FilePath $installerPath -ArgumentList @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=`"$smokeDirectory`""
    )
    if ($setup.ExitCode -ne 0) {
        throw "Windows installer smoke test failed with exit code $($setup.ExitCode)."
    }
    $reportPath = Join-Path $buildDir "installed-runtime-check.txt"
    $smoke = Start-Process -PassThru -FilePath (Join-Path $smokeDirectory "FaceManager.exe") `
        -ArgumentList "--check-runtime `"$reportPath`""
    if (-not $smoke.WaitForExit(120000)) {
        $smoke.Kill()
        throw "Installed Face Manager runtime check timed out."
    }
    $report = if (Test-Path $reportPath) { Get-Content -Raw $reportPath } else { "No runtime report" }
    if ($smoke.ExitCode -ne 0 -or $report -notlike "Face Manager $version,* CPU inference passed.*") {
        throw "Installed Face Manager runtime check failed with exit code $($smoke.ExitCode): $report"
    }
    Write-Host $report
}

foreach ($artifact in @($appExe, $installerPath)) {
    $signature = Get-AuthenticodeSignature $artifact
    if ($RequireSigned -and $signature.Status -ne "Valid") {
        throw "$artifact does not have a valid Authenticode signature (status: $($signature.Status))"
    }
}

$checksumPath = "$installerPath.sha256"
$checksum = (Get-FileHash -Algorithm SHA256 $installerPath).Hash.ToLowerInvariant()
"$checksum  $(Split-Path -Leaf $installerPath)" | Set-Content -Encoding ascii $checksumPath

Write-Host "Built installer: $installerPath"
Write-Host "SHA-256 checksum: $checksumPath"
Write-Host "Dependency inventory: $dependencyInventoryPath"
