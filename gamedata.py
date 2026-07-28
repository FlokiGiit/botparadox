"""
Données de jeu : noms et icônes.

Les paquets réseau ne transportent que des numéros. Deux sources locales
permettent de les rendre lisibles :

  noms    extraits une fois pour toutes des fichiers de langue du client,
          vers data/*.json (voir l'outil langdump)
  icônes  déjà présentes sur le disque, dans clips/items/<groupe>/<gfx>.png

La règle de regroupement des dossiers d'icônes n'est pas documentée, donc
plutôt que de la deviner on parcourt l'arborescence une fois et on retient
où chaque fichier se trouve réellement.
"""

import json
import os

# Chemin du client resolu au seul endroit qui l'auto-detecte (voir client_config).
from apppaths import DATA_DIR
from client_config import RETROCLIENT as CLIENT, ICON_ROOT


class GameData:
    def __init__(self):
        self.items = {}      # numéro de modèle -> {"name", "gfx", "type"}
        self.monsters = {}
        self.interactives = {}
        self.jobs = {}
        self.icons = {}      # numéro de gfx -> chemin du png
        self._uid_to_model = {}   # exemplaire -> modèle, appris via OAK/ASK

    # ── chargement ───────────────────────────────────────────────────────────

    def load(self):
        self._load_json("items", self.items)
        self._load_json("monsters", self.monsters)
        self._load_json("interactiveobjects", self.interactives)
        self._load_json("jobs", self.jobs)
        self._index_icons()
        return self

    def resource_name(self, gfx):
        """Nom d'une ressource récoltable d'après son numéro graphique."""
        entry = self.interactives.get(f"gfx:{gfx}") or self.interactives.get(str(gfx))
        return entry["name"] if entry else None

    def _load_json(self, name, target):
        path = os.path.join(DATA_DIR, f"{name}.json")
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                target.update(json.load(f))
        except (OSError, ValueError) as e:
            print(f"[gamedata] {name}.json illisible : {e}")

    def _index_icons(self):
        """Indexe par "<type>/<gfx>" : le nom de fichier seul est ambigu, un
        même 9.png existe dans 67 dossiers différents."""
        if not os.path.isdir(ICON_ROOT):
            return
        for group in os.listdir(ICON_ROOT):
            folder = os.path.join(ICON_ROOT, group)
            if not os.path.isdir(folder):
                continue
            for fname in os.listdir(folder):
                if fname.endswith(".png"):
                    self.icons[f"{group}/{fname[:-4]}"] = os.path.join(folder, fname)

    def icon_key(self, entry):
        """Clé d'icône d'une entrée d'objet, ou None si introuvable."""
        if not entry:
            return None
        key = f"{entry.get('typeId')}/{entry.get('gfx')}"
        return key if key in self.icons else None

    # ── correspondance exemplaire -> modèle ──────────────────────────────────

    def parse_bag(self, payload):
        """Quantite de chaque modele dans le SAC (position vide).

        Les objets equipes ont une position renseignee (4e champ) ; on les
        exclut car ils sont deja utilises, pas disponibles pour une fusion.
        """
        bag = {}
        for entry in payload.split(";"):
            bits = entry.split("~")
            if len(bits) < 4:
                continue
            position = bits[3]
            if position:            # non vide = equipe
                continue
            try:
                model = str(int(bits[1], 16))
                qty = int(bits[2], 16) if bits[2] else 1
            except ValueError:
                continue
            bag[model] = bag.get(model, 0) + qty
        return bag

    def parse_equipped(self, payload):
        """Quantite de chaque modele EQUIPE (position renseignee)."""
        eq = {}
        for entry in payload.split(";"):
            bits = entry.split("~")
            if len(bits) < 4 or not bits[3]:
                continue
            try:
                model = str(int(bits[1], 16))
                qty = int(bits[2], 16) if bits[2] else 1
            except ValueError:
                continue
            eq[model] = eq.get(model, 0) + qty
        return eq

    def parse_bag_uids(self, payload):
        """Sac detaille : {modele: {uid: quantite}}. Necessaire pour crafter,
        car le serveur veut le guid (uid) exact de chaque ingredient."""
        bag = {}
        for entry in payload.split(";"):
            bits = entry.split("~")
            if len(bits) < 4 or bits[3]:   # position renseignee = equipe
                continue
            try:
                uid = str(int(bits[0].lstrip("O"), 16))
                model = str(int(bits[1], 16))
                qty = int(bits[2], 16) if bits[2] else 1
            except ValueError:
                continue
            bag.setdefault(model, {})[uid] = qty
        return bag

    def parse_bank(self, payload):
        """Contenu de la BANQUE (echange ECK5, paquet ELO).

        Meme format par objet que le sac (guid~modele~quantite~position~effets,
        en hexa), mais objets separes par ';;', chacun prefixe 'O', avec un
        'G<kamas>' final. Renvoie {modele: quantite totale}."""
        bank = {}
        for seg in payload.split(";;"):
            seg = seg.lstrip("O")
            if not seg or seg[0] == "G":     # G<kamas> : pas un objet
                continue
            bits = seg.split("~")
            if len(bits) < 3:
                continue
            try:
                model = str(int(bits[1], 16))
                qty = int(bits[2], 16) if bits[2] else 1
            except ValueError:
                continue
            bank[model] = bank.get(model, 0) + qty
        return bank

    def learn_objects(self, payload):
        """Lit une liste d'objets telle qu'envoyée par OAK ou ASK.

        Format d'une entrée : <uid hex>~<modèle hex>~<quantité>~...
        Le réseau désigne ensuite les objets par leur uid seul, d'où la
        nécessité de mémoriser cette correspondance au passage.
        """
        found = []
        for entry in payload.split(";"):
            bits = entry.split("~")
            if len(bits) < 2:
                continue
            try:
                uid = int(bits[0].lstrip("O"), 16)
                model = int(bits[1], 16)
                qty = int(bits[2], 16) if len(bits) > 2 and bits[2] else 0
            except ValueError:
                continue
            self._uid_to_model[str(uid)] = str(model)
            found.append((str(uid), str(model), qty))
        return found

    def model_of(self, uid):
        return self._uid_to_model.get(str(uid))

    # ── restitution ──────────────────────────────────────────────────────────

    def item_name(self, uid_or_model):
        model = self.model_of(uid_or_model) or str(uid_or_model)
        entry = self.items.get(model)
        return entry["name"] if entry else None

    def item_icon(self, uid_or_model):
        model = self.model_of(uid_or_model) or str(uid_or_model)
        entry = self.items.get(model)
        if not entry:
            return None
        return self.icons.get(str(entry.get("gfx")))

    def describe(self, uid):
        """Nom si connu, sinon le numéro — le tableau reste lisible dans tous
        les cas, y compris avant extraction des fichiers de langue."""
        model = self.model_of(uid)
        name = self.item_name(uid)
        if name:
            return name
        return f"objet {model or uid}"


_data = None


def get():
    global _data
    if _data is None:
        _data = GameData().load()
    return _data
