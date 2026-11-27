param(
    [Parameter(Mandatory = $true)]
    [string]$Version
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
$root = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$testRoot = Join-Path $root "build/upgrade-smoke"
$legacyRepository = "KaiPressmar/face-manager"
$registryPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{0E1A7413-4FD3-4A24-A59A-3A4B359EA5D0}_is1"
$machineRegistryPath = $registryPath.Replace("HKCU:", "HKLM:")
$savedDataDirectory = $env:FACE_MANAGER_DATA_DIR

function Invoke-Setup {
    param([string]$Path, [string[]]$ExtraArguments = @())
    $arguments = @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") + $ExtraArguments
    $process = Start-Process -FilePath $Path -ArgumentList $arguments -PassThru
    if (-not $process.WaitForExit(300000)) {
        $process.Kill($true)
        throw "Windows setup did not finish in five minutes: $Path"
    }
    return $process.ExitCode
}

function Get-FileDigest {
    param([string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Write-UpgradeTiming {
    param([string]$Fixture, [string]$Phase, [Diagnostics.Stopwatch]$Timer)
    $Timer.Stop()
    Write-Host ("Upgrade timing [{0}] {1}: {2:F2} s" -f $Fixture, $Phase, $Timer.Elapsed.TotalSeconds)
}

$createLibrary = @'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as connection:
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("CREATE TABLE library_probe (label TEXT NOT NULL)")
    connection.execute("INSERT INTO library_probe VALUES ('Existing library')")
'@
$verifyLibrary = @'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as connection:
    assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
    assert connection.execute("SELECT label FROM library_probe").fetchall() == [("Existing library",)]
'@

$fixtures = @(
    @{ Version = "0.6.1"; Suffix = ""; TestFailure = $false; UseDefaultData = $true },
    @{ Version = "0.13.0"; Suffix = "-GPU"; TestFailure = $true; UseDefaultData = $false },
    @{ Version = "0.14.2"; Suffix = ""; TestFailure = $false; UseDefaultData = $true }
)

try {
    foreach ($fixture in $fixtures) {
        $legacyVersion = $fixture.Version
        $suffix = $fixture.Suffix
        $legacyName = "FaceManager-Setup${suffix}-${legacyVersion}.exe"
        $fixtureRoot = Join-Path $testRoot "${legacyVersion}${suffix}"
        $installDirectory = Join-Path $fixtureRoot "Face Manager custom directory"
        New-Item -ItemType Directory -Path $fixtureRoot -Force | Out-Null

        $timer = [Diagnostics.Stopwatch]::StartNew()
        gh release download "v$legacyVersion" --repo $legacyRepository --dir $fixtureRoot `
            --pattern $legacyName --pattern "$legacyName.sha256"
        $legacyPath = Join-Path $fixtureRoot $legacyName
        $legacyDigest = Get-FileDigest $legacyPath
        $expectedChecksum = "$legacyDigest  $legacyName"
        $downloadedChecksum = Get-Content -LiteralPath "$legacyPath.sha256" -Raw
        Write-UpgradeTiming $legacyName "Download and verify fixture" $timer
        if ($downloadedChecksum.TrimEnd() -cne $expectedChecksum) {
            throw "The published Windows fixture checksum does not match $legacyName."
        }

        $log = Join-Path $fixtureRoot "setup.log"
        $candidate = Join-Path $root "dist/FaceManager-Setup${suffix}-${Version}.exe"
        $liveApp = Join-Path $installDirectory "FaceManager.exe"
        $madeMachineRecord = $false
        $madeDefaultData = $false
        try {
            if ($fixture.UseDefaultData) {
                $env:FACE_MANAGER_DATA_DIR = $null
                $dataDirectory = Join-Path $env:LOCALAPPDATA "FaceManager"
                if (Test-Path -LiteralPath $dataDirectory) {
                    throw "The isolated runner already has a default Face Manager data folder."
                }
                New-Item -ItemType Directory -Path $dataDirectory | Out-Null
                $madeDefaultData = $true
            } else {
                $dataDirectory = Join-Path $fixtureRoot "Configured library"
                $env:FACE_MANAGER_DATA_DIR = $dataDirectory
                New-Item -ItemType Directory -Path $dataDirectory | Out-Null
            }
            $database = Join-Path $dataDirectory "database.sqlite"
            $timer.Restart()
            $legacyExit = Invoke-Setup $legacyPath @("/DIR=`"$installDirectory`"")
            Write-UpgradeTiming $legacyName "Install legacy version" $timer
            if ($legacyExit -ne 0) {
                throw "Installing $legacyName failed with exit code $legacyExit."
            }
            $priorAppDigest = Get-FileDigest $liveApp
            & python -c $createLibrary $database
            $priorDatabaseDigest = Get-FileDigest $database

            if ($fixture.TestFailure) {
                if (Test-Path -LiteralPath $machineRegistryPath) {
                    throw "The isolated runner already has a machine-wide Face Manager registration."
                }
                New-Item -Path $machineRegistryPath | Out-Null
                $madeMachineRecord = $true
                New-ItemProperty -LiteralPath $machineRegistryPath -Name InstallLocation -Value $testRoot | Out-Null
                Remove-ItemProperty -LiteralPath $registryPath -Name InstallLocation

                $lockedFile = Join-Path $installDirectory "_internal-$Version/onnxruntime/capi/onnxruntime.dll"
                New-Item -ItemType Directory -Path (Split-Path -Parent $lockedFile) -Force | Out-Null
                Set-Content -LiteralPath $lockedFile -Value "File held open until the upgrade fails."
                $lock = [System.IO.File]::Open($lockedFile, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
                try {
                    $timer.Restart()
                    $failedExit = Invoke-Setup $candidate @("/LOG=`"$log`"")
                    Write-UpgradeTiming $legacyName "Run rollback scenario installer" $timer
                    if ($failedExit -eq 0) {
                        throw "The candidate reported success while a new runtime DLL was locked."
                    }
                    if ((Get-FileDigest $liveApp) -ne $priorAppDigest -or
                        -not (Test-Path (Join-Path $installDirectory "_internal")) -or
                        (Get-ItemProperty -LiteralPath $registryPath).DisplayVersion -ne $legacyVersion -or
                        -not (Select-String -LiteralPath $log -SimpleMatch "Saved the prior executable for recovery" -Quiet)) {
                        throw "The failed setup did not retain the prior app and runtime."
                    }
                    & python -c $verifyLibrary $database
                } finally {
                    $lock.Dispose()
                    Remove-Item -LiteralPath $lockedFile -Force
                }
            }

            $timer.Restart()
            $upgradeExit = Invoke-Setup $candidate @("/LOG=`"$log`"")
            Write-UpgradeTiming $legacyName "Install candidate" $timer
            if ($upgradeExit -ne 0) {
                throw "Upgrading from $legacyName failed with exit code $upgradeExit."
            }
            $record = Get-ItemProperty -LiteralPath $registryPath
            if ($record.DisplayVersion -ne $Version -or $record.InstallLocation.TrimEnd('\') -ne $installDirectory -or
                (Get-Item -LiteralPath $liveApp).VersionInfo.ProductVersion -ne $Version -or
                -not (Test-Path (Join-Path $installDirectory "_internal-$Version"))) {
                throw "The upgrade did not replace the app and install the new runtime in its existing installation directory."
            }
            if ($fixture.UseDefaultData -and (Test-Path (Join-Path $installDirectory "_internal"))) {
                throw "The successful setup left the old runtime with the default library configuration."
            }
            if ($madeMachineRecord -and
                (Get-ItemProperty -LiteralPath $machineRegistryPath).InstallLocation -ne $testRoot) {
                throw "The per-user upgrade changed the machine-wide registration."
            }
            if ((Get-FileDigest $database) -ne $priorDatabaseDigest) {
                throw "Setup modified the original library from $legacyName."
            }
            $backupDirectory = Join-Path $dataDirectory "update-backups"
            $snapshot = Get-ChildItem -LiteralPath $backupDirectory -Filter "database-$legacyVersion-*.sqlite" |
                Select-Object -First 1
            if ($null -eq $snapshot) {
                throw "Setup did not generate a recoverable pre-upgrade library backup."
            }
            & python -c $verifyLibrary $snapshot.FullName

            $runtimeReport = Join-Path $fixtureRoot "runtime.txt"
            $timer.Restart()
            $process = Start-Process -FilePath $liveApp -ArgumentList "--check-runtime `"$runtimeReport`"" -PassThru
            if (-not $process.WaitForExit(120000)) {
                $process.Kill($true)
                throw "Installed runtime check timed out on the upgrade from $legacyName."
            }
            Write-UpgradeTiming $legacyName "Check installed runtime" $timer
            if ($process.ExitCode -ne 0 -or (Get-Content -LiteralPath $runtimeReport -Raw) -notlike "Face Manager $Version,* CPU inference passed.*") {
                throw "The upgraded runtime from $legacyName did not pass its CPU inference check."
            }
            Write-Host "Upgraded $legacyName in place and verified the original library, backup and installed runtime."
        } catch {
            if (Test-Path -LiteralPath $log) {
                Get-Content -LiteralPath $log -Tail 60 | Write-Host
            }
            throw
        } finally {
            $timer.Restart()
            if ($madeMachineRecord) {
                Remove-Item -LiteralPath $machineRegistryPath -Force
            }
            $uninstaller = Join-Path $installDirectory "unins000.exe"
            if ((Test-Path -LiteralPath $uninstaller) -and (Invoke-Setup $uninstaller) -ne 0) {
                throw "Uninstalling the fixture at $installDirectory failed."
            }
            if ($madeDefaultData) {
                Remove-Item -LiteralPath $dataDirectory -Recurse -Force
            }
            Remove-Item -LiteralPath $fixtureRoot -Recurse -Force
            Write-UpgradeTiming $legacyName "Clean up fixture" $timer
        }
    }
} finally {
    $env:FACE_MANAGER_DATA_DIR = $savedDataDirectory
}
