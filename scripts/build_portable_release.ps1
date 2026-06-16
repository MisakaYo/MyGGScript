param(
    [string]$OutputDir = "",
    [string]$Version = "",
    [string]$Repository = "https://github.com/MisakaYo/MyGGScript",
    [string]$Branch = "main",
    [string]$BootstrapDir = "",
    [string]$BootstrapArchive = "",
    [string]$BootstrapArchiveUrl = "",
    [string]$DeployTemplate = "config/deploy.template-cn.yaml"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Section {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message"
}

function Resolve-SevenZip {
    $candidate = Get-Command 7z -ErrorAction SilentlyContinue
    if ($candidate) {
        return $candidate.Source
    }

    $defaultPath = "C:\Program Files\7-Zip\7z.exe"
    if (Test-Path $defaultPath) {
        return $defaultPath
    }

    throw "7z.exe was not found. Install 7-Zip first, or provide a pre-extracted bootstrap directory."
}

function Try-ResolveSevenZip {
    $candidate = Get-Command 7z -ErrorAction SilentlyContinue
    if ($candidate) {
        return $candidate.Source
    }

    $defaultPath = "C:\Program Files\7-Zip\7z.exe"
    if (Test-Path $defaultPath) {
        return $defaultPath
    }

    return $null
}

function Get-ReleaseAssetUrl {
    param(
        [string]$Owner,
        [string]$Repo,
        [string]$Token
    )

    $headers = @{
        "Accept" = "application/vnd.github+json"
        "User-Agent" = "MyGGScript-Portable-Builder"
    }
    if ($Token) {
        $headers["Authorization"] = "Bearer $Token"
    }

    $release = Invoke-RestMethod -Headers $headers -Uri "https://api.github.com/repos/$Owner/$Repo/releases/latest"
    $assets = @($release.assets)
    $preferred = $assets | Where-Object { $_.name -match "_full\.7z$" } | Select-Object -First 1
    if (-not $preferred) {
        $preferred = $assets | Where-Object { $_.name -match "_fullcn\.7z$" } | Select-Object -First 1
    }
    if (-not $preferred) {
        $preferred = $assets | Where-Object { $_.name -match "full.*\.7z$" } | Select-Object -First 1
    }
    if (-not $preferred) {
        throw "Could not find an upstream full release asset under $Owner/$Repo."
    }

    return $preferred.browser_download_url
}

function Find-BootstrapRoot {
    param([string]$SearchRoot)

    $resolved = (Resolve-Path $SearchRoot).Path
    if (
        (Test-Path (Join-Path $resolved "toolkit")) -and
        (Test-Path (Join-Path $resolved "Alas.exe")) -and
        (Test-Path (Join-Path $resolved ".git"))
    ) {
        return $resolved
    }

    $candidate = Get-ChildItem -Path $resolved -Directory -Recurse -Force |
        Where-Object {
            (Test-Path (Join-Path $_.FullName "toolkit")) -and
            (Test-Path (Join-Path $_.FullName "Alas.exe")) -and
            (Test-Path (Join-Path $_.FullName ".git"))
        } |
        Select-Object -First 1

    if ($candidate) {
        return $candidate.FullName
    }

    throw "Unable to locate a bootstrap release root under $SearchRoot."
}

function Sync-Directory {
    param(
        [string]$SourcePath,
        [string]$DestinationPath
    )

    if (-not (Test-Path $SourcePath)) {
        throw "Directory not found: $SourcePath"
    }

    New-Item -ItemType Directory -Path $DestinationPath -Force | Out-Null
    $robocopyLog = Join-Path ([System.IO.Path]::GetTempPath()) ("mygg-robocopy-" + [guid]::NewGuid().ToString("N") + ".log")
    $null = robocopy $SourcePath $DestinationPath /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP /LOG:$robocopyLog
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy failed while syncing $SourcePath to $DestinationPath. See $robocopyLog"
    }
}

function Copy-ReleaseShell {
    param(
        [string]$BootstrapRoot,
        [string]$PackageRoot
    )

    # Copy only the shell layer so we do not ship repo sources or local user data.
    $shellDirectories = @(
        ".git",
        "toolkit"
    )
    foreach ($directory in $shellDirectories) {
        $sourcePath = Join-Path $BootstrapRoot $directory
        if (Test-Path $sourcePath) {
            Sync-Directory -SourcePath $sourcePath -DestinationPath (Join-Path $PackageRoot $directory)
        }
    }

    # Launchers must stay at the package root for the same double-click flow as official builds.
    foreach ($file in @("Alas.exe", "console.bat")) {
        $sourcePath = Join-Path $BootstrapRoot $file
        if (Test-Path $sourcePath) {
            Copy-Item -Path $sourcePath -Destination $PackageRoot -Force
        }
    }
}

function New-DeployConfig {
    param(
        [string]$TemplatePath,
        [string]$DestinationPath,
        [string]$RepositoryUrl,
        [string]$BranchName
    )

    $content = Get-Content -Path $TemplatePath -Raw
    $content = [regex]::Replace($content, "(?m)^    Repository:\s*.*$", "    Repository: $RepositoryUrl")
    $content = [regex]::Replace($content, "(?m)^    Branch:\s*.*$", "    Branch: $BranchName")
    Set-Content -Path $DestinationPath -Value $content -Encoding UTF8
}

function Disable-GitCredentialSelector {
    param([string]$PackageRoot)

    $gitConfigPath = Join-Path $PackageRoot "toolkit\Git\etc\gitconfig"
    if (-not (Test-Path $gitConfigPath)) {
        return
    }

    # 发布包面向公开仓库更新场景，不需要 Git for Windows 的凭据选择器弹窗。
    # 这里直接把 helper-selector 改成空 helper，避免朋友首次启动时被额外交互打断。
    $content = Get-Content -Path $gitConfigPath -Raw
    $content = [regex]::Replace($content, '(?m)^(\s*helper\s*=\s*)helper-selector\s*$', '${1}')
    Set-Content -Path $gitConfigPath -Value $content -Encoding ASCII
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $OutputDir) {
    $OutputDir = Join-Path $repoRoot "dist"
}
if (-not $Version) {
    $Version = (git -C $repoRoot rev-parse --short HEAD).Trim()
}

$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("mygg-portable-" + [guid]::NewGuid().ToString("N"))
$bootstrapExtract = Join-Path $tempRoot "bootstrap"
$packageRoot = Join-Path $tempRoot "package"
$archiveRoot = Join-Path $tempRoot "archive-root"
$archiveContentRoot = Join-Path $archiveRoot "AzurLaneAutoScript"

try {
    New-Item -ItemType Directory -Path $tempRoot, $bootstrapExtract, $packageRoot, $archiveRoot, $OutputDir -Force | Out-Null

    if (-not $BootstrapDir -and -not $BootstrapArchive -and -not $BootstrapArchiveUrl) {
        $localRepo3 = Join-Path (Split-Path $repoRoot -Parent) "repo3"
        if (
            (Test-Path (Join-Path $localRepo3 "toolkit")) -and
            (Test-Path (Join-Path $localRepo3 "Alas.exe")) -and
            (Test-Path (Join-Path $localRepo3 ".git"))
        ) {
            $BootstrapDir = $localRepo3
        }
    }

    if (-not $BootstrapDir) {
        $sevenZip = Resolve-SevenZip

        if (-not $BootstrapArchive) {
            if (-not $BootstrapArchiveUrl) {
                Write-Section "Resolving latest upstream bootstrap asset"
                $BootstrapArchiveUrl = Get-ReleaseAssetUrl -Owner "LmeSzinc" -Repo "AzurLaneAutoScript" -Token $env:GITHUB_TOKEN
            }

            $BootstrapArchive = Join-Path $tempRoot "bootstrap.7z"
            Write-Section "Downloading bootstrap archive"
            Write-Host $BootstrapArchiveUrl
            Invoke-WebRequest -Uri $BootstrapArchiveUrl -OutFile $BootstrapArchive
        }

        Write-Section "Extracting bootstrap archive"
        & $sevenZip x "-o$bootstrapExtract" -y $BootstrapArchive | Out-Host
        $BootstrapDir = Find-BootstrapRoot -SearchRoot $bootstrapExtract
    }
    else {
        $BootstrapDir = Find-BootstrapRoot -SearchRoot $BootstrapDir
    }

    Write-Section "Using bootstrap root"
    Write-Host $BootstrapDir

    Write-Section "Copying official-style release shell"
    Copy-ReleaseShell -BootstrapRoot $BootstrapDir -PackageRoot $packageRoot
    Disable-GitCredentialSelector -PackageRoot $packageRoot

    Write-Section "Overlaying MyGG deploy layer"
    Sync-Directory -SourcePath (Join-Path $repoRoot "deploy") -DestinationPath (Join-Path $packageRoot "deploy")
    foreach ($readmeName in @("README.md", "README_en.md", "README_jp.md")) {
        $readmePath = Join-Path $repoRoot $readmeName
        if (Test-Path $readmePath) {
            Copy-Item -Path $readmePath -Destination $packageRoot -Force
        }
    }

    $configDir = Join-Path $packageRoot "config"
    New-Item -ItemType Directory -Path $configDir -Force | Out-Null
    $templateJsonSource = Join-Path $repoRoot "config/template.json"
    if (Test-Path $templateJsonSource) {
        # Keep only template.json plus deploy.yaml to avoid leaking builder-side configs.
        Copy-Item -Path $templateJsonSource -Destination (Join-Path $configDir "template.json") -Force
    }

    $templatePath = Join-Path $repoRoot $DeployTemplate
    if (-not (Test-Path $templatePath)) {
        throw "Deploy template not found: $templatePath"
    }

    Write-Section "Generating config/deploy.yaml"
    New-DeployConfig -TemplatePath $templatePath -DestinationPath (Join-Path $configDir "deploy.yaml") -RepositoryUrl $Repository -BranchName $Branch

    # 官方 release 解压后会先落到 AzurLaneAutoScript 目录下，这里复刻同样的目录层级。
    Write-Section "Preparing official-style archive root"
    Sync-Directory -SourcePath $packageRoot -DestinationPath $archiveContentRoot

    $sevenZip = Try-ResolveSevenZip
    $archiveName = if ($sevenZip) {
        "MyGGScript_${Version}_portable.7z"
    }
    else {
        "MyGGScript_${Version}_portable.zip"
    }
    $archivePath = Join-Path $OutputDir $archiveName
    if (Test-Path $archivePath) {
        Remove-Item -Path $archivePath -Force
    }

    Write-Section "Packing release archive"
    if ($sevenZip) {
        Push-Location $archiveRoot
        try {
            & $sevenZip a -t7z -mx=9 $archivePath * | Out-Host
        }
        finally {
            Pop-Location
        }
    }
    else {
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::CreateFromDirectory(
            $archiveRoot,
            $archivePath,
            [System.IO.Compression.CompressionLevel]::Optimal,
            $false
        )
    }

    $hash = Get-FileHash -Algorithm SHA256 $archivePath
    $hashPath = "$archivePath.sha256.txt"
    Set-Content -Path $hashPath -Value ("{0}  {1}" -f $hash.Hash.ToLowerInvariant(), $archiveName) -Encoding ASCII

    Write-Section "Portable release created"
    Write-Host $archivePath
    Write-Host $hashPath

    if ($env:GITHUB_OUTPUT) {
        "archive_path=$archivePath" | Out-File -FilePath $env:GITHUB_OUTPUT -Encoding utf8 -Append
        "archive_name=$archiveName" | Out-File -FilePath $env:GITHUB_OUTPUT -Encoding utf8 -Append
        "checksum_path=$hashPath" | Out-File -FilePath $env:GITHUB_OUTPUT -Encoding utf8 -Append
    }
}
finally {
    if (Test-Path $tempRoot) {
        Remove-Item -Path $tempRoot -Recurse -Force
    }
}
