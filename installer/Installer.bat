@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
title Bot Paradox - Installation

set "TARGET=%LOCALAPPDATA%\BotParadox"
set "SRC=%~dp0"
echo.
echo   Installation / mise a jour de Bot Paradox...
echo   %TARGET%
echo.

rem --- ferme les instances en cours et attend la liberation des fichiers ---
taskkill /IM BotParadox.exe /F >nul 2>&1
taskkill /IM botcore.exe /F >nul 2>&1
ping 127.0.0.1 -n 3 >nul

rem --- copie des fichiers (preserve les donnees utilisateur existantes) ---
if not exist "%TARGET%" mkdir "%TARGET%"
robocopy "%SRC%." "%TARGET%" /E /R:5 /W:2 /NFL /NDL /NJH /NJS /NP ^
   /XF Installer.bat session.json client_override.txt client_path.txt handoff.json >nul

rem --- garantit la mise a jour meme si un fichier a coince (jamais de pause) ---
copy /Y "%SRC%version.txt" "%TARGET%\version.txt" >nul 2>&1
copy /Y "%SRC%update.json" "%TARGET%\update.json" >nul 2>&1
copy /Y "%SRC%BotParadox.exe" "%TARGET%\BotParadox.exe" >nul 2>&1

cd /d "%TARGET%"

rem --- detection du client Nexus ---
echo   Recherche du client Nexus...
set "CLIENT="
for /f "usebackq delims=" %%P in (`botcore\botcore.exe --detect 2^>nul`) do set "CLIENT=%%P"

if /I "!CLIENT!"=="NOTFOUND" (
   echo   Client introuvable : selectionne le dossier de ton launcher Nexus...
   for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command ^
      "Add-Type -AssemblyName System.Windows.Forms; $f=New-Object System.Windows.Forms.FolderBrowserDialog; $f.Description='Ou est installe ton launcher Nexus ? (dossier contenant srv_nexus)'; if($f.ShowDialog() -eq 'OK'){$f.SelectedPath}"`) do set "PICK=%%D"
   if not "!PICK!"=="" (
      > "%TARGET%\botcore\data\client_override.txt" echo !PICK!
   )
)

rem --- application des patchs (overlay + ponts) ---
echo   Application des patchs au client...
botcore\botcore.exe --patch

rem --- raccourci sur le Bureau ---
powershell -NoProfile -Command ^
   "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Bot Paradox.lnk'); $s.TargetPath='%TARGET%\BotParadox.exe'; $s.WorkingDirectory='%TARGET%'; $s.IconLocation='%TARGET%\BotParadox.exe,0'; $s.Save()" >nul 2>&1

echo   Termine. Lancement...
start "" "%TARGET%\BotParadox.exe"
endlocal
