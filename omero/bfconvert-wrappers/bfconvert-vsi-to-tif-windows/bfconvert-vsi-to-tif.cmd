@echo off
setlocal
set "APP_DIR=%~dp0"

if not exist "%APP_DIR%runtime\bin\java.exe" (
  echo ERROR: The bundled Java runtime is missing.
  echo Expected: "%APP_DIR%runtime\bin\java.exe"
  echo Rebuild the portable package with build-portable.ps1.
  set "EXIT_CODE=1"
  goto :finish
)

if not exist "%APP_DIR%bftools\bioformats_package.jar" (
  echo ERROR: Bio-Formats is missing.
  echo Expected: "%APP_DIR%bftools\bioformats_package.jar"
  echo Rebuild the portable package with build-portable.ps1.
  set "EXIT_CODE=1"
  goto :finish
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%APP_DIR%convert-vsi-to-tif.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

:finish
echo.
if not defined EXIT_CODE set "EXIT_CODE=1"
if not "%EXIT_CODE%"=="0" echo Conversion finished with errors. Exit code: %EXIT_CODE%
pause
exit /b %EXIT_CODE%
