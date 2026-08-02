"""Portée et ligne de vue Dofus 1.29, calculées EN LOCAL (aucune requête serveur).

Repris tel quel de l'émulateur (PathFinding.java) pour coller exactement aux
règles du serveur :

- coordonnées Dofus : ``cell = 15*x + 14*y`` (donc x,y inversibles),
  distance = |dx| + |dy| (getCellXCoord / getCellYCoord / getDistanceBetween) ;
- ligne de vue : checkLoS / getLoSBotheringIDCases — on avance depuis les deux
  extrémités vers le centre et on vérifie chaque case obstacle. Une case laisse
  passer la vue si ``gmap.cells[c]["los"]`` est vrai (= blockLoS côté serveur) ;
  un combattant sur une case intermédiaire bloque aussi la vue.

Sert à l'assistant de combat (dashboard /assist) : surligner les cases où un
sort peut être lancé (portée + LdV), sans envoyer de ZDC au serveur.
"""

W = 15                       # largeur de grille (map.getW)
STRIDE = W * 2 - 1           # 29

# Déplacements élémentaires en coordonnées Dofus (dx, dy). Vérifiés contre
# GetCaseIDFromDirrection : b=+15(+x), f=-15(-x), d=+14(+y), h=-14(-y),
# a=+1(+x,-y), e=-1(-x,+y), c=+29(+x,+y), g=-29(-x,-y).
DELTA = {
    "b": (1, 0), "f": (-1, 0), "d": (0, 1), "h": (0, -1),
    "a": (1, -1), "e": (-1, 1), "c": (1, 1), "g": (-1, -1),
}
# Direction opposée (décalage de 4 : a<->e, b<->f, c<->g, d<->h).
OPP = {"a": "e", "e": "a", "b": "f", "f": "b",
       "c": "g", "g": "c", "d": "h", "h": "d"}


def cell_y(cell):
    loc5 = cell // STRIDE
    loc6 = cell - loc5 * STRIDE
    return loc5 - (loc6 % W)


def cell_x(cell):
    return (cell - (W - 1) * cell_y(cell)) // W


def distance(a, b):
    return abs(cell_x(a) - cell_x(b)) + abs(cell_y(a) - cell_y(b))


def step(total, cell, direction):
    """Case voisine dans une direction ('a'..'h'), ou -1 hors grille.

    On passe par (x,y) puis on revérifie le round-trip : ça rejette les
    débordements de bord (une case qui « sortirait » par un côté)."""
    dx, dy = DELTA[direction]
    x, y = cell_x(cell) + dx, cell_y(cell) + dy
    c = W * x + (W - 1) * y
    if 0 <= c < total and cell_x(c) == x and cell_y(c) == y:
        return c
    return -1


def _bothering_diag(total, c1, c2, dx, dy):
    if dx > 0 and dy > 0:
        d = "g"
    elif dx > 0 and dy < 0:
        d = "e"
    elif dx < 0 and dy > 0:
        d = "a"
    else:
        d = "c"
    out = []
    cur = c1
    compteur = 0
    while cur != -1 and compteur < 100:
        cur = step(total, cur, d)
        if cur == c2:
            break
        out.append(cur)
        compteur += 1
    return out


def _bothering(total, c1, c2):
    """Cases susceptibles d'obstruer la vue entre c1 et c2 (port de
    getLoSBotheringIDCases, Combat=true : le pas cardinal ne dépend pas du
    mode)."""
    out = []
    a, b = c1, c2
    direction = "b"
    compteur = 0
    while distance(a, b) > 2 and compteur < 300:
        dx = cell_x(a) - cell_x(b)
        dy = cell_y(a) - cell_y(b)
        if abs(dx) > abs(dy):
            direction = "f" if dx > 0 else "b"
        elif abs(dx) < abs(dy):
            direction = "h" if dy > 0 else "d"
        else:
            if compteur == 0:
                return _bothering_diag(total, c1, c2, dx, dy)
            if direction in ("f", "b"):
                direction = "h" if dy > 0 else "d"
            else:
                direction = "f" if dx > 0 else "b"
        a = step(total, a, direction)
        b = step(total, b, OPP[direction])
        out.append(a)
        out.append(b)
        compteur += 1
    if distance(a, b) == 2:
        dx = cell_x(a) - cell_x(b)
        dy = cell_y(a) - cell_y(b)
        d2 = None
        if dx == 0:
            d2 = "h" if dy > 0 else "d"
        elif dy == 0:
            d2 = "f" if dx > 0 else "b"
        if d2:
            out.append(step(total, a, d2))
    return out


def check_los(gmap, c1, c2, blockers=None):
    """Vrai si la vue est dégagée entre c1 et c2. `blockers` = cases occupées
    par un combattant (elles bloquent la vue), la cible c2 exclue."""
    total = len(gmap)
    blockers = blockers or set()
    for cid in _bothering(total, c1, c2):
        if cid < 0 or cid == c1 or cid == c2:
            continue
        cell = gmap.cells[cid] if cid < total else None
        if cell is not None and not cell["los"]:
            return False
        if cid in blockers:
            return False
    return True


def rotate_around(total, center, cell, quarter_clockwise):
    """Case obtenue en faisant tourner `cell` autour de `center` de N quarts de
    tour horaires (repris de rotateCellAroundCaster du Comte Harebourg). -1 si
    hors grille. q1: (dx,dy)->(dy,-dx) ; q2: (-dx,-dy) ; q3: (-dy,dx)."""
    q = quarter_clockwise % 4
    cx, cy = cell_x(center), cell_y(center)
    dx, dy = cell_x(cell) - cx, cell_y(cell) - cy
    if q == 1:
        rx, ry = dy, -dx
    elif q == 2:
        rx, ry = -dx, -dy
    elif q == 3:
        rx, ry = -dy, dx
    else:
        rx, ry = dx, dy
    x, y = cx + rx, cy + ry
    c = W * x + (W - 1) * y
    if 0 <= c < total and cell_x(c) == x and cell_y(c) == y:
        return c
    return -1


def valid_target_cells(gmap, center, rmin, rmax, needs_los, free_cell, occupied,
                       placement=False):
    """Cases où le sort peut être lancé depuis `center` : dans la portée
    [rmin, rmax], LdV dégagée si le sort l'exige. `occupied` = set des cases
    avec un combattant.

    - placement=True (invocation) : la case doit être LIBRE et PRATICABLE
      (on pose une créature dessus).
    - sinon : occupée par une entité si le sort n'accepte pas les cellules
      libres (free_cell)."""
    total = len(gmap)
    occupied = set(occupied)
    out = []
    for c in range(total):
        if c == center:
            continue
        d = distance(center, c)
        if d < rmin or d > rmax:
            continue
        if placement:
            if c in occupied or not gmap.walkable(c):
                continue
        elif not free_cell and c not in occupied:
            continue
        if needs_los and not check_los(gmap, center, c, occupied - {c}):
            continue
        out.append(c)
    return out
