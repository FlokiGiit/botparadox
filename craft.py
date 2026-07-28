"""
Resolveur d'arbre de fusion.

A partir d'items cibles (ex. Bouclier du Paradoxe I/II/III), decompose
recursivement chaque recette jusqu'aux items farmables (les feuilles : ceux qui
n'ont pas de recette de fusion), et agrege les quantites.

Les recettes viennent de l'API du panel fusion (data/fusion_recipes.json),
toutes confondues : la decomposition traverse plusieurs metiers (un bouclier
est fait de Paradoxes, eux-memes faits de Dofus).
"""

import json
import os

from apppaths import DATA_DIR as DATA


def _fix(s):
    """Filet contre le double-encodage UTF-8 des noms extraits du panel
    (ex. 'OtomaÃ¯' -> 'Otomaï'). Les data livrees sont deja propres ; ceci
    protege si une future extraction ré-introduit le bug."""
    if not isinstance(s, str) or ("Ã" not in s and "Â" not in s):
        return s
    for enc in ("cp1252", "latin-1"):
        try:
            return s.encode(enc).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return s


class CraftBook:
    def __init__(self):
        self.by_result = {}   # templateId -> liste d'ingredients
        self.name = {}        # templateId -> nom
        self.gfx = {}         # templateId -> gfxId
        self.type = {}        # templateId -> typeName (Bouclier, Familier...)
        self.recipe_id = {}   # templateId resultat -> id de recette (selectedRecipeId)
        self._load()

    def _load(self):
        recipes = self._read("fusion_recipes.json", [])
        for r in recipes:
            res = r["result"]
            tid = res["templateId"]
            self.by_result[tid] = r["ingredients"]
            self.name[tid] = _fix(res["name"])
            self.gfx[tid] = res.get("gfxId")
            self.type[tid] = _fix(res.get("typeName", ""))
            self.recipe_id[tid] = r["id"]
            for ing in r["ingredients"]:
                self.name.setdefault(ing["templateId"], _fix(ing["name"]))
                self.gfx.setdefault(ing["templateId"], ing.get("gfxId"))
        # items.json complete les noms/gfx des feuilles (Dofus de base).
        items = self._read("items.json", {})
        for k, v in items.items():
            try:
                tid = int(k)
            except ValueError:
                continue
            self.name.setdefault(tid, v["name"])
            self.gfx.setdefault(tid, v.get("gfx"))

    def _read(self, name, default):
        path = os.path.join(DATA, name)
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return default

    # ── requetes ──────────────────────────────────────────────────────────────

    def craftable(self, tid):
        return tid in self.by_result

    def search(self, query, limit=25):
        """Items craftables dont le nom contient la requete."""
        q = query.lower().strip()
        if not q:
            return []
        out = []
        for tid, ing in self.by_result.items():
            n = self.name.get(tid, "")
            if q in n.lower():
                out.append({"id": tid, "name": n, "gfx": self._gfxkey(tid)})
        out.sort(key=lambda r: (len(r["name"]), r["name"]))
        return out[:limit]

    def tree(self, targets):
        """Arbre de fusion aplati en liste, chaque noeud avec sa profondeur.

        Montre les intermediaires craftables (ex. un Paradoxe) et, indentes en
        dessous, leurs sous-composants. Les feuilles (sans recette) sont ce
        qu'il reste a farmer.
        """
        out = []

        def walk(tid, qty, depth):
            ing = self.by_result.get(tid)
            out.append({"id": tid, "need": qty,
                        "craftable": bool(ing), "depth": depth})
            if ing and depth < 15:
                for i in ing:
                    walk(i["templateId"], qty * i["quantity"], depth + 1)

        for tid, qty in targets.items():
            walk(tid, qty, 0)
        return out

    def _gfxkey(self, tid):
        """Clé d'icône '<type>/<gfx>' si connue via items.json, sinon le gfx
        brut de l'API (pour lequel on n'a pas le dossier)."""
        return str(self.gfx.get(tid, ""))


_book = None


def book():
    global _book
    if _book is None:
        _book = CraftBook()
    return _book
