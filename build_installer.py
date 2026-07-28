"""
Construit l'installateur .exe a distribuer aux amis.

Assemble, dans l'ordre :
  1. botcore.exe   — le bot fige par PyInstaller (aucun Python requis)
  2. BotParadox.exe — l'UI publiee en autonome (aucun .NET requis)
  3. data/ + logs/  — donnees de jeu a cote du bot
  4. Installer.bat + README + config SFX
  5. le tout compresse et prefixe du module 7-Zip SFX -> BotParadox-Setup.exe

Prerequis (presents sur la machine de build uniquement) :
  - Python + PyInstaller       (pip install pyinstaller)
  - .NET SDK                   (dotnet)
  - 7-Zip installe             (C:\\Program Files\\7-Zip)

Lancer :  python build_installer.py
Sortie :  dist\\BotParadox-Setup.exe
"""

import json
import os
import shutil
import subprocess
import sys

PROJECT = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(PROJECT, "build_out")        # intermediaire, jetable
STAGING = os.path.join(BUILD, "staging")          # arbre final avant compression
PUBLISH = os.path.join(PROJECT, "Publish")        # dossier livrable
SETUP_EXE = os.path.join(PUBLISH, "BotParadox-Setup.exe")

SEVENZIP = r"C:\Program Files\7-Zip\7z.exe"
SFX_MODULE = r"C:\Program Files\7-Zip\7z.sfx"
INSTALLER_DIR = os.path.join(PROJECT, "installer")

# Fichiers data specifiques a une machine : jamais embarques.
SKIP_DATA = {"session.json", "client_path.txt", "client_override.txt"}


def run(cmd, **kw):
    print(">", " ".join(cmd) if isinstance(cmd, list) else cmd)
    r = subprocess.run(cmd, cwd=kw.pop("cwd", PROJECT), **kw)
    if r.returncode != 0:
        sys.exit(f"[build] echec ({r.returncode}) : {cmd}")


def step(msg):
    print("\n=== " + msg + " ===")


def freeze_bot():
    step("1/5  Gel du bot (PyInstaller)")
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--name", "botcore", "--onedir", "--console",
         "--distpath", os.path.join(BUILD, "dist"),
         "--workpath", os.path.join(BUILD, "work"),
         "--specpath", BUILD,
         "bot.py"])
    src = os.path.join(BUILD, "dist", "botcore")
    dst = os.path.join(STAGING, "botcore")
    shutil.copytree(src, dst)
    # Donnees de jeu a cote de l'exe (le bot les lit/ecrit la).
    data_dst = os.path.join(dst, "data")
    os.makedirs(data_dst, exist_ok=True)
    for f in os.listdir(os.path.join(PROJECT, "data")):
        if f.endswith(".json") and f not in SKIP_DATA:
            shutil.copy2(os.path.join(PROJECT, "data", f),
                         os.path.join(data_dst, f))
    os.makedirs(os.path.join(dst, "logs"), exist_ok=True)
    print("   botcore pret :", dst)


def publish_ui():
    step("2/5  Publication de l'UI (autonome)")
    out = os.path.join(BUILD, "ui")
    run(["dotnet", "publish", os.path.join("ui", "ui.csproj"),
         "-c", "Release", "-r", "win-x64", "--self-contained", "true",
         "-p:PublishSingleFile=true",
         "-p:IncludeNativeLibrariesForSelfExtract=true",
         "-p:EnableCompressionInSingleFile=true",
         "-p:DebugType=none", "-p:DebugSymbols=false",
         "-o", out, "--nologo"])
    exe = os.path.join(out, "BotParadox.exe")
    if not os.path.exists(exe):
        sys.exit("[build] BotParadox.exe introuvable apres publish")
    shutil.copy2(exe, os.path.join(STAGING, "BotParadox.exe"))
    print("   UI prete :", exe)


def _release_config():
    try:
        with open(os.path.join(PROJECT, "release_config.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"repo": "OWNER/botparadox", "version": "0.0.0"}


def add_installer_assets():
    step("3/5  Ajout des scripts d'installation")
    for f in ("Installer.bat", "README.txt"):
        shutil.copy2(os.path.join(INSTALLER_DIR, f), os.path.join(STAGING, f))
    # Metadonnees de mise a jour, lues par l'UI a cote de BotParadox.exe.
    cfg = _release_config()
    with open(os.path.join(STAGING, "version.txt"), "w", encoding="utf-8") as f:
        f.write(str(cfg.get("version", "0.0.0")))
    with open(os.path.join(STAGING, "update.json"), "w", encoding="utf-8") as f:
        json.dump({"repo": cfg.get("repo", "OWNER/botparadox")}, f)
    print(f"   version {cfg.get('version')} / repo {cfg.get('repo')}")


def make_archive():
    step("4/5  Compression 7-Zip")
    archive = os.path.join(BUILD, "payload.7z")
    if os.path.exists(archive):
        os.remove(archive)
    # -r + .\* depuis STAGING : chemins relatifs a la racine de l'archive.
    run([SEVENZIP, "a", "-t7z", "-mx=9", "-ms=on", archive, "*"], cwd=STAGING)
    return archive


def make_sfx(archive):
    step("5/5  Assemblage du .exe auto-extractible")
    os.makedirs(PUBLISH, exist_ok=True)
    config = os.path.join(INSTALLER_DIR, "sfx_config.txt")
    with open(SETUP_EXE, "wb") as out:
        for part in (SFX_MODULE, config, archive):
            with open(part, "rb") as f:
                out.write(f.read())
    size = os.path.getsize(SETUP_EXE) / (1024 * 1024)
    print(f"   {SETUP_EXE}  ({size:.1f} Mo)")


def fill_publish():
    """Depose dans Publish/ le livrable complet : l'installateur + l'appli
    decompressee (pour une copie manuelle) + le README."""
    step("Remplissage du dossier Publish")
    app_dir = os.path.join(PUBLISH, "BotParadox")
    if os.path.exists(app_dir):
        shutil.rmtree(app_dir)
    shutil.copytree(STAGING, app_dir, ignore=shutil.ignore_patterns("Installer.bat"))
    shutil.copy2(os.path.join(INSTALLER_DIR, "README.txt"),
                 os.path.join(PUBLISH, "README.txt"))
    print("   appli decompressee :", app_dir)


def main():
    for tool, path in (("7z", SEVENZIP), ("sfx", SFX_MODULE)):
        if not os.path.exists(path):
            sys.exit(f"[build] {tool} introuvable : {path} — installe 7-Zip")
    if os.path.exists(BUILD):
        shutil.rmtree(BUILD)
    if os.path.exists(PUBLISH):
        shutil.rmtree(PUBLISH)
    os.makedirs(STAGING)
    freeze_bot()
    publish_ui()
    add_installer_assets()
    archive = make_archive()
    make_sfx(archive)
    fill_publish()
    print("\n===================================================")
    print(" Dossier livrable :", PUBLISH)
    print("   - BotParadox-Setup.exe   (a envoyer a tes amis)")
    print("   - BotParadox\\           (appli decompressee, copie manuelle)")
    print("   - README.txt")
    print("===================================================")


if __name__ == "__main__":
    main()
