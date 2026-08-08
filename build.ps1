param(
    [switch]$SkipChecks
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$match = Select-String -Path "pyproject.toml" -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $match) { throw "Project version was not found in pyproject.toml" }
$version = $match.Matches[0].Groups[1].Value

if (-not $SkipChecks) {
    if (-not (Test-Path ".venv\Scripts\python.exe")) {
        py -3.10 -m venv .venv
    }
    $python = ".venv\Scripts\python.exe"
    & $python -m pip install -e ".[dev]"
    & $python -m unittest discover -s tests -v
    & $python -m ruff check src tests
} else {
    $python = "python"
}

if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (-not (Test-Path "releases")) { New-Item -ItemType Directory "releases" | Out-Null }

& $python -m PyInstaller --clean --noconfirm packaging\FolderBridge.spec

Copy-Item "README.md" "dist\FolderBridge\README.txt" -Force
$howTo = @(
    "FolderBridge $version — portable",
    "",
    "Raspakuyte ves arhiv i zapustite FolderBridge.exe.",
    "OAuth JSON hranit ryadom s programmoy ne nuzhno.",
    "Podrobnosti smotrite v README.txt."
) -join "`r`n"
$howTo | Set-Content "dist\FolderBridge\HOW_TO_RUN.txt" -Encoding utf8

$versionedZip = "releases\FolderBridge-$version-windows-x64.zip"
$stableZip = "releases\FolderBridge-portable.zip"
Remove-Item $versionedZip, $stableZip -Force -ErrorAction SilentlyContinue
Compress-Archive -Path "dist\FolderBridge" -DestinationPath $versionedZip -CompressionLevel Optimal
Copy-Item $versionedZip $stableZip -Force

$isccCandidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
)
$iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
    & $iscc "/DMyAppVersion=$version" "installer\FolderBridge.iss"
} else {
    Write-Warning "Inno Setup 6 not found; portable packages were built without setup.exe"
}

$releaseFiles = Get-ChildItem "releases" -File | Where-Object {
    $_.Name -like "FolderBridge-$version-*" -or $_.Name -eq "FolderBridge-portable.zip"
}
$checksums = foreach ($file in $releaseFiles) {
    $hash = (Get-FileHash $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $($file.Name)"
}
$checksums | Set-Content "releases\SHA256SUMS.txt" -Encoding ascii

Write-Host "FolderBridge $version build complete" -ForegroundColor Green
Get-ChildItem "releases" -File | Format-Table Name, Length

