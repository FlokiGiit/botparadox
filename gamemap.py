"""
Téléchargement, déchiffrement et lecture des cartes Dofus 1.29 (Paradox).

Le serveur envoie `GDM|<mapId>|<date>|<cleHex>`. La géométrie n'est pas dans
ce paquet : elle est dans un SWF téléchargé à part, chiffré avec cette clé.

Le décalage de déchiffrement se calcule depuis la clé — vérifié sur les cartes
10311 (décalage 30) et 10310 (décalage 24), deux valeurs différentes trouvées
par force brute puis reproduites par la formule.
"""

import os
import re
import urllib.error
import urllib.parse
import urllib.request
import zlib

CHAIN = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
DATA_URL = "https://data.nexus-temporel.com/dofus/maps/{map_id}_{date}{suffix}.swf"
from apppaths import MAPS_DIR as CACHE_DIR

# Grille isométrique standard : 17 rangées de 15 cellules alternant avec
# 16 rangées de 14, soit 15*17 + 14*16 = 479 cellules.
WIDTH = 15
ROW_STRIDE = WIDTH * 2 - 1  # 29 cellules par paire de rangées

# Directions Dofus : 0=E, 1=SE, 2=S, 3=SO, 4=O, 5=NO, 6=N, 7=NE.
# Le décalage de colonne dépend de la parité de la demi-rangée, sauf pour les
# déplacements droits. Confirmé sur les captures : la direction 2 correspond
# à +29 (344->402) et la direction 6 à -29 (289->260).
#   direction: (delta rangée, delta colonne si rangée paire, si rangée impaire)
DIRECTIONS = {
    0: (0, +1, +1),    # E   -> +1
    1: (+1, 0, +1),    # SE  -> +15
    2: (+2, 0, 0),     # S   -> +29
    3: (+1, -1, 0),    # SO  -> +14
    4: (0, -1, -1),    # O   -> -1
    5: (-1, -1, 0),    # NO  -> -15
    6: (-2, 0, 0),     # N   -> -29
    7: (-1, 0, +1),    # NE  -> -14
}


def rowcol(cell):
    """Cellule -> (demi-rangée, colonne). Les rangées paires ont 15 cellules,
    les impaires 14, décalées d'un demi-pas."""
    pair, rem = divmod(cell, ROW_STRIDE)
    if rem < WIDTH:
        return pair * 2, rem
    return pair * 2 + 1, rem - WIDTH


def from_rowcol(row, col):
    """Inverse de rowcol ; None si hors grille."""
    if row < 0:
        return None
    pair, odd = divmod(row, 2)
    limit = WIDTH - 1 if odd else WIDTH
    if not 0 <= col < limit:
        return None
    return pair * ROW_STRIDE + (WIDTH if odd else 0) + col


def compress_path(cells):
    """Liste de cellules -> format serveur (un maillon par changement de cap).

    Le client n'envoie pas chaque cellule : il envoie [direction][cellule]
    pour chaque segment rectiligne. `dfycgs` = direction 3 jusqu'à 344, puis
    direction 2 jusqu'à 402.
    """
    if len(cells) < 2:
        return ""
    steps = []
    for a, b in zip(cells, cells[1:]):
        row, col = rowcol(a)
        direction = next(
            (d for d, (dr, dce, dco) in DIRECTIONS.items()
             if from_rowcol(row + dr, col + (dco if row % 2 else dce)) == b),
            None,
        )
        # Deux cellules non adjacentes ne peuvent pas être encodées. Ça ne
        # devrait pas arriver — A* ne produit que des pas voisins — mais mieux
        # vaut renvoyer un chemin vide que faire remonter une exception.
        if direction is None:
            return ""
        steps.append((direction, b))

    out = []
    for i, (direction, cell) in enumerate(steps):
        last = i == len(steps) - 1
        if last or steps[i + 1][0] != direction:
            out.append((direction, cell))
    return "".join(CHAIN[d] + CHAIN[c // 64] + CHAIN[c % 64] for d, c in out)


# Certaines cartes n'existent pas sur le serveur de donnees (le hub de
# rotation de donjon 80208 renvoie 404). Sans memoire de l'echec, le bot
# retentait le telechargement 23 fois par session.
_MISSING = set()

# Cache des cartes déjà parsées : un même GDM (même map+date) était
# rechargé des centaines de fois / session (thread pool + gros SWF en RAM).
_MAP_CACHE = {}
_MAP_CACHE_MAX = 12


def load_map(map_id, date, key_hex=""):
    """GameMap depuis le cache mémoire, sinon parse + mémorise (LRU simple)."""
    key = (int(map_id), str(date), str(key_hex or ""))
    hit = _MAP_CACHE.get(key)
    if hit is not None:
        # LRU : remonter en fin.
        _MAP_CACHE.pop(key, None)
        _MAP_CACHE[key] = hit
        return hit
    gmap = GameMap(map_id, date, key_hex)
    _MAP_CACHE[key] = gmap
    while len(_MAP_CACHE) > _MAP_CACHE_MAX:
        _MAP_CACHE.pop(next(iter(_MAP_CACHE)))
    return gmap


def _fetch(map_id, date, suffix=""):
    """Télécharge le SWF de carte, avec cache disque (les cartes sont figées).

    Le suffixe distingue les deux formats servis : "X" pour les cartes
    classiques (hex chiffré), "" pour les cartes du nouveau format (en clair)."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{map_id}_{date}{suffix}.swf")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()

    if map_id in _MISSING:
        raise FileNotFoundError(f"carte {map_id} absente du serveur")
    url = DATA_URL.format(map_id=map_id, date=date, suffix=suffix)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        raw = urllib.request.urlopen(req, timeout=20).read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            _MISSING.add(map_id)
        raise
    with open(path, "wb") as f:
        f.write(raw)
    return raw


def _body(swf_bytes):
    """Corps du SWF, décompressé si zlib (CWS)."""
    return zlib.decompress(swf_bytes[8:]) if swf_bytes[:3] == b"CWS" else swf_bytes[8:]


def _extract_hex(swf_bytes):
    """Ancien format : longue chaîne hex chiffrée dans le pool AS2."""
    candidates = re.findall(rb"[0-9a-f]{1000,}", _body(swf_bytes))
    if not candidates:
        raise ValueError("données de carte (hex) introuvables dans le SWF")
    return max(candidates, key=len).decode("ascii")


def _extract_plain(swf_bytes):
    """Nouveau format (maj serveur 2026-07) : cellules EN CLAIR dans le pool
    AS2, 10 caractères CHAIN par cellule, plus de hex ni de chiffrement."""
    candidates = re.findall(rb"[A-Za-z0-9_-]{200,}", _body(swf_bytes))
    if not candidates:
        raise ValueError("données de carte introuvables dans le SWF")
    return max(candidates, key=len).decode("ascii")


def checksum_of(key):
    """Décalage de déchiffrement, dérivé de la clé."""
    return (sum(ord(c) % 16 for c in key) % 16) * 2


def decrypt(data_hex, key_hex):
    key = urllib.parse.unquote(bytes.fromhex(key_hex).decode("latin-1"))
    offset = checksum_of(key)
    out = []
    for i in range(0, len(data_hex), 2):
        code = int(data_hex[i:i + 2], 16)
        out.append(chr(code ^ ord(key[(i // 2 + offset) % len(key)])))
    return "".join(out)


class GameMap:
    """Une carte : praticabilité des cellules et voisinage."""

    def __init__(self, map_id, date, key_hex=""):
        self.map_id = int(map_id)
        self.date = str(date)
        # Deux formats coexistent, distingués par la présence d'une clé dans le
        # GDM : clé => carte classique (URL avec X, hex chiffré) ; pas de clé =>
        # nouveau format (URL sans X, cellules en clair).
        if key_hex:
            raw = decrypt(_extract_hex(_fetch(map_id, date, "X")), key_hex)
        else:
            raw = _extract_plain(_fetch(map_id, date, ""))
        if len(raw) % 10:
            raise ValueError(f"longueur inattendue : {len(raw)}")

        self.cells = []
        # gfx de l'objet interactif (ressource) par cellule, quand présent :
        # couche objet2 = 13 bits (c7<<12 | c8<<6 | c9). Vérifié sur une récolte
        # réelle (case 432 -> gfx 7500 = Frêne) et recoupé sur toute la carte
        # (Frêne/Châtaignier/Noyer/Trèfle/Menthe = ce qu'il y a sur la map).
        self.node_gfx = {}
        for i in range(0, len(raw), 10):
            c = [CHAIN.index(ch) for ch in raw[i:i + 10]]
            gfx = ((c[7] << 12) | (c[8] << 6) | c[9]) & 0x1FFF
            if gfx:
                self.node_gfx[i // 10] = gfx
            self.cells.append({
                "active": bool(c[0] & 0x20),
                "los": bool(c[0] & 0x01),
                # Champ trouvé en analysant les 60 bits : c'est le seul qui
                # vaille 1 sur les cellules réellement traversées (344, 402)
                # tout en restant vrai pour une majorité de cellules.
                # Trois valeurs observées : 0 (obstacle), 1, 2 (praticable).
                "movement": (c[2] & 0x30) >> 4,
            })

    def __len__(self):
        return len(self.cells)

    def walkable(self, cell):
        if not 0 <= cell < len(self.cells):
            return False
        c = self.cells[cell]
        return c["active"] and c["movement"] == 2

    def coords(self, cell):
        """Cellule -> (x, y) en repère grille, y compris le décalage isométrique."""
        row, rem = divmod(cell, ROW_STRIDE)
        if rem < WIDTH:
            return rem, row * 2          # rangée large (15 cellules)
        return rem - WIDTH, row * 2 + 1  # rangée décalée (14 cellules)

    def neighbours(self, cell):
        """Les 8 voisins, sous forme (direction, cellule)."""
        row, col = rowcol(cell)
        out = []
        for direction, (drow, dcol_even, dcol_odd) in DIRECTIONS.items():
            dcol = dcol_odd if row % 2 else dcol_even
            n = from_rowcol(row + drow, col + dcol)
            # from_rowcol rejette les débordements de colonne, ce qui empêche
            # un déplacement de "sortir" par un bord pour réapparaître à l'autre.
            if n is not None and n < len(self.cells):
                out.append((direction, n))
        return out

    def find_path(self, start, goal, blocked=None):
        """A* ; renvoie la liste des cellules de start à goal, ou None.

        `blocked` : cases occupées par un acteur, à contourner. Le serveur
        tronque un déplacement qui traverse quelqu'un, donc un chemin qui les
        ignore mène à un trajet que le personnage n'effectuera jamais. La case
        d'arrivée reste autorisée : c'est justement celle du mob qu'on agresse.
        """
        if start == goal:
            return [start]
        if not self.walkable(goal):
            return None
        blocked = (blocked or set()) - {start, goal}

        def h(c):
            r1, c1 = rowcol(c)
            r2, c2 = rowcol(goal)
            return abs(r1 - r2) + abs(c1 - c2)

        open_set = [(h(start), 0, start)]
        came = {start: None}
        cost = {start: 0}
        while open_set:
            open_set.sort()
            _, g, cur = open_set.pop(0)
            if cur == goal:
                path = []
                while cur is not None:
                    path.append(cur)
                    cur = came[cur]
                return path[::-1]
            for _, nxt in self.neighbours(cur):
                if not self.walkable(nxt) or nxt in blocked:
                    continue
                ng = g + 1
                if ng < cost.get(nxt, 1 << 30):
                    cost[nxt] = ng
                    came[nxt] = cur
                    open_set.append((ng + h(nxt), ng, nxt))
        return None

    def render(self, marks=None):
        """Vue ASCII, pour vérifier d'un coup d'oeil que le parsing est bon."""
        marks = marks or {}
        lines = []
        for row in range(33):
            wide = row % 2 == 0
            count = WIDTH if wide else WIDTH - 1
            base = (row // 2) * ROW_STRIDE + (0 if wide else WIDTH)
            cells = []
            for i in range(count):
                cell = base + i
                if cell >= len(self.cells):
                    break
                cells.append(marks.get(cell, "." if self.walkable(cell) else "#"))
            lines.append(("" if wide else " ") + " ".join(cells))
        return "\n".join(lines)
