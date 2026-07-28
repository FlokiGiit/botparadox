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


def _base_dir():
    if getattr(sys, "frozen", False):        # execute depuis un exe PyInstaller
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _base_dir()
DATA_DIR = os.path.join(BASE_DIR, "data")
LOG_DIR = os.path.join(BASE_DIR, "logs")
MAPS_DIR = os.path.join(BASE_DIR, "maps")
HANDOFF_FILE = os.path.join(BASE_DIR, "handoff.json")


def data(*parts):
    return os.path.join(DATA_DIR, *parts)
