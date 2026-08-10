"""
Construit l'installateur .exe a distribuer aux amis.

Assemble, dans l'ordre :
  1. botcore.exe   — le bot fige par PyInstaller (aucun Python requis)
  2. BotParadox.exe — l'UI publiee en autonome (aucun .NET requis)
  3. data/ + README + metadonnees de MAJ a cote du bot
  4. le tout embarque dans BotParadox-Setup.exe (setup_main.py fige) : un vrai
     installateur qui copie dans %LOCALAPPDATA%, patch, cree le raccourci et
     lance — sans fenetre d'extraction, contrairement a l'ancien 7z SFX.

Prerequis (presents sur la machine de build uniquement) :
  - Python + PyInstaller       (pip install pyinstaller)
  - .NET SDK                   (dotnet)

Lancer :  python build_installer.py
Sortie :  Publish\\BotParadox-Setup.exe
"""

import json
import os
import shutil
import subprocess
import sys

PROJECT = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(PROJECT, "build_out")        # intermediaire, jetable
STAGING = os.path.join(BUILD, "staging")          # arbre de l'appli (le payload)
PUBLISH = os.path.join(PROJECT, "Publish")        # dossier livrable
SETUP_EXE = os.path.join(PUBLISH, "BotParadox-Setup.exe")

INSTALLER_DIR = os.path.join(PROJECT, "installer")

# Fichiers data specifiques a une machine ou a un personnage : jamais embarques.
# spells.json est le catalogue de sorts du personnage qui a joue ici : l'expedier
# donnerait a chacun les sorts d'une classe qui n'est pas la sienne. Il se
# reconstruit tout seul au premier paquet ST recu par son proprietaire.
SKIP_DATA = {"session.json", "client_path.txt", "client_override.txt",
             "overlay.json", "spells.json", "map_exits.json"}


def run(cmd, **kw):
    print(">", " ".join(cmd) if isinstance(cmd, list) else cmd)
    r = subprocess.run(cmd, cwd=kw.pop("cwd", PROJECT), **kw)
    if r.returncode != 0:
        sys.exit(f"[build] echec ({r.returncode}) : {cmd}")


def step(msg):
    print("\n=== " + msg + " ===")


def freeze_bot():
    step("1/4  Gel du bot (PyInstaller)")
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
    # Icones de sorts : absentes du client (qui ne les a qu'en Flash), donc
    # livrees avec le bot. Les SVG sources restent en local, le PNG suffit.
    icons = os.path.join(PROJECT, "data", "spell_icons")
    if os.path.isdir(icons):
        shutil.copytree(icons, os.path.join(data_dst, "spell_icons"),
                        ignore=shutil.ignore_patterns("*.svg"))
    os.makedirs(os.path.join(dst, "logs"), exist_ok=True)
    print("   botcore pret :", dst)


def publish_ui():
    step("2/4  Publication de l'UI (autonome)")
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
    step("3/4  Metadonnees et README")
    shutil.copy2(os.path.join(INSTALLER_DIR, "README.txt"),
                 os.path.join(STAGING, "README.txt"))
    # Metadonnees de mise a jour, lues par l'UI a cote de BotParadox.exe.
    cfg = _release_config()
    with open(os.path.join(STAGING, "version.txt"), "w", encoding="utf-8") as f:
        f.write(str(cfg.get("version", "0.0.0")))
    with open(os.path.join(STAGING, "update.json"), "w", encoding="utf-8") as f:
        json.dump({"repo": cfg.get("repo", "OWNER/botparadox")}, f)
    print(f"   version {cfg.get('version')} / repo {cfg.get('repo')}")


def build_setup():
    step("4/4  Gel de l'installateur (PyInstaller)")
    # setup_main.py embarque tout STAGING sous "payload" et l'installe au
    # lancement. --noconsole : aucune fenetre parasite pendant l'auto-update.
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--onefile", "--noconsole", "--name", "BotParadox-Setup",
         "--add-data", f"{STAGING}{os.pathsep}payload",
         "--distpath", os.path.join(BUILD, "setupdist"),
         "--workpath", os.path.join(BUILD, "setupwork"),
         "--specpath", BUILD,
         os.path.join(PROJECT, "setup_main.py")])
    os.makedirs(PUBLISH, exist_ok=True)
    src = os.path.join(BUILD, "setupdist", "BotParadox-Setup.exe")
    if not os.path.exists(src):
        sys.exit("[build] BotParadox-Setup.exe introuvable apres PyInstaller")
    shutil.copy2(src, SETUP_EXE)
    size = os.path.getsize(SETUP_EXE) / (1024 * 1024)
    print(f"   {SETUP_EXE}  ({size:.1f} Mo)")


def fill_publish():
    """Depose aussi l'appli decompressee (pour une copie manuelle) + le README."""
    step("Remplissage du dossier Publish")
    app_dir = os.path.join(PUBLISH, "BotParadox")
    if os.path.exists(app_dir):
        shutil.rmtree(app_dir)
    shutil.copytree(STAGING, app_dir)
    shutil.copy2(os.path.join(INSTALLER_DIR, "README.txt"),
                 os.path.join(PUBLISH, "README.txt"))
    print("   appli decompressee :", app_dir)


def main():
    # Un botcore/BotParadox lance depuis Publish\ verrouille des fichiers et
    # ferait echouer le nettoyage : on coupe toute instance avant de builder.
    for exe in ("botcore.exe", "BotParadox.exe"):
        subprocess.run(["taskkill", "/IM", exe, "/F"],
                       capture_output=True)
    if os.path.exists(BUILD):
        shutil.rmtree(BUILD, ignore_errors=True)
    if os.path.exists(PUBLISH):
        shutil.rmtree(PUBLISH, ignore_errors=True)
    os.makedirs(STAGING)
    freeze_bot()
    publish_ui()
    add_installer_assets()
    build_setup()
    fill_publish()
    print("\n===================================================")
    print(" Dossier livrable :", PUBLISH)
    print("   - BotParadox-Setup.exe   (a envoyer / auto-update)")
    print("   - BotParadox\\           (appli decompressee, copie manuelle)")
    print("   - README.txt")
    print("===================================================")


if __name__ == "__main__":
    main()
