"""Cases qui font changer de carte (les « soleils »), apprises en jouant.

Aucune donnée ne les liste : le SWF de carte n'expose que
width/height/mapData, et le serveur ne les annonce pas. Une première version
les devinait par géométrie (tout le bord de la grille) — beaucoup trop large :
le bot refusait des groupes parfaitement attaquables et restait planté à
attendre un repop qui était déjà là.

On les apprend donc par l'observation, ce qui est exact par construction : quand
un déplacement du bot se termine par un changement de carte, la case d'arrivée
EST une sortie. Le résultat est mémorisé par carte dans data/map_exits.json et
resservi aux sessions suivantes.
"""

import json
import os
import threading

from apppaths import data as _data

FILE = _data("map_exits.json")

# Garde-fous : une carte a une poignée de sorties, et un farmeur ne visite pas
# des milliers de cartes. Bornes larges, juste pour que le fichier ne grossisse
# jamais indéfiniment.
MAX_CELLS_PER_MAP = 40
MAX_MAPS = 400

_lock = threading.Lock()
_cache = None


def _load():
    global _cache
    if _cache is not None:
        return _cache
    try:
        with open(FILE, encoding="utf-8") as f:
            raw = json.load(f)
        _cache = {str(k): set(int(c) for c in v) for k, v in raw.items()}
    except (OSError, ValueError, TypeError):
        _cache = {}
    return _cache


def known(map_id):
    """Sorties connues de cette carte (set, éventuellement vide)."""
    if map_id is None:
        return frozenset()
    return _load().get(str(map_id), frozenset())


def learn(map_id, cell):
    """Mémorise une sortie. Renvoie True si c'est une découverte."""
    if map_id is None or cell is None:
        return False
    with _lock:
        data = _load()
        cells = data.setdefault(str(map_id), set())
        if cell in cells:
            return False
        if len(cells) >= MAX_CELLS_PER_MAP:
            return False
        cells.add(int(cell))
        if len(data) > MAX_MAPS:
            # La plus ancienne carte connue s'efface : on ne farme jamais
            # 400 cartes à la fois, et une sortie se réapprend en un passage.
            data.pop(next(iter(data)), None)
        _save(data)
        return True


def _save(data):
    try:
        os.makedirs(os.path.dirname(FILE), exist_ok=True)
        tmp = FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({k: sorted(v) for k, v in data.items()}, f)
        os.replace(tmp, FILE)
    except OSError:
        pass
