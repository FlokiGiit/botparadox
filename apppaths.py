"""
Dossiers de l'application, resolus une fois pour toutes.

En developpement, tout vit a cote des sources. Une fois fige par PyInstaller,
`__file__` pointe a l'interieur du bundle (lecture seule, ephemere) : on se base
alors sur le dossier de l'executable, ou l'installateur a depose data/, et ou le
bot peut ecrire handoff.json, logs/, etc.

Tous les modules qui manipulent des chemins importent d'ici plutot que de
recalculer depuis leur propre `__file__`.
"""

import os
import sys
import tempfile


def _base_dir():
    if getattr(sys, "frozen", False):        # execute depuis un exe PyInstaller
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _base_dir()
DATA_DIR = os.path.join(BASE_DIR, "data")
LOG_DIR = os.path.join(BASE_DIR, "logs")
MAPS_DIR = os.path.join(BASE_DIR, "maps")

# handoff.json DOIT etre au MEME endroit pour le client (qui l'ecrit) et pour le
# bot (qui le lit), sinon le proxy ne trouve pas la cible et abandonne la
# connexion ("ABANDON : handoff.json absent" -> deconnexion). On le met donc a
# un emplacement FIXE, commun a toutes les versions du bot (dev, packagee,
# installee) : peu importe le bot lance, ils partagent le meme fichier.
_SHARED_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(), "BotParadox")
try:
    os.makedirs(_SHARED_DIR, exist_ok=True)   # doit exister avant que le client ecrive
except OSError:
    pass
HANDOFF_FILE = os.path.join(_SHARED_DIR, "handoff.json")


def data(*parts):
    return os.path.join(DATA_DIR, *parts)
