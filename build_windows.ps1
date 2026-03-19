# ClipHist Windows Build Script
# UTF-8 with BOM

param(
    [string]$Python = "python",
    [switch]$UseUpx = $true
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

# 1. Create clean venv for smaller build size
$venvDir = Join-Path $projectRoot ".build_venv"
if (Test-Path $venvDir) {
    Write-Host "Removing previous build venv..."
    Remove-Item -Recurse -Force $venvDir
}

Write-Host "Creating isolated build virtual environment..."
& $Python -m venv $venvDir
$venvPython = Join-Path $venvDir "Scripts\python.exe"

Write-Host "Installing build dependencies into venv..."
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r requirements.txt --quiet
& $venvPython -m pip install pyinstaller --quiet

# 2. Generate icon if not exists
$iconPath = Join-Path $projectRoot "assets\icon.ico"
if (-not (Test-Path $iconPath)) {
    Write-Host "Generating application icon..."
    & $venvPython generate_icon.py
    if (-not (Test-Path $iconPath)) {
        Write-Warning "Icon generation failed, building without icon."
        $iconPath = $null
    }
}

# 3. Clean old build output
Write-Host "Cleaning old build output..."
Remove-Item -Recurse -Force "$projectRoot\build" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "$projectRoot\dist" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "$projectRoot\ClipHist.spec" -ErrorAction SilentlyContinue

$pyinstallerArgs = @(
    "--noconfirm",
    "--clean",
    "--windowed",
    "--onefile",
    "--name", "ClipHist",
    "--optimize", "2"
)

# Icon parameter (Windows uses semicolon separator)
if ($iconPath -and (Test-Path $iconPath)) {
    $pyinstallerArgs += @("--icon", $iconPath)
    $iconData = $iconPath + ";assets"
    $pyinstallerArgs += @("--add-data", $iconData)
}

# PySide6 plugins and data
$pysidePluginsSrc = $env:PYTHONHOME + "\Lib\site-packages\PySide6\plugins"
if (Test-Path $pysidePluginsSrc) {
    $pysideData = $pysidePluginsSrc + ";PySide6\plugins"
    $pyinstallerArgs += @("--add-data", $pysideData)
}

$pyinstallerArgs += @(
    "--exclude-module", "pytest",
    "--exclude-module", "tkinter",
    "--exclude-module", "unittest",
    "--exclude-module", "pydoc",
    "--exclude-module", "doctest",
    "--exclude-module", "matplotlib",
    "--exclude-module", "numpy",
    "--exclude-module", "scipy",
    "--exclude-module", "pandas",
    "--exclude-module", "PySide6.Qt3DAnimation",
    "--exclude-module", "PySide6.Qt3DCore",
    "--exclude-module", "PySide6.Qt3DExtras",
    "--exclude-module", "PySide6.Qt3DInput",
    "--exclude-module", "PySide6.Qt3DLogic",
    "--exclude-module", "PySide6.Qt3DRender",
    "--exclude-module", "PySide6.QtBluetooth",
    "--exclude-module", "PySide6.QtCharts",
    "--exclude-module", "PySide6.QtDataVisualization",
    "--exclude-module", "PySide6.QtLocation",
    "--exclude-module", "PySide6.QtMultimedia",
    "--exclude-module", "PySide6.QtMultimediaWidgets",
    "--exclude-module", "PySide6.QtNetworkAuth",
    "--exclude-module", "PySide6.QtNetwork",
    "--exclude-module", "PySide6.QtPrintSupport",
    "--exclude-module", "PySide6.QtPdf",
    "--exclude-module", "PySide6.QtPdfWidgets",
    "--exclude-module", "PySide6.QtPositioning",
    "--exclude-module", "PySide6.QtOpenGL",
    "--exclude-module", "PySide6.QtOpenGLWidgets",
    "--exclude-module", "PySide6.QtDBus",
    "--exclude-module", "PySide6.QtConcurrent",
    "--exclude-module", "PySide6.QtQml",
    "--exclude-module", "PySide6.QtQuick",
    "--exclude-module", "PySide6.QtQuick3D",
    "--exclude-module", "PySide6.QtQuickControls2",
    "--exclude-module", "PySide6.QtQuickWidgets",
    "--exclude-module", "PySide6.QtRemoteObjects",
    "--exclude-module", "PySide6.QtScxml",
    "--exclude-module", "PySide6.QtSensors",
    "--exclude-module", "PySide6.QtSerialBus",
    "--exclude-module", "PySide6.QtSerialPort",
    "--exclude-module", "PySide6.QtSql",
    "--exclude-module", "PySide6.QtStateMachine",
    "--exclude-module", "PySide6.QtSvg",
    "--exclude-module", "PySide6.QtSvgWidgets",
    "--exclude-module", "PySide6.QtTest",
    "--exclude-module", "PySide6.QtTextToSpeech",
    "--exclude-module", "PySide6.QtWebChannel",
    "--exclude-module", "PySide6.QtWebEngineCore",
    "--exclude-module", "PySide6.QtWebEngineWidgets",
    "--exclude-module", "PySide6.QtWebSockets",
    "--exclude-module", "PySide6.QtXml",
    "--exclude-module", "PySide6.QtXmlPatterns",
    # Hidden imports for pywin32
    "--hidden-import", "win32api",
    "--hidden-import", "win32gui",
    "--hidden-import", "win32con",
    "--hidden-import", "win32clipboard",
    "--hidden-import", "win32com",
    "--hidden-import", "win32com.client",
    "--hidden-import", "win32com.gen_py",
    "--hidden-import", "pythoncom",
    "--hidden-import", "pywintypes",
    "run.py"
)

Write-Host "Building executable (size-optimized onefile)..."
& $venvPython -m PyInstaller @pyinstallerArgs

$exePath = Join-Path $projectRoot "dist\ClipHist.exe"
if (-not (Test-Path $exePath)) {
    throw "Build failed: $exePath not found."
}

if ($UseUpx) {
    $upxExe = $null
    $upxCmd = Get-Command upx -ErrorAction SilentlyContinue
    if ($upxCmd) {
        $upxExe = $upxCmd.Source
    } elseif (Test-Path "$projectRoot\.tools\upx-4.2.4\upx.exe") {
        $upxExe = "$projectRoot\.tools\upx-4.2.4\upx.exe"
    }

    if ($upxExe) {
        Write-Host "Compressing with UPX (--best --lzma --force)..."
        & $upxExe "--best" "--lzma" "--force" $exePath
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "UPX compression failed. Keeping uncompressed executable."
        }
    } else {
        Write-Warning "UPX not found (PATH or .tools\upx-4.2.4). Skipping UPX compression."
    }
}

$sizeMB = [math]::Round(((Get-Item $exePath).Length / 1MB), 2)
Write-Host "Done: $exePath ($sizeMB MB)"

# Cleanup build venv
Write-Host "Cleaning up build venv..."
Remove-Item -Recurse -Force $venvDir -ErrorAction SilentlyContinue
