$ErrorActionPreference = "Stop"

trap {
    $Message = $_.Exception.Message.Replace("`r", " ").Replace("`n", " ")
    Write-Host "::error file=scripts/build_windows.ps1::$Message"
    exit 1
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

python -m venv .build-venv
& .\.build-venv\Scripts\python.exe -m pip install --upgrade "pyinstaller>=6,<7"

$TesseractCommand = Get-Command tesseract.exe -ErrorAction SilentlyContinue
$TesseractExecutable = if ($TesseractCommand) { $TesseractCommand.Source } else { $null }
if (-not $TesseractExecutable) {
    $DefaultTesseract = Join-Path $env:ProgramFiles "Tesseract-OCR\tesseract.exe"
    if (Test-Path $DefaultTesseract) {
        $TesseractExecutable = $DefaultTesseract
    }
}
if (-not $TesseractExecutable) {
    throw "构建电脑未安装Tesseract。请先执行：choco install tesseract -y"
}

$TesseractSource = Split-Path -Parent $TesseractExecutable
$TesseractTarget = Join-Path $ProjectRoot "build_assets\tesseract"
if (Test-Path $TesseractTarget) {
    Remove-Item $TesseractTarget -Recurse -Force
}
New-Item -ItemType Directory -Path $TesseractTarget | Out-Null
Copy-Item (Join-Path $TesseractSource "*") $TesseractTarget -Recurse -Force

$Tessdata = Join-Path $TesseractTarget "tessdata"
New-Item -ItemType Directory -Path $Tessdata -Force | Out-Null
Invoke-WebRequest "https://github.com/tesseract-ocr/tessdata_fast/raw/main/chi_sim.traineddata" -OutFile (Join-Path $Tessdata "chi_sim.traineddata")
Invoke-WebRequest "https://github.com/tesseract-ocr/tessdata_fast/raw/main/osd.traineddata" -OutFile (Join-Path $Tessdata "osd.traineddata")

& .\.build-venv\Scripts\python.exe -m PyInstaller --noconfirm --clean desktop_app.spec

Write-Host "构建完成：$ProjectRoot\dist\身份证识别\身份证识别.exe"
