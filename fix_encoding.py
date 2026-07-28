"""
Repare le double-encodage UTF-8 des noms dans les data extraites du panel.

Symptome : "d'OtomaÃ¯" au lieu de "d'Otomaï", "SpÃ©cial" au lieu de "Special".
Cause : les octets UTF-8 (ex. ï = C3 AF) ont ete re-encodes une 2e fois en UTF-8
(-> C3 83 C2 AF). On inverse : encode('latin-1') puis decode('utf-8').

On ne touche qu'aux chaines suspectes (contenant Ã ou Â) et seulement si la
re-decodage reussit — les chaines deja correctes restent intactes.
"""

import json
import os
import sys

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
TARGETS = ["fusion_recipes.json", "fusionneur_dofus.json"]


def demojibake(s):
    # cp1252 d'abord (couvre ‰, Œ, etc. des majuscules accentuees : Ã‰ = É),
    # latin-1 en secours. On ne touche qu'aux chaines suspectes.
    if not isinstance(s, str) or ("Ã" not in s and "Â" not in s):
        return s
    for enc in ("cp1252", "latin-1"):
        try:
            return s.encode(enc).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return s


def walk(obj):
    if isinstance(obj, str):
        return demojibake(obj)
    if isinstance(obj, list):
        return [walk(x) for x in obj]
    if isinstance(obj, dict):
        return {k: walk(v) for k, v in obj.items()}
    return obj


def repair(name):
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        print(f"  {name} : absent, ignore")
        return
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    fixed = walk(data)
    if fixed == data:
        print(f"  {name} : rien a corriger")
        return
    backup = path + ".mojibake.bak"
    if not os.path.exists(backup):
        os.replace(path, backup)
    else:
        os.remove(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(fixed, f, ensure_ascii=False)
    print(f"  {name} : corrige (sauvegarde {os.path.basename(backup)})")


if __name__ == "__main__":
    print("Reparation de l'encodage :")
    for t in (sys.argv[1:] or TARGETS):
        repair(t)
