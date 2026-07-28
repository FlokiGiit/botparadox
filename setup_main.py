"""
Installateur / metteur a jour de Bot Paradox.

Fige par PyInstaller en BotParadox-Setup.exe, avec l'application entiere
embarquee (dossier "payload"). Au lancement, SANS aucune fenetre d'extraction :

  1. ferme les instances en cours,
  2. copie l'appli dans %LOCALAPPDATA%\\BotParadox (garde les donnees locales),
  3. detecte le client Nexus (demande le dossier si introuvable),
  4. applique les patchs (overlay + ponts),
  5. cree le raccourci Bureau,
  6. lance Bot Paradox.

Remplace l'ancien 7z SFX, qui n'etait qu'un extracteur (fenetre "Extract to")
et ne lancait jamais l'installation -> AppData jamais mis a jour.
"""

import os
import shutil
import subprocess
import sys
import time

TARGET = os.path.join(os.environ.get("LOCALAPPDATA",
                      os.path.expanduser(r"~\AppData\Local")), "BotParadox")

# Fichiers propres a l'utilisateur : jamais ecrases par une mise a jour.
PRESERVE = {"session.json", "client_override.txt", "client_path.txt",
            "handoff.json"}

# Fenetre cachee pour les sous-process (pas de flash de console).
_NOWIN = 0x08000000  # CREATE_NO_WINDOW


def resource(*parts):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def log(msg):
    try:
        os.makedirs(TARGET, exist_ok=True)
        with open(os.path.join(TARGET, "setup.log"), "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except OSError:
        pass


def kill_running():
    for exe in ("BotParadox.exe", "botcore.exe"):
        subprocess.run(["taskkill", "/IM", exe, "/F"],
                       capture_output=True, creationflags=_NOWIN)
    time.sleep(1.0)   # laisse les handles se liberer


def copy_payload():
    src = resource("payload")
    for root, _dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        dst = TARGET if rel == "." else os.path.join(TARGET, rel)
        os.makedirs(dst, exist_ok=True)
        for f in files:
            out = os.path.join(dst, f)
            if f in PRESERVE and os.path.exists(out):
                continue
            for attempt in range(5):     # l'exe peut etre brievement verrouille
                try:
                    shutil.copy2(os.path.join(root, f), out)
                    break
                except PermissionError:
                    time.sleep(1.0)
            else:
                log(f"copie impossible : {out}")
    log("payload copie")


def ask_client_folder():
    try:
        import tkinter
        import tkinter.filedialog as fd
        tk = tkinter.Tk()
        tk.withdraw()
        p = fd.askdirectory(
            title="Ou est installe ton launcher Nexus ? (dossier contenant srv_nexus)")
        tk.destroy()
        return p or ""
    except Exception as e:
        log(f"dialogue dossier indispo : {e}")
        return ""


def detect_and_patch():
    botcore = os.path.join(TARGET, "botcore", "botcore.exe")
    if not os.path.exists(botcore):
        log("botcore absent apres copie")
        return
    try:
        r = subprocess.run([botcore, "--detect"], capture_output=True,
                           text=True, timeout=90, creationflags=_NOWIN)
        found = (r.stdout or "").strip()
        log(f"detect -> {found!r}")
        if found == "NOTFOUND" or r.returncode != 0:
            pick = ask_client_folder()
            if pick:
                data = os.path.join(TARGET, "botcore", "data")
                os.makedirs(data, exist_ok=True)
                with open(os.path.join(data, "client_override.txt"),
                          "w", encoding="utf-8") as f:
                    f.write(pick)
                log(f"override -> {pick}")
        subprocess.run([botcore, "--patch"], timeout=180, creationflags=_NOWIN)
        log("patch applique")
    except Exception as e:
        log(f"detect/patch erreur : {e}")


def make_shortcut():
    exe = os.path.join(TARGET, "BotParadox.exe")
    ps = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut("
        "[Environment]::GetFolderPath('Desktop')+'\\Bot Paradox.lnk');"
        f"$s.TargetPath='{exe}';$s.WorkingDirectory='{TARGET}';"
        f"$s.IconLocation='{exe},0';$s.Save()"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, timeout=30, creationflags=_NOWIN)
        log("raccourci cree")
    except Exception as e:
        log(f"raccourci erreur : {e}")


def main():
    log("=== installation / mise a jour ===")
    kill_running()
    copy_payload()
    detect_and_patch()
    make_shortcut()
    try:
        os.startfile(os.path.join(TARGET, "BotParadox.exe"))
        log("Bot Paradox lance")
    except Exception as e:
        log(f"lancement erreur : {e}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"ECHEC GLOBAL : {e}")
