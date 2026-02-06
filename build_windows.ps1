param(
    [string]$Python = "python",
    [switch]$UseUpx = $true
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

Write-Host "Installing build dependencies..."
& $Python -m pip install -r requirements.txt
& $Python -m pip install pyinstaller

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
    "--optimize", "2",
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
    "run.py"
)

Write-Host "Building executable (size-optimized onefile)..."
& $Python -m PyInstaller @pyinstallerArgs

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
        Write-Host "Compressing with UPX (--best --lzma)..."
        & $upxExe "--best" "--lzma" $exePath
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "UPX compression failed. Keeping uncompressed executable."
        }
    } else {
        Write-Warning "UPX not found (PATH or .tools\\upx-4.2.4). Skipping UPX compression."
    }
}

$sizeMB = [math]::Round(((Get-Item $exePath).Length / 1MB), 2)
Write-Host "Done: $exePath ($sizeMB MB)"
