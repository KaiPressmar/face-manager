#requires -Version 7.3
#requires -RunAsAdministrator

param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-zA-Z0-9_.-]+$')]
    [string]$Distribution
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$repository = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$wsl = (Get-Command wsl.exe).Source
$python = (Get-Command python.exe).Source

function Invoke-Linux([string[]]$Arguments) {
    & $wsl --distribution $Distribution --user root --exec @Arguments
}

Write-Host 'Checking the native Windows, WSL2, and CIFS capabilities.'
& $wsl --version
$kernel = Invoke-Linux @('uname', '-r') | Select-Object -Last 1
$osVersion = Invoke-Linux @('cat', '/etc/os-release')
if ($kernel -notmatch 'WSL2' -or $osVersion -notcontains 'ID=ubuntu') {
    throw "The test requires an Ubuntu WSL2 guest. The running Linux kernel is $kernel."
}
Write-Host "Ubuntu guest kernel: $kernel"
$osVersion | Where-Object { $_ -like 'PRETTY_NAME=*' } | Write-Host
if (-not (Invoke-Linux @('cat', '/proc/filesystems') | Where-Object { $_ -match '\bcifs$' })) {
    Invoke-Linux @('/usr/sbin/modprobe', 'cifs')
}
Invoke-Linux @('/usr/sbin/mount.cifs', '-V')
if ((Get-Service LanmanServer).Status -ne 'Running') {
    throw 'The native Windows SMB Server (LanmanServer) service is not running.'
}

$defaultRoute = Invoke-Linux @('/usr/sbin/ip', '-4', 'route', 'show', 'default') | Select-Object -First 1
if ($defaultRoute -notmatch '^default via (?<gateway>[0-9.]+)') {
    throw 'The Ubuntu WSL2 guest has no IPv4 gateway for the private SMB test.'
}
$gateway = $Matches.gateway
$guestRoute = Invoke-Linux @('/usr/sbin/ip', '-4', 'route', 'get', $gateway) | Select-Object -First 1
if ($guestRoute -notmatch '\bsrc (?<guest>[0-9.]+)' -or
    -not (Get-NetIPAddress -IPAddress $gateway -AddressFamily IPv4 -ErrorAction SilentlyContinue)) {
    throw "The Ubuntu gateway $gateway is not an IPv4 address on the Windows host. WSL NAT is required for this proof."
}
$guestAddress = $Matches.guest
$linuxRepository = Invoke-Linux @('wslpath', '-a', $repository)

$id = [guid]::NewGuid().ToString('N')
$shareName = "fm-proof-$id"
$userName = 'fmproof' + $id.Substring(0, 10)
$peer = "$env:COMPUTERNAME\$userName"
$runner = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$password = 'Fm9!' + [guid]::NewGuid().ToString('N')
if ($env:GITHUB_ACTIONS) { Write-Host "::add-mask::$password" }
$scratch = Join-Path ([IO.Path]::GetTempPath()) $shareName
$shareDirectory = Join-Path $scratch 'share'
$windowsPath = "\\localhost\$shareName"
$guestDirectory = "/tmp/$shareName"
$mount = "$guestDirectory/mount"
$credentials = "$guestDirectory/credentials"
$madeUser = $false
$madeShare = $false
$madeRule = $false
$madeGuest = $false
$mounted = $false
$workers = @()

try {
    New-Item -ItemType Directory -Path (Join-Path $shareDirectory 'transport') -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $shareDirectory 'rendezvous') | Out-Null
    $securePassword = ConvertTo-SecureString $password -AsPlainText -Force
    New-LocalUser -Name $userName -Password $securePassword | Out-Null
    $madeUser = $true
    & icacls.exe $shareDirectory /grant "${peer}:(OI)(CI)M"
    New-SmbShare -Name $shareName -Path $shareDirectory -FullAccess @($runner, $peer) -EncryptData $true | Out-Null
    $madeShare = $true
    New-NetFirewallRule -Name $shareName -DisplayName $shareName -Direction Inbound -Protocol TCP `
        -LocalPort 445 -LocalAddress $gateway -RemoteAddress $guestAddress -Action Allow | Out-Null
    $madeRule = $true

    Write-Host 'Connecting native Windows UNC and Ubuntu kernel CIFS clients to the same Windows SMB share.'
    Get-Item -LiteralPath (Join-Path $windowsPath 'transport') | Out-Null
    $connection = Get-SmbConnection | Where-Object { $_.ShareName -eq $shareName } | Select-Object -First 1
    if ($null -eq $connection) { throw 'The Windows UNC client did not establish an SMB connection.' }
    Write-Host "Windows client SMB dialect: $($connection.Dialect)"

    Invoke-Linux @('mkdir', '-m', '700', $guestDirectory)
    $madeGuest = $true
    Invoke-Linux @('mkdir', $mount)
    $hostCredentials = Join-Path $scratch 'wsl-credentials'
    [IO.File]::WriteAllText($hostCredentials, "username=$userName`npassword=$password`ndomain=$env:COMPUTERNAME`n")
    $guestSource = Invoke-Linux @('wslpath', '-a', $hostCredentials)
    Invoke-Linux @('install', '-m', '600', $guestSource, $credentials)
    Remove-Item -LiteralPath $hostCredentials -Force
    Invoke-Linux @('timeout', '25', 'mount', '-t', 'cifs', "//$gateway/$shareName", $mount, '-o',
        "credentials=$credentials,vers=3.1.1,seal,nosharesock,cache=none,actimeo=0")
    $mounted = $true
    Invoke-Linux @('rm', '--', $credentials)
    $mountType = Invoke-Linux @('findmnt', '--noheadings', '--raw', '--output', 'FSTYPE,SOURCE', '--target', $mount)
    if ($mountType -notmatch '^cifs\s+') { throw 'Ubuntu did not mount the share with the kernel CIFS client.' }
    Write-Host "Ubuntu client mount: $mountType"

    $module = 'backend.tests.shared_sync_smb_client'
    $windowsArguments = @('-m', $module, "`"$windowsPath`"", $id, 'windows')
    $ubuntuArguments = @('--distribution', $Distribution, '--user', 'root', '--cd', "`"$linuxRepository`"",
        '--exec', 'timeout', '--kill-after=5s', '110s', 'python3', '-m', $module, $mount, $id, 'ubuntu')
    $workers += Start-Process -FilePath $python -WorkingDirectory $repository -ArgumentList $windowsArguments `
        -RedirectStandardOutput (Join-Path $scratch 'windows.out') -RedirectStandardError (Join-Path $scratch 'windows.err') -PassThru
    $workers += Start-Process -FilePath $wsl -ArgumentList $ubuntuArguments `
        -RedirectStandardOutput (Join-Path $scratch 'ubuntu.out') -RedirectStandardError (Join-Path $scratch 'ubuntu.err') -PassThru
    foreach ($worker in $workers) {
        if (-not $worker.WaitForExit(120000)) { throw 'An SMB proof client exceeded its two-minute timeout.' }
    }
    if ($workers | Where-Object { $_.ExitCode -ne 0 }) { throw 'A cross-platform SMB client failed.' }
    if ($env:GITHUB_STEP_SUMMARY) {
        'Verified Windows UNC and Ubuntu WSL2 CIFS clients racing on one private Windows SMB share.' |
            Add-Content $env:GITHUB_STEP_SUMMARY
    }
} finally {
    foreach ($worker in $workers) {
        if (-not $worker.HasExited) { $worker.Kill($true) }
    }
    foreach ($name in @('windows.out', 'windows.err', 'ubuntu.out', 'ubuntu.err')) {
        $log = Join-Path $scratch $name
        if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log | Write-Host }
    }
    if ($mounted) {
        try { Invoke-Linux @('umount', '--lazy', $mount) } catch { Write-Warning 'Could not unmount the guest CIFS client.' }
    }
    if ($madeGuest) {
        try { Invoke-Linux @('rm', '-rf', '--', $guestDirectory) } catch { Write-Warning 'Could not remove the guest scratch directory.' }
    }
    if ($madeShare) { Remove-SmbShare -Name $shareName -Force -ErrorAction Continue }
    if ($madeRule) { Remove-NetFirewallRule -Name $shareName -ErrorAction Continue }
    if ($madeUser) { Remove-LocalUser -Name $userName -ErrorAction Continue }
    if (Test-Path -LiteralPath $scratch) { Remove-Item -LiteralPath $scratch -Recurse -Force -ErrorAction Continue }
}
