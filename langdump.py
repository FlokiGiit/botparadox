"""
Extrait les noms du jeu depuis les fichiers de langue du client, vers data/.

À lancer une fois (ou après une mise à jour du serveur) :  python langdump.py

Les fichiers sont téléchargés puis mis en cache dans data/swf/. Les versions
proviennent du cache HTTP du client ; si le serveur en publie de nouvelles,
il suffit de mettre à jour les numéros ci-dessous.
"""

import json
import os
import urllib.request

import avm1

BASE = "https://data.nexus-temporel.com/dofus/lang/swf/"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
SWF_CACHE = os.path.join(DATA, "swf")

SOURCES = {
    "items": "items_fr_1434.swf",
    "monsters": "monsters_fr_1262.swf",
    "interactiveobjects": "interactiveobjects_fr_1256.swf",
    "jobs": "jobs_fr_1255.swf",
    "maps": "maps_fr_1304.swf",
}


def fetch(filename):
    os.makedirs(SWF_CACHE, exist_ok=True)
    path = os.path.join(SWF_CACHE, filename)
    if not os.path.exists(path):
        print(f"  téléchargement {filename}")
        req = urllib.request.Request(BASE + filename,
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        with open(path, "wb") as f:
            f.write(data)
        return data
    with open(path, "rb") as f:
        return f.read()


def first_table(globals_, *candidates):
    """Renvoie la première table non vide parmi les racines proposées."""
    for name in candidates:
        table = globals_.get(name)
        if isinstance(table, dict) and len(table) > 5:
            return table
    return {}


def normalise_items(g):
    root = g.get("I", {})
    types = {k: v.get("n") for k, v in root.get("t", {}).items()
             if isinstance(v, dict)}
    out = {}
    for item_id, entry in root.get("u", {}).items():
        if not isinstance(entry, dict) or "n" not in entry:
            continue
        out[item_id] = {
            "name": entry.get("n"),
            # Le chemin d'une icône est items/<type>/<gfx>.png : le seul gfx ne
            # suffit pas, 67 dossiers contiennent par exemple un 9.png.
            "gfx": str(_as_key(entry.get("g", ""))),
            "typeId": str(_as_key(entry.get("t", ""))),
            "type": types.get(str(_as_key(entry.get("t"))), ""),
            "level": entry.get("l"),
            "weight": entry.get("w"),
            "desc": entry.get("d", ""),
        }
    return out


def normalise_interactives(g):
    """Objets interactifs : les définitions sont dans IO.d, et IO.g fait le
    lien depuis le numéro graphique posé sur la carte."""
    root = g.get("IO", {})
    out = {}
    for io_id, entry in root.get("d", {}).items():
        if isinstance(entry, dict) and entry.get("n"):
            out[io_id] = {"name": entry["n"], "gfx": ""}
    # On indexe aussi par numéro graphique : c'est cette clé-là que porte la
    # carte, l'identifiant de définition n'y apparaît jamais.
    for gfx, io_id in root.get("g", {}).items():
        target = out.get(str(_as_key(io_id)))
        if target:
            out[f"gfx:{gfx}"] = dict(target)
    return out


def _as_key(value):
    if isinstance(value, float) and value == int(value):
        return int(value)
    return value


def normalise_named(g, *roots):
    """Table générique <id> -> {"name": ...} pour monstres, objets, métiers."""
    table = first_table(g, *roots)
    out = {}
    for key, entry in table.items():
        if isinstance(entry, dict):
            name = entry.get("n") or entry.get("name")
            if name:
                out[key] = {"name": name,
                            "gfx": str(entry.get("g", ""))}
        elif isinstance(entry, str):
            out[key] = {"name": entry, "gfx": ""}
    return out


def main():
    os.makedirs(DATA, exist_ok=True)
    for name, filename in SOURCES.items():
        try:
            g = avm1.load(fetch(filename))
        except Exception as e:
            print(f"  {name:20} ÉCHEC : {e}")
            continue

        if name == "items":
            data = normalise_items(g)
        elif name == "interactiveobjects":
            data = normalise_interactives(g)
        else:
            data = normalise_named(g, "M", "J", "MA")

        path = os.path.join(DATA, f"{name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        print(f"  {name:20} {len(data):6} entrées")


if __name__ == "__main__":
    print("extraction des noms du jeu\n")
    main()
    print("\nterminé — data/*.json")
