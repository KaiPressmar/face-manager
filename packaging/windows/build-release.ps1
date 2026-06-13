param(
    [ValidateSet("cpu", "gpu")]
    [string]$Variant = "cpu"
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

Set-Location $projectRoot

if (Test-Path $distDir) {
    Remove-Item -Recurse -Force $distDir
}
if (Test-Path $buildDir) {
    Remove-Item -Recurse -Force $buildDir
}

npm --prefix frontend ci
npm --prefix frontend run build

python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt -r $desktopRequirements

if ($Variant -eq "gpu") {
    python -m pip uninstall -y onnxruntime
    python -m pip install -r $gpuRequirements
} else {
    python -m pip install "onnxruntime>=1.21,<2"
}

pyinstaller --noconfirm --clean $specPath

$installerSuffix = if ($Variant -eq "gpu") { "-GPU" } else { "" }

$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) {
    throw "Inno Setup 6 is not installed at $iscc"
}

& $iscc `
    "/DAppVersion=$version" `
    "/DSourceDir=$projectRoot" `
    "/DOutputDir=$distDir" `
    "/DInstallerSuffix=$installerSuffix" `
    (Join-Path $projectRoot "packaging/windows/FaceManager.iss")
