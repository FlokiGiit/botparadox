"""
Publie une nouvelle version : build de l'installateur + release GitHub.

Prerequis (une seule fois) :
  gh auth login          # connexion GitHub (interactive)
  # + un depot GitHub PUBLIC dont le nom correspond a release_config.json

A chaque nouvelle version :
  1. incremente "version" dans release_config.json (ex. 1.0.1)
  2. python publish_release.py

Le bot des amis verra la nouvelle release au prochain lancement et proposera
la mise a jour automatiquement.
"""

import json
import os
import subprocess
import sys

PROJECT = os.path.dirname(os.path.abspath(__file__))
SETUP = os.path.join(PROJECT, "Publish", "BotParadox-Setup.exe")


def cfg():
    with open(os.path.join(PROJECT, "release_config.json"), encoding="utf-8") as f:
        return json.load(f)


def main():
    c = cfg()
    repo, version = c.get("repo", ""), str(c.get("version", ""))
    if not repo or repo.startswith("OWNER/"):
        sys.exit("Configure 'repo' dans release_config.json (ex. \"pseudo/botparadox\").")
    if not version:
        sys.exit("Configure 'version' dans release_config.json (ex. \"1.0.0\").")

    # gh present et connecte ?
    try:
        subprocess.run(["gh", "auth", "status"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        sys.exit("gh non connecte. Lance d'abord :  gh auth login")

    print(f"== Build de la version {version} ==")
    subprocess.run([sys.executable, os.path.join(PROJECT, "build_installer.py")],
                   check=True)
    if not os.path.exists(SETUP):
        sys.exit("Setup introuvable apres le build.")

    tag = "v" + version
    print(f"== Publication de la release {tag} sur {repo} ==")
    subprocess.run(
        ["gh", "release", "create", tag, SETUP,
         "--repo", repo,
         "--title", f"Bot Paradox {tag}",
         "--notes", f"Mise a jour {tag} (installation automatique)."],
        check=True)
    print(f"\n[OK] Release {tag} publiee. Les amis seront mis a jour au prochain lancement.")


if __name__ == "__main__":
    main()
