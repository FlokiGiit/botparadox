"""
Encodage/décodage des cellules et chemins Dofus 1.29 (serveur Paradox).

Confirmé par la capture du 19/07 : le paquet de récolte visait la cellule 402
(`GA500402;45`) et le dernier maillon du chemin envoyé juste avant valait
`gs` — soit 6*64+18 = 402. L'alphabet est le même que le CHAIN utilisé par
login-tcp-bridge.js pour le chiffrement du mot de passe.
"""

# a-z (0-25), A-Z (26-51), 0-9 (52-61), - (62), _ (63)
CHAIN = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"


def cell_decode(pair):
    """'gs' -> 402"""
    return CHAIN.index(pair[0]) * 64 + CHAIN.index(pair[1])


def cell_encode(cell):
    """402 -> 'gs'"""
    return CHAIN[cell // 64] + CHAIN[cell % 64]


def path_decode(path):
    """'dfycgs' -> [(3, 344), (2, 402)]

    Un chemin est une suite de triplets [direction][cellule sur 2 caractères].
    Les directions vont de 0 à 7 (a..h), sens horaire.
    """
    steps = []
    for i in range(0, len(path) - 2, 3):
        direction = CHAIN.index(path[i])
        cell = cell_decode(path[i + 1:i + 3])
        steps.append((direction, cell))
    return steps


def path_encode(steps):
    """[(3, 344), (2, 402)] -> 'dfycgs'"""
    return "".join(CHAIN[d] + cell_encode(c) for d, c in steps)
