"""
Interpréteur minimal d'ActionScript 2 (AVM1), juste ce qu'il faut pour lire
les fichiers de langue du client Dofus.

Ces fichiers ne contiennent pas de logique : uniquement une longue suite
d'affectations du genre `I.u[7941] = {n:"Blé", g:1234, ...}`. Une dizaine
d'instructions suffit donc, et on garde la maîtrise de deux points que les
outils génériques gèrent mal ici :

  encodage  les chaînes sont en cp1252, pas en UTF-8 ; les décoder en UTF-8
            détruit irrémédiablement tous les accents
  identité  `I.u` doit désigner le même objet d'un tag à l'autre, sinon chaque
            morceau du fichier écrase le précédent au lieu de le compléter
"""

import struct
import zlib

# Les seules instructions présentes dans ces fichiers.
CONSTANT_POOL = 0x88
PUSH = 0x96
POP = 0x17
GET_VARIABLE = 0x1C
SET_VARIABLE = 0x1D
GET_MEMBER = 0x4E
SET_MEMBER = 0x4F
INIT_ARRAY = 0x42
INIT_OBJECT = 0x43
DEFINE_LOCAL = 0x3C
END = 0x00


def decompress(raw):
    """Rend le corps du SWF, décompressé si besoin."""
    if raw[:3] == b"CWS":
        return zlib.decompress(raw[8:])
    if raw[:3] == b"FWS":
        return raw[8:]
    raise ValueError(f"signature inconnue : {raw[:3]!r}")


def _rect_size(data, pos):
    """Le SWF débute par un rectangle de taille variable, à sauter."""
    nbits = data[pos] >> 3
    total = 5 + nbits * 4
    return (total + 7) // 8


def iter_tags(body):
    """Parcourt les tags du SWF : (code, contenu)."""
    pos = _rect_size(body, 0) + 4          # rectangle + cadence + nb d'images
    while pos + 2 <= len(body):
        (code_len,) = struct.unpack_from("<H", body, pos)
        pos += 2
        code, length = code_len >> 6, code_len & 0x3F
        if length == 0x3F:
            (length,) = struct.unpack_from("<I", body, pos)
            pos += 4
        yield code, body[pos:pos + length]
        pos += length


def _read_string(data, pos):
    end = data.index(b"\x00", pos)
    raw = data[pos:end]
    # Le fichier mélange les encodages : l'essentiel est en cp1252, mais une
    # partie des entrées (celles ajoutées par le serveur) est en UTF-8. On
    # tente donc l'UTF-8 d'abord, qui échoue proprement sur du cp1252 réel,
    # et on retombe sur cp1252 sinon — où aucun octet ne peut échouer.
    try:
        return raw.decode("utf-8"), end + 1
    except UnicodeDecodeError:
        return raw.decode("cp1252"), end + 1


class Machine:
    """Exécute les instructions et accumule les variables globales."""

    def __init__(self):
        self.globals = {}
        self.pool = []

    def run(self, code):
        stack = []
        pos = 0
        n = len(code)
        while pos < n:
            op = code[pos]
            pos += 1
            if op == END:
                break
            if op < 0x80:                   # instruction sans argument
                length = 0
            else:
                (length,) = struct.unpack_from("<H", code, pos)
                pos += 2
            body = code[pos:pos + length]
            pos += length

            try:
                self._step(op, body, stack)
            except (IndexError, ValueError, KeyError, struct.error):
                # Une instruction mal formée ne doit pas interrompre la
                # lecture du reste du fichier.
                stack.clear()

    def _step(self, op, body, stack):
        if op == CONSTANT_POOL:
            (count,) = struct.unpack_from("<H", body, 0)
            pos = 2
            self.pool = []
            for _ in range(count):
                s, pos = _read_string(body, pos)
                self.pool.append(s)

        elif op == PUSH:
            pos = 0
            while pos < len(body):
                kind = body[pos]
                pos += 1
                if kind == 0:
                    val, pos = _read_string(body, pos)
                elif kind == 1:
                    (val,) = struct.unpack_from("<f", body, pos); pos += 4
                elif kind == 2:
                    val = None
                elif kind == 3:
                    val = None
                elif kind == 4:
                    val = None; pos += 1          # registre, non utilisé ici
                elif kind == 5:
                    val = bool(body[pos]); pos += 1
                elif kind == 6:
                    # Double AVM1 : les deux moitiés de 32 bits sont inversées.
                    lo, hi = struct.unpack_from("<II", body, pos); pos += 8
                    (val,) = struct.unpack("<d", struct.pack("<II", hi, lo))
                elif kind == 7:
                    (val,) = struct.unpack_from("<i", body, pos); pos += 4
                elif kind == 8:
                    val = self._const(body[pos]); pos += 1
                elif kind == 9:
                    (idx,) = struct.unpack_from("<H", body, pos); pos += 2
                    val = self._const(idx)
                else:
                    return
                stack.append(val)

        elif op == POP:
            stack.pop()

        elif op == GET_VARIABLE:
            name = _key(stack.pop())
            stack.append(self.globals.setdefault(name, {}))

        elif op == SET_VARIABLE:
            value = stack.pop()
            self.globals[_key(stack.pop())] = value

        elif op == DEFINE_LOCAL:
            value = stack.pop()
            self.globals[_key(stack.pop())] = value

        elif op == GET_MEMBER:
            name = _key(stack.pop())
            obj = stack.pop()
            if isinstance(obj, dict):
                # setdefault : `I.u[x]=…` doit pouvoir créer `u` au vol tout en
                # renvoyant toujours le même objet aux tags suivants.
                stack.append(obj.setdefault(name, {}))
            else:
                stack.append({})

        elif op == SET_MEMBER:
            value = stack.pop()
            name = _key(stack.pop())
            obj = stack.pop()
            if isinstance(obj, dict):
                obj[name] = value

        elif op == INIT_OBJECT:
            count = int(stack.pop() or 0)
            obj = {}
            for _ in range(count):
                value = stack.pop()
                obj[_key(stack.pop())] = value
            stack.append(obj)

        elif op == INIT_ARRAY:
            count = int(stack.pop() or 0)
            stack.append([stack.pop() for _ in range(count)])

    def _const(self, index):
        return self.pool[index] if index < len(self.pool) else ""


def _key(value):
    """Les clés numériques deviennent des chaînes, comme dans Flash."""
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def load(raw):
    """SWF brut -> dictionnaire des variables globales."""
    machine = Machine()
    for code, data in iter_tags(decompress(raw)):
        if code == 12:                      # DoAction
            machine.run(data)
        elif code == 59:                    # DoInitAction
            machine.run(data[2:])
    return machine.globals
