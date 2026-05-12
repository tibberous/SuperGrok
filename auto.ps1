$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.IO.Compression.FileSystem

$OriginalRoot = (Get-Location).Path

function Resolve-AutoloadRoot {
    param([string]$StartingPath)

    $current = Get-Item -LiteralPath $StartingPath -ErrorAction Stop

    # If a stale deploy nested the project as C:\flop\flop, treat the parent as canonical.
    while ($current -and $current.Parent -and ($current.Name -ieq $current.Parent.Name)) {
        $current = $current.Parent
    }

    return $current.FullName
}

$Root = Resolve-AutoloadRoot -StartingPath $OriginalRoot
$AutoloadRootCorrectionMessage = ""
if ($Root -ne $OriginalRoot) {
    $AutoloadRootCorrectionMessage = "Corrected nested autoload root from $OriginalRoot to $Root"
    Set-Location -LiteralPath $Root
}
$StatePath = Join-Path $Root ".autoload-state.json"

function Write-Log {
    param([string]$Message)
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$stamp] $Message"
}


function Format-ExceptionDetail {
    param($ErrorRecord)
    try {
        if ($ErrorRecord -and $ErrorRecord.Exception) {
            return ($ErrorRecord.Exception.GetType().Name + ": " + $ErrorRecord.Exception.Message)
        }
    } catch {}
    return [string]$ErrorRecord
}

function Stop-ProcessTreeByPid {
    param(
        [int]$Pid,
        [string]$Reason = "process-tree cleanup"
    )

    if ($Pid -le 0) { return $false }
    try {
        $currentPid = [System.Diagnostics.Process]::GetCurrentProcess().Id
        if ($Pid -eq $currentPid) { return $false }
    } catch {}

    try {
        Write-Log "TASKKILL: pid=$Pid reason=$Reason"
        & taskkill.exe /PID ([string]$Pid) /T /F 2>&1 | ForEach-Object { Write-Log "TASKKILL: $_" }
        return $true
    } catch {
        Write-Log "WARN: taskkill failed pid=$Pid. $(Format-ExceptionDetail $_)"
        try { Stop-Process -Id $Pid -Force -ErrorAction SilentlyContinue } catch {}
        return $false
    }
}

function Stop-StaleSuperGrokProcesses {
    try {
        $rootForward = ([string]$Root).Replace('\','/').TrimEnd('/')
        $needles = @(
            ($rootForward + '/start.py'),
            ($rootForward + '/supergrok_bridge/app.py')
        )
        $currentPid = [System.Diagnostics.Process]::GetCurrentProcess().Id
        $matches = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $cmd = [string]$_.CommandLine
            if ([string]::IsNullOrWhiteSpace($cmd)) { return $false }
            if ([int]$_.ProcessId -eq [int]$currentPid) { return $false }
            $normalized = $cmd.Replace('\','/')
            foreach ($needle in $needles) {
                if ($normalized.IndexOf([string]$needle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
            }
            return $false
        })
        foreach ($proc in $matches) {
            Write-Log "STALE-SUPERGROK:FOUND pid=$($proc.ProcessId) name=$($proc.Name) command=$($proc.CommandLine)"
            Stop-ProcessTreeByPid -Pid ([int]$proc.ProcessId) -Reason "stale SuperGrok relaunch cleanup" | Out-Null
        }
        if ($matches.Count -eq 0) {
            Write-Log "STALE-SUPERGROK:NONE"
        } else {
            Write-Log "STALE-SUPERGROK:SUMMARY found=$($matches.Count)"
            Start-Sleep -Milliseconds 500
        }
    } catch {
        Write-Log "WARN: stale SuperGrok cleanup failed. $(Format-ExceptionDetail $_)"
    }
}

function New-StateObject {
    param($Seed)

    $state = [pscustomobject]@{
        ZipPath         = ""
        ZipWriteTicks   = ""
        StartPath       = ""
        StartWriteTicks = ""
        StartSignature  = ""
    }

    if ($Seed) {
        foreach ($name in @('ZipPath','ZipWriteTicks','StartPath','StartWriteTicks','StartSignature')) {
            try {
                $value = $Seed.PSObject.Properties[$name].Value
            } catch {
                $value = $null
            }
            if ($null -ne $value) {
                $state | Add-Member -NotePropertyName $name -NotePropertyValue ([string]$value) -Force
            }
        }
    }

    return $state
}

function Load-State {
    if (Test-Path $StatePath) {
        try {
            $seed = Get-Content $StatePath -Raw | ConvertFrom-Json
            return (New-StateObject -Seed $seed)
        } catch {
            Write-Log "State file unreadable. Resetting."
        }
    }

    return (New-StateObject)
}

function Save-State {
    param($State)
    $State | ConvertTo-Json -Depth 5 | Set-Content -Path $StatePath -Encoding UTF8
}

function Get-AutoloadSearchRoots {
    $roots = New-Object System.Collections.Generic.List[string]
    foreach ($candidate in @($Root, $OriginalRoot)) {
        if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
        try {
            $resolved = (Get-Item -LiteralPath $candidate -ErrorAction Stop).FullName
            if (-not $roots.Contains($resolved)) {
                $roots.Add($resolved) | Out-Null
            }
        } catch {
        }
    }
    return @($roots)
}

function Get-NewestZip {
    $all = @()
    foreach ($searchRoot in (Get-AutoloadSearchRoots)) {
        $all += @(Get-ChildItem -Path $searchRoot -File -Filter *.zip -ErrorAction SilentlyContinue)
    }
    $all | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
}

function Resolve-StartPy {
    $rootStart = Join-Path $Root "start.py"
    if (Test-Path $rootStart) {
        return (Get-Item $rootStart)
    }

    $nested = @(Get-ChildItem -Path $Root -Recurse -File -Filter start.py -ErrorAction SilentlyContinue |
        Sort-Object FullName)
    if ($nested.Count -gt 0) {
        Write-Log "Root start.py is missing. Refusing to launch nested start.py candidates:"
        foreach ($candidate in $nested) {
            Write-Log "  nested start.py ignored: $($candidate.FullName)"
        }
        Write-Log "Drop a zip with start.py at zip root, or use the fixed autoload script from project root."
    }

    return $null
}

function Get-StartSignature {
    param($StartFile)

    if (-not $StartFile) { return "" }

    try {
        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $StartFile.FullName -ErrorAction Stop).Hash
    } catch {
        $hash = [string]$StartFile.LastWriteTimeUtc.Ticks + "|" + [string]$StartFile.Length
    }

    return ($StartFile.FullName + "|" + [string]$hash)
}


function Get-EffectiveExtractRoot {
    param([string]$Path)

    $current = Get-Item -LiteralPath $Path -ErrorAction Stop
    while ($current.PSIsContainer) {
        $children = @(Get-ChildItem -LiteralPath $current.FullName -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -notin @('.', '..', '__MACOSX') })
        if ($children.Count -eq 1 -and $children[0].PSIsContainer) {
            $current = $children[0]
            continue
        }
        break
    }
    return $current
}

function Test-WindowsSafeRelativePath {
    param([string]$RelativePath)

    if ([string]::IsNullOrWhiteSpace($RelativePath)) { return $false }
    $trimmed = $RelativePath.Trim()
    if ([string]::IsNullOrWhiteSpace($trimmed)) { return $false }

    # Zip entries may use / or \, but they must stay relative.
    if ([System.IO.Path]::IsPathRooted($trimmed)) { return $false }
    if ($trimmed -match '^[A-Za-z]:') { return $false }

    $normalized = $trimmed.Replace('\', '/')
    $normalized = $normalized.TrimStart('/').TrimEnd('/')
    if ([string]::IsNullOrWhiteSpace($normalized)) { return $false }

    $parts = @($normalized -split '/+' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if (-not $parts -or $parts.Count -eq 0) { return $false }

    $invalidChars = '<>:"\|?*'
    foreach ($part in $parts) {
        if ([string]::IsNullOrWhiteSpace($part)) { return $false }
        if ($part -eq '.' -or $part -eq '..') { return $false }
        if ($part.EndsWith(' ') -or $part.EndsWith('.')) { return $false }
        foreach ($char in $part.ToCharArray()) {
            if ([int][char]$char -lt 32) { return $false }
            if ($invalidChars.Contains([string]$char)) { return $false }
        }
    }

    return $true
}

function Test-PathInsideDirectory {
    param(
        [string]$Path,
        [string]$Directory
    )

    try {
        $fullPath = [System.IO.Path]::GetFullPath($Path)
        $fullDirectory = [System.IO.Path]::GetFullPath($Directory)
        if (-not $fullDirectory.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
            $fullDirectory = $fullDirectory + [System.IO.Path]::DirectorySeparatorChar
        }
        return $fullPath.StartsWith($fullDirectory, [System.StringComparison]::OrdinalIgnoreCase)
    } catch {
        return $false
    }
}

function Expand-ZipEntriesSafely {
    param(
        [string]$ZipPath,
        [string]$DestinationPath
    )

    $archive = $null
    try {
        $archive = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
        foreach ($entry in $archive.Entries) {
            $entryName = [string]$entry.FullName
            if ([string]::IsNullOrWhiteSpace($entryName)) { continue }
            $relative = $entryName.Replace('/', '\').TrimStart('\')
            if ([string]::IsNullOrWhiteSpace($relative)) { continue }
            if ($relative.StartsWith('__MACOSX\')) { continue }
            if (-not (Test-WindowsSafeRelativePath -RelativePath $relative)) {
                Write-Log "Skipping unsafe zip entry: $entryName"
                continue
            }

            $targetPath = Join-Path $DestinationPath $relative
            if (-not (Test-PathInsideDirectory -Path $targetPath -Directory $DestinationPath)) {
                Write-Log "Skipping zip entry outside destination: $entryName"
                continue
            }
            $isDirectory = $entryName.EndsWith('/') -or $entryName.EndsWith('\')
            if ($isDirectory) {
                New-Item -ItemType Directory -Path $targetPath -Force | Out-Null
                continue
            }

            $targetDir = Split-Path $targetPath -Parent
            if (-not (Test-Path $targetDir)) {
                New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
            }

            $entryStream = $null
            $fileStream = $null
            try {
                $entryStream = $entry.Open()
                $fileStream = [System.IO.File]::Open($targetPath, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
                $entryStream.CopyTo($fileStream)
            } finally {
                if ($fileStream) { $fileStream.Dispose() }
                if ($entryStream) { $entryStream.Dispose() }
            }
        }
    } finally {
        if ($archive) { $archive.Dispose() }
    }
}

function Extract-ZipOverRoot {
    param([string]$ZipPath)

    Write-Log "Extracting zip over canonical root: $ZipPath -> $Root"

    $temp = Join-Path ([System.IO.Path]::GetTempPath()) ("autoload_" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $temp | Out-Null

    try {
        Expand-ZipEntriesSafely -ZipPath $ZipPath -DestinationPath $temp

        $effectiveRoot = Get-EffectiveExtractRoot -Path $temp

        Write-Log "Effective zip root: $($effectiveRoot.FullName)"

        Get-ChildItem -LiteralPath $effectiveRoot.FullName -Recurse -Force -ErrorAction SilentlyContinue | ForEach-Object {
            $relative = $_.FullName.Substring($effectiveRoot.FullName.Length).TrimStart('\','/')
            if ([string]::IsNullOrWhiteSpace($relative)) { return }
            if (-not (Test-WindowsSafeRelativePath -RelativePath $relative)) { return }

            $dest = Join-Path $Root $relative

            if ($_.PSIsContainer) {
                if (-not (Test-Path $dest)) {
                    New-Item -ItemType Directory -Path $dest -Force | Out-Null
                }
            } else {
                $destDir = Split-Path $dest -Parent
                if (-not (Test-Path $destDir)) {
                    New-Item -ItemType Directory -Path $destDir -Force | Out-Null
                }
                Copy-Item -LiteralPath $_.FullName -Destination $dest -Force
            }
        }
    } finally {
        Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
    }

    try {
        Remove-Item -LiteralPath $ZipPath -Force -ErrorAction Stop
        Write-Log "Deleted $([System.IO.Path]::GetFileName($ZipPath))"
    } catch {
        Write-Log "WARN: Could not delete zip after extraction: $ZipPath :: $($_.Exception.Message)"
    }
}

function Get-PythonCommand {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return [pscustomobject]@{
            FilePath   = $python.Source
            PrefixArgs = @()
        }
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        return [pscustomobject]@{
            FilePath   = $py.Source
            PrefixArgs = @("-3")
        }
    }

    throw "Could not find python or py on PATH."
}

function New-LaunchInfo {
    return [pscustomobject]@{
        Process          = $null
        LaunchPath       = ""
        LaunchWriteTicks = ""
        LaunchSignature  = ""
        LastLaunchUtc    = $null
    }
}

function Stop-LaunchInfo {
    param($LaunchInfo)

    if (-not $LaunchInfo) { return }

    $proc = $LaunchInfo.Process
    if (-not $proc) { return }

    try {
        if (-not $proc.HasExited) {
            Write-Log "Stopping PID $($proc.Id)"
            try { $null = $proc.CloseMainWindow() } catch {}
            Start-Sleep -Milliseconds 800

            if (-not $proc.HasExited) {
                Stop-ProcessTreeByPid -Pid ([int]$proc.Id) -Reason "active SuperGrok watcher restart" | Out-Null
            }
        }
    } catch {}

    $LaunchInfo.Process = $null
}

function Read-Hotkeys {
    $result = [pscustomobject]@{
        Restart = $false
        Quit    = $false
    }

    try {
        while ([Console]::KeyAvailable) {
            $keyInfo = [Console]::ReadKey($true)
            switch ($keyInfo.Key) {
                ([ConsoleKey]::R) { $result.Restart = $true }
                ([ConsoleKey]::Q) { $result.Quit = $true }
            }
        }
    } catch {
    }

    return $result
}

function Launch-StartPy {
    param(
        $StartFile,
        $LaunchInfo
    )

    if ($LaunchInfo -and $LaunchInfo.Process -and -not $LaunchInfo.Process.HasExited) {
        Stop-LaunchInfo $LaunchInfo
    }

    $py = Get-PythonCommand
    $args = @()
    $args += $py.PrefixArgs
    $args += @($StartFile.FullName)
    $args += @("--debug")

    Stop-StaleSuperGrokProcesses

    Write-Log "Launching start.py target: $($py.FilePath) $($args -join ' ')"
    Write-Log "Launch working directory: $($StartFile.DirectoryName)"

    $proc = Start-Process -FilePath $py.FilePath `
                          -ArgumentList $args `
                          -WorkingDirectory $StartFile.DirectoryName `
                          -NoNewWindow `
                          -PassThru

    $LaunchInfo.Process = $proc
    $LaunchInfo.LaunchPath = $StartFile.FullName
    $LaunchInfo.LaunchWriteTicks = [string]$StartFile.LastWriteTimeUtc.Ticks
    $LaunchInfo.LaunchSignature = Get-StartSignature -StartFile $StartFile
    $LaunchInfo.LastLaunchUtc = [DateTime]::UtcNow
}

$state = Load-State
$launchInfo = New-LaunchInfo
$hasLaunchedThisWatcher = $false
$lastObservedStartSignature = ""
$startSignatureStablePasses = 0
$launchCooldownMs = 4000

if (-not [string]::IsNullOrWhiteSpace($AutoloadRootCorrectionMessage)) {
    Write-Log $AutoloadRootCorrectionMessage
}
Write-Log "Autoload watcher original cwd: $OriginalRoot"
Write-Log "Autoload watcher canonical root: $Root"
Write-Host ""
Write-Host "R = rerun start.py"
Write-Host "Q = quit watcher"
Write-Host ""

while ($true) {
    try {
        $hotkeys = Read-Hotkeys

        if ($hotkeys.Quit) {
            Write-Log "Quit requested."
            Stop-LaunchInfo $launchInfo
            break
        }

        $manualRestart = [bool]$hotkeys.Restart
        if ($manualRestart) {
            Write-Log "Manual restart requested."
        }

        $zip = Get-NewestZip
        $zipChanged = $false

        if ($zip) {
            $zipTicks = [string]$zip.LastWriteTimeUtc.Ticks
            if ($state.ZipPath -ne $zip.FullName -or $state.ZipWriteTicks -ne $zipTicks) {
                Extract-ZipOverRoot -ZipPath $zip.FullName
                $state.ZipPath = $zip.FullName
                $state.ZipWriteTicks = $zipTicks
                $zipChanged = $true
                Save-State $state
            }
        }

        $startFile = Resolve-StartPy
        if (-not $startFile) {
            Write-Log "No start.py found yet."
            Start-Sleep -Milliseconds 500
            continue
        }

        $startTicks = [string]$startFile.LastWriteTimeUtc.Ticks
        $startSignature = Get-StartSignature -StartFile $startFile
        if ($startSignature -eq $lastObservedStartSignature) {
            $startSignatureStablePasses += 1
        } else {
            $lastObservedStartSignature = $startSignature
            $startSignatureStablePasses = 1
        }
        $savedSignature = [string]($state.StartSignature)
        if (-not $savedSignature -and $state.StartPath -eq $startFile.FullName -and $state.StartWriteTicks -eq $startTicks) {
            $savedSignature = $startSignature
        }
        $startChanged = (
            $state.StartPath -ne $startFile.FullName -or
            $savedSignature -ne $startSignature
        )

        $shouldLaunch = $false

        if (-not $hasLaunchedThisWatcher) {
            $shouldLaunch = $true
        } elseif ($zipChanged -or $startChanged) {
            $shouldLaunch = $true
        } elseif ($manualRestart) {
            $shouldLaunch = $true
        }

        if ($shouldLaunch -and -not $manualRestart) {
            if ($startSignatureStablePasses -lt 2) {
                $shouldLaunch = $false
            }
            if ($shouldLaunch -and $launchInfo.LastLaunchUtc) {
                $elapsedMs = ([DateTime]::UtcNow - $launchInfo.LastLaunchUtc).TotalMilliseconds
                if ($elapsedMs -lt $launchCooldownMs) {
                    $shouldLaunch = $false
                }
            }
            if ($shouldLaunch -and $launchInfo.Process -and -not $launchInfo.Process.HasExited) {
                if ($launchInfo.LaunchPath -eq $startFile.FullName -and $launchInfo.LaunchSignature -eq $startSignature) {
                    $shouldLaunch = $false
                }
            }
        }

        if ($shouldLaunch) {
            Launch-StartPy -StartFile $startFile -LaunchInfo $launchInfo

            $state.StartPath = $startFile.FullName
            $state.StartWriteTicks = $startTicks
            $state.StartSignature = $startSignature
            Save-State $state

            $hasLaunchedThisWatcher = $true
        }

    } catch {
        Write-Log ("ERROR: " + $_.Exception.Message)
    }

    Start-Sleep -Milliseconds 500
}
