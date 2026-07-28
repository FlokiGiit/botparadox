@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
title Bot Paradox - Installation

set "TARGET=%LOCALAPPDATA%\BotParadox"
echo.
echo   Installation de Bot Paradox dans :
echo   %TARGET%
echo.

rem --- ferme les instances en cours (sinon on ne peut pas remplacer les exe) ---
taskkill /IM BotParadox.exe /F >nul 2>&1
taskkill /IM botcore.exe /F >nul 2>&1

rem --- copie des fichiers (preserve les donnees utilisateur existantes) ---
if not exist "%TARGET%" mkdir "%TARGET%"
robocopy "%~dp0." "%TARGET%" /E /R:3 /W:5 /NFL /NDL /NJH /NJS /NP ^
   /XF Installer.bat session.json client_override.txt client_path.txt handoff.json >nul
if errorlevel 8 (
   echo   [ERREUR] copie des fichiers impossible.
   pause & exit /b 1
)

cd /d "%TARGET%"

rem --- detection du client Nexus ---
echo   Recherche du client Nexus...
set "CLIENT="
for /f "usebackq delims=" %%P in (`botcore\botcore.exe --detect 2^>nul`) do set "CLIENT=%%P"

if /I "!CLIENT!"=="NOTFOUND" (
   echo   Client introuvable automatiquement.
   echo   Selectionne le dossier de ton launcher Nexus dans la fenetre...
   for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command ^
      "Add-Type -AssemblyName System.Windows.Forms; $f=New-Object System.Windows.Forms.FolderBrowserDialog; $f.Description='Ou est installe ton launcher Nexus ? (dossier contenant srv_nexus)'; if($f.ShowDialog() -eq 'OK'){$f.SelectedPath}"`) do set "PICK=%%D"
   if not "!PICK!"=="" (
      > "%TARGET%\botcore\data\client_override.txt" echo !PICK!
      echo   Dossier choisi : !PICK!
   )
)

rem --- application des patchs (overlay + ponts) ---
echo   Application des patchs au client...
botcore\botcore.exe --patch

rem --- raccourci sur le Bureau ---
powershell -NoProfile -Command ^
   "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Bot Paradox.lnk'); $s.TargetPath='%TARGET%\BotParadox.exe'; $s.WorkingDirectory='%TARGET%'; $s.IconLocation='%TARGET%\BotParadox.exe,0'; $s.Save()" >nul 2>&1

echo.
echo   Termine ! Un raccourci "Bot Paradox" est sur ton Bureau.
echo   Lancement...
start "" "%TARGET%\BotParadox.exe"
endlocal
