param(
    [ValidateSet("cpu", "gpu")]
    [string]$Variant = "cpu",
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
$buildVariantPath = Join-Path $buildDir "BUILD_VARIANT"

Set-Location $projectRoot

if (Test-Path $distDir) {
    Remove-Item -Recurse -Force $distDir
}
if (Test-Path $buildDir) {
    Remove-Item -Recurse -Force $buildDir
}
New-Item -ItemType Directory -Force $buildDir | Out-Null
Set-Content -Path $buildVariantPath -Value $Variant -Encoding ascii
$env:FACE_MANAGER_BUILD_VARIANT = $Variant

npm --prefix frontend ci
npm --prefix frontend run build

python scripts/inventory-dependencies.py --project-root $projectRoot --output $dependencyInventoryPath
python packaging/windows/generate-version-info.py --version $version --output $versionInfoPath

python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt -r $desktopRequirements

if ($Variant -eq "gpu") {
    python -m pip uninstall -y onnxruntime
    python -m pip install -r $gpuRequirements
} else {
    python -m pip install "onnxruntime>=1.21,<2"
}

pyinstaller --noconfirm --clean $specPath

$appExe = Join-Path $distDir "FaceManager/FaceManager.exe"
if (-not (Test-Path $appExe)) {
    throw "PyInstaller did not create $appExe"
}

$appVersionInfo = (Get-Item $appExe).VersionInfo
if ($appVersionInfo.ProductVersion -ne $version) {
    throw "FaceManager.exe product version is '$($appVersionInfo.ProductVersion)', expected '$version'"
}
if ($appVersionInfo.ProductName -ne "Face Manager") {
    throw "FaceManager.exe product name is '$($appVersionInfo.ProductName)', expected 'Face Manager'"
}

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

$installerPath = Join-Path $distDir "FaceManager-Setup$installerSuffix-$version.exe"
if (-not (Test-Path $installerPath)) {
    throw "Inno Setup did not create $installerPath"
}

$installerVersionInfo = (Get-Item $installerPath).VersionInfo
$installerProductVersion = $installerVersionInfo.ProductVersion.Trim()
if ($installerProductVersion -ne $version) {
    throw "Installer product version is '$installerProductVersion', expected '$version'"
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
