"""
Emplacement du client Dofus, resolu en un seul endroit.

Le client vit hors du projet, a un endroit qui varie d'une machine a l'autre
(nom d'utilisateur, dossier d'install du launcher Nexus) et qu'une mise a jour
peut deplacer. On le detecte donc dynamiquement, dans cet ordre :

  1. variable d'environnement DOFUS_CLIENT_APP (surcharge ponctuelle)
  2. data/client_override.txt (chemin choisi par l'ami a l'installation)
  3. emplacements connus, deduits des variables d'environnement du PC
  4. balayage borne des racines probables a la recherche du client

Tous les modules importent d'ici ; l'UI lit le pointeur data/client_path.txt.
Le chemin trouve est ...\\resources\\app (parent de retroclient et ipc).
"""

import os

from apppaths import DATA_DIR as _DATA_DIR

_MARKER = os.path.join("retroclient", "D1ElectronLauncher.html")
_OVERRIDE_FILE = os.path.join(_DATA_DIR, "client_override.txt")

# Sous-chemin du client relatif a une racine d'installation quelconque.
_SUFFIXES = [
    os.path.join("srv_nexus", "resources", "app"),
    os.path.join("resources", "app"),
    os.path.join("Nexus", "srv_nexus", "resources", "app"),
]


def _env_roots():
    """Racines d'install plausibles, construites depuis l'environnement du PC
    (donc valables quel que soit le nom d'utilisateur)."""
    roots = []
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    for base in (
        os.path.join(home, "Desktop"),
        home,
        os.environ.get("LOCALAPPDATA", ""),
        os.environ.get("APPDATA", ""),
        os.environ.get("PROGRAMFILES", ""),
        os.environ.get("PROGRAMFILES(X86)", ""),
        "C:\\",
    ):
        if base:
            roots.append(os.path.join(base, "Nexus"))
            roots.append(base)
    # Ancien client Paradox, si encore present.
    if os.environ.get("APPDATA"):
        roots.append(os.path.join(os.environ["APPDATA"], "Paradox"))
    return roots


def _has_client(app):
    return bool(app) and os.path.exists(os.path.join(app, _MARKER))


def _read_override():
    try:
        with open(_OVERRIDE_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _from_root(root):
    """Cherche le client sous une racine : la racine elle-meme, puis chaque
    suffixe connu."""
    if _has_client(root):
        return root
    for suf in _SUFFIXES:
        cand = os.path.join(root, suf)
        if _has_client(cand):
            return cand
    return None


def _scan(root, depth=3):
    """Balayage borne : descend jusqu'a `depth` niveaux pour trouver le marqueur.
    Coupe les branches lourdes (node_modules) pour rester rapide."""
    root = os.path.abspath(root)
    base_depth = root.rstrip("\\/").count(os.sep)
    try:
        for cur, dirs, _files in os.walk(root):
            if cur.count(os.sep) - base_depth > depth:
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs
                       if d.lower() not in ("node_modules", "cache", "locales")]
            if os.path.exists(os.path.join(cur, _MARKER)):
                return cur
    except OSError:
        pass
    return None


def _detect():
    # 1. surcharge explicite
    env = os.environ.get("DOFUS_CLIENT_APP")
    if _has_client(env):
        return env
    # 2. choix persiste par l'ami : peut pointer resources\app, srv_nexus, ou la
    #    racine Nexus — on essaie les suffixes puis un balayage.
    ov = _read_override()
    if ov and os.path.isdir(ov):
        found = _from_root(ov) or _scan(ov, depth=4)
        if found:
            return found
    # 3. emplacements deduits de l'environnement
    for root in _env_roots():
        found = _from_root(root)
        if found:
            return found
    # 4. balayage borne des racines probables
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    for root in (os.path.join(home, "Desktop", "Nexus"),
                 os.path.join(home, "Nexus"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Nexus")):
        if root and os.path.isdir(root):
            found = _scan(root)
            if found:
                return found
    # Rien trouve : renvoie un chemin plausible (le bot logguera l'absence).
    return os.path.join(home, "Desktop", "Nexus", "srv_nexus", "resources", "app")


APP_ROOT = _detect()
RETROCLIENT = os.path.join(APP_ROOT, "retroclient")
CLIENT_HTML = os.path.join(RETROCLIENT, "D1ElectronLauncher.html")
ICON_ROOT = os.path.join(RETROCLIENT, "clips", "items")
LOGIN_BRIDGE = os.path.join(APP_ROOT, "ipc", "login-tcp-bridge.js")
PANEL_HANDLERS = os.path.join(APP_ROOT, "ipc", "handlers", "panel-handlers.js")


def _write_pointer():
    """Depose le chemin retroclient resolu pour les consommateurs non-Python."""
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        with open(os.path.join(_DATA_DIR, "client_path.txt"), "w",
                  encoding="utf-8") as f:
            f.write(RETROCLIENT)
    except OSError:
        pass


_write_pointer()


if __name__ == "__main__":
    print("APP_ROOT      ", APP_ROOT, "  (existe:", os.path.exists(APP_ROOT), ")")
    print("CLIENT_HTML   ", CLIENT_HTML, "  (existe:", os.path.exists(CLIENT_HTML), ")")
    print("ICON_ROOT     ", ICON_ROOT, "  (existe:", os.path.exists(ICON_ROOT), ")")
    print("LOGIN_BRIDGE  ", LOGIN_BRIDGE, "  (existe:", os.path.exists(LOGIN_BRIDGE), ")")
    print("PANEL_HANDLERS", PANEL_HANDLERS, "  (existe:", os.path.exists(PANEL_HANDLERS), ")")
