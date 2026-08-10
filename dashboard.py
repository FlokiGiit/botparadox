"""
Tableau de bord local du bot.

Sert une page sur http://127.0.0.1:8765 qui montre en direct les compteurs de
la session : fauchages, combats, objets ramassés, pods, cadence.

Tout est déduit du flux de jeu, rien n'est saisi à la main. Les objets sont
identifiés par leur numéro : le serveur ne transmet jamais leur nom dans les
paquets d'inventaire. Celui qui monte à chaque fauche est forcément le blé,
donc on le repère tout seul ; tout autre numéro qui apparaît est un butin
remarquable, et c'est là qu'un Dofus Paysan se signalera.
"""

import asyncio
import json
import os
import time
from collections import deque

import combat
import craft
import gamedata
from overlay_page import OVERLAY_PAGE
from assist_page import ASSIST_PAGE
import losrange
from boss_ids import DUNGEON_BOSS_IDS

HOST = "127.0.0.1"
PORT = 8765
MAX_EVENTS = 60

# Loot rare a mettre en avant. Les types viennent de items.json, verifies :
# 241 Dofus/fragments, 212 reliques. Les energies/modularite n'ont pas de type
# distinct (ce sont des "Ressource"), donc on les cible par identifiant.
RARE_TYPES = {"Dofus", "Fragment de Dofus", "Relique"}
RARE_IDS = {
    "74059": "Energie rare", "925393": "Energie rare liee",
    "74061": "Rune d'amelioration temporelle",
    "925392": "Rune d'amelioration temporelle liee",
    "101377": "Modularite", "101378": "Modularite intemporelle",
    "101379": "Modularite frigost",
}


def json_or(d):
    return dict(d)


def _rare_category(model, entry):
    """Categorie d'un objet rare, ou None si banal."""
    if str(model) in RARE_IDS:
        return "energie"
    t = (entry or {}).get("type", "")
    if t in ("Dofus", "Fragment de Dofus"):
        return "dofus"
    if t == "Relique":
        return "relique"
    return None

# Le mode et les totaux survivent aux redemarrages : sans ca il fallait
# re-selectionner Farming a chaque fois et les cumuls repartaient de zero.
from apppaths import data as _data
STATE_FILE = _data("session.json")


BRIDGE = "http://127.0.0.1:8790/panel"


def _post_bridge(payload):
    """Envoie une action de panneau au client via le pont (le client la signe
    et l'emet lui-meme). Bloquant : a lancer dans un thread."""
    import urllib.request
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        BRIDGE, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"success": False, "error": f"pont injoignable : {e}"}


_GEO_FILE = _data("overlay.json")


def _load_overlay_geo():
    """Position/taille de l'overlay, persistees cote bot : le client du jeu
    efface son localStorage a chaque lancement, d'ou le reset sinon."""
    try:
        with open(_GEO_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_overlay_geo(geo):
    try:
        with open(_GEO_FILE, "w", encoding="utf-8") as f:
            json.dump(geo, f)
    except OSError:
        pass


_HARVEST_JOBS = None


def harvest_jobs_list():
    """Métiers de récolte disponibles (dérivés de harvest_gfx.json)."""
    global _HARVEST_JOBS
    if _HARVEST_JOBS is None:
        try:
            with open(_data("harvest_gfx.json"), encoding="utf-8") as f:
                _HARVEST_JOBS = sorted({v["job"] for v in json.load(f).values()})
        except (OSError, ValueError):
            _HARVEST_JOBS = []
    return _HARVEST_JOBS


def combat_spell_rows():
    """Sorts connus, pour l'onglet Farming.

    Source : le catalogue en cache sur disque, complété du niveau réellement
    appris quand le jeu est connecté. Passer par le cache permet de régler ses
    sorts jeu fermé — sinon il faudrait être en combat pour voir la liste.
    """
    ai = getattr(_brain, "combat", None) if _brain else None
    levels = dict(getattr(ai, "levels", None) or {})
    catalog = dict(getattr(ai, "catalog", None) or {}) or combat.load_catalog()
    # Dès que le serveur a annoncé les sorts du personnage connecté (SL), on s'y
    # tient : le catalogue sur disque peut contenir ceux d'une autre classe,
    # jouée sur cette machine avant. Sans ce filtre l'onglet Farming proposait
    # des sorts que le personnage ne possède pas.
    known = {sid for sid, _ in catalog}
    rows = []
    for spell_id in (set(levels) & known) if levels else known:
        level = levels.get(spell_id, 1)
        sp = catalog.get((spell_id, level)) or catalog.get((spell_id, 1))
        if sp is None:
            continue
        rows.append({
            "id": sp.id, "name": sp.name, "pa": sp.pa,
            "rmin": sp.range_min, "rmax": sp.range_max,
            "zone": sp.zone_radius, "max": sp.max_per_turn,
            "offensive": sp.offensive,
            # "appris" = annoncé par le serveur (SL). Un sort non appris reste
            # proposé, mais l'IA l'ignorera : autant le signaler.
            "learned": spell_id in levels,
            "level": levels.get(spell_id),
        })
    rows.sort(key=lambda r: (not r["offensive"], r["name"]))
    return rows


def combat_state():
    """Réglages de combat + liste des sorts, pour l'onglet Farming."""
    ai = getattr(_brain, "combat", None) if _brain else None
    return {
        "spells": combat_spell_rows(),
        # Faux tant que le serveur n'a pas annoncé les sorts du personnage : la
        # liste vient alors du cache disque et peut concerner une autre classe.
        "known": bool(getattr(ai, "levels", None)),
        "selected": list(_stats.combat_spells),
        "buffs": list(_stats.combat_buffs),
        "move": _stats.combat_move,
        "delay": _stats.combat_delay,
        "engage_max_steps": _stats.engage_max_steps,
        "capture_souls": _stats.capture_souls,
        "auto_maitrise": _stats.auto_maitrise,
        "auto_tir": _stats.auto_tir,
        "auto_coffre": _stats.auto_coffre,
        "fight_scripts": list(_stats.fight_scripts),
        "script_korriandre": "korriandre" in _stats.fight_scripts,
    }


def _reorder(ids, spell_id, direction):
    """Déplace un sort d'un cran dans la liste de priorité."""
    if spell_id not in ids:
        return ids
    i = ids.index(spell_id)
    j = i + (1 if direction == "down" else -1)
    if 0 <= j < len(ids):
        ids[i], ids[j] = ids[j], ids[i]
    return ids


def combat_command(parts):
    """Applique une commande /combat/... et renvoie l'état à jour.

    parts : segments de l'URL après "combat" (ex. ["toggle", "179"]).
    """
    action = parts[0] if parts else "state"
    if action == "toggle":
        spell_id = int(parts[1])
        if spell_id in _stats.combat_spells:
            _stats.combat_spells.remove(spell_id)
        else:
            _stats.combat_spells.append(spell_id)
        _stats.persist()
    elif action == "buff":
        spell_id = int(parts[1])
        if spell_id in _stats.combat_buffs:
            _stats.combat_buffs.remove(spell_id)
        else:
            _stats.combat_buffs.append(spell_id)
        _stats.persist()
    elif action == "order":
        _reorder(_stats.combat_spells, int(parts[1]), parts[2])
        _stats.persist()
    elif action == "clear":
        _stats.combat_spells = []
        _stats.combat_buffs = []
        _stats.persist()
    elif action == "move":
        _stats.combat_move = parts[1] == "1"
        _stats.persist()
    elif action == "delay":
        _stats.combat_delay = max(0.1, min(2.0, float(parts[1]) / 100))
        _stats.persist()
    elif action == "steps":
        _stats.engage_max_steps = max(1, min(60, int(parts[1])))
        _stats.persist()
    return combat_state()


def build_fuse(stats, template_id):
    """Construit l'action de craft pour un item : retrouve un guid par
    ingredient dans le sac. Renvoie (payload, erreur)."""
    b = craft.book()
    recipe_id = b.recipe_id.get(template_id)
    if not recipe_id:
        return None, "pas une recette de fusion"
    slots = []
    for ing in b.by_result.get(template_id, []):
        model = str(ing["templateId"])
        need = ing["quantity"]
        remaining = need
        for uid, qty in stats.bag_uids.get(model, {}).items():
            if remaining <= 0:
                break
            take = min(qty, remaining)
            slots.append({"guid": int(uid), "quantity": take})
            remaining -= take
        if remaining > 0:
            return None, f"manque {b.name.get(ing['templateId'], model)}"
    return {"panelId": "fusion", "action": "craft",
            "params": {"slots": slots, "selectedRecipeId": recipe_id}}, None


def _headers(ctype, length):
    """En-tetes HTTP communs a toutes les reponses du tableau de bord."""
    return ("HTTP/1.1 200 OK\r\n"
            f"Content-Type: {ctype}\r\n"
            f"Content-Length: {length}\r\n"
            "Cache-Control: no-store\r\n"
            "Connection: close\r\n\r\n").encode()


class Stats:
    def __init__(self):
        # Mettre en pause plutôt qu'arrêter : le proxy porte la connexion de
        # jeu, le tuer déconnecte le joueur. L'état vit ici plutôt que dans le
        # Brain, qui est recréé à chaque reconnexion.
        self.enabled = True
        # "harvest" : récolte de ressources. "farm" : combats en boucle.
        # "off" : observateur pur (le perso ne joue pas, seul le
        # loot est compte pour l'overlay). "harvest"/"farm" jouent.
        self.mode = "off"
        self.total = {"harvests": 0, "kills": 0, "fights": 0,
                      "xp": 0, "kamas": 0, "prestige": 0}
        # Jetons de prestige gagnes cette session. Ils n'arrivent pas en OA/OQ
        # comme le loot ordinaire : ils ne figurent que dans le bilan de combat
        # (GE), qu'on parse pour les compter.
        self.prestige = 0
        # Le jeu n'est pas forcément connecté : le bot peut tourner en
        # attendant, ce qui est même l'ordre à respecter.
        self.client_connected = False
        self.started = time.time()
        self.harvests = 0
        self.fights = 0
        self.fights_won = 0
        self.pods = (0, 0)
        self.map_id = None
        self.items = {}          # numéro -> {"qty": int, "gained": int, "last": float}
        self.events = deque(maxlen=MAX_EVENTS)
        # Trace brute de ce que fait le bot, telle qu'ecrite dans le
        # journal de session. La console de l'exe est masquee, donc sans ca
        # ces lignes ne sont visibles que dans un fichier.
        self.logs = deque(maxlen=200)
        self._fight_started = None
        self.level = None
        self.xp = None           # (actuel, plancher du niveau, palier suivant)
        self.jobs = {}           # numéro de métier -> (niveau, plancher, xp, suivant)
        # Avant _restore : sinon la restauration des cibles serait ecrasee.
        self.craft_targets = {}   # templateId -> quantite voulue
        self.harvest_jobs = set()  # metiers a recolter (vide = tous)
        self.capture_souls = False  # lancer "Capture d'âmes" (sort 413) en combat
        # Prep auto (hors Farming) : lancés au tour 1 de chaque combat.
        self.auto_maitrise = False   # Maîtrise de l'Arc (180)
        self.auto_tir = False        # Tir Puissant (166)
        self.auto_coffre = False     # Coffre Animé de Joueur (6019)
        # Scripts boss (checkbox) : complètent le farming sans le remplacer.
        self.fight_scripts = []      # ex. ["korriandre"]
        # Reglages de combat de l'onglet Farming. combat_spells est ORDONNE :
        # c'est l'ordre de priorite que l'IA suit a la lettre. Vide = auto
        # (tous les sorts offensifs appris, les plus chers en PA d'abord).
        self.combat_spells = []      # ids de sorts offensifs, dans l'ordre
        self.combat_buffs = []       # ids de sorts lances sur soi au 1er tour
        self.combat_move = True      # se rapprocher si aucune cible a portee
        self.combat_delay = 0.3      # secondes entre deux actions
        self.engage_max_steps = 30   # trajet max pour aller agresser
        self._restore()
        self.xp_start = None     # XP au premier paquet, pour mesurer le gain
        self.kills = 0
        self.kamas = None
        self.kamas_start = None
        self._xp_at_fight = None
        self._kamas_at_fight = None
        self._pending_gains = None
        # Gains BRUTS de session : on additionne les hausses du total et on
        # ignore les baisses (taxes, achats). Le simple ecart total actuel -
        # total de depart sous-comptait des qu'on depensait quoi que ce soit.
        self._xp_prev = None
        self._xp_acc = 0
        self._kamas_prev = None
        self._kamas_acc = 0
        self.bag = {}             # modele -> quantite en sac (hors equipe)
        self.bag_uids = {}        # modele -> {uid: quantite} (pour crafter)
        self.equipped = {}        # modele -> quantite equipee
        # Banque : capturee a l'ouverture (ELO/ECK5) et MEMORISEE sur disque,
        # car le serveur ne l'envoie qu'a l'ouverture. On la garde donc entre
        # les sessions : ouvrir la banque une fois suffit, meme apres restart.
        self.bank = self._load_bank()
        self.level_start = None   # niveau au debut de la session
        self.session_levels = 0   # niveaux gagnes cette session
        self.xp_floor = None      # plancher du niveau courant, pour detecter un up

    # ── enregistrement ───────────────────────────────────────────────────────

    def _restore(self):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                saved = json.load(f)
            self.mode = saved.get("mode", self.mode)
            self.total.update(saved.get("total", {}))
            self.craft_targets = {int(k): v
                                  for k, v in saved.get("craft", {}).items()}
            self.harvest_jobs = set(saved.get("harvest_jobs", []))
            self.capture_souls = bool(saved.get("capture_souls", False))
            self.auto_maitrise = bool(saved.get("auto_maitrise", False))
            self.auto_tir = bool(saved.get("auto_tir", False))
            self.auto_coffre = bool(saved.get("auto_coffre", False))
            self.fight_scripts = [str(s) for s in saved.get("fight_scripts", [])]
            self.combat_spells = [int(i) for i in saved.get("combat_spells", [])]
            self.combat_buffs = [int(i) for i in saved.get("combat_buffs", [])]
            self.combat_move = bool(saved.get("combat_move", True))
            self.combat_delay = float(saved.get("combat_delay", 0.3))
            self.engage_max_steps = int(saved.get("engage_max_steps", 30))
        except (OSError, ValueError, TypeError):
            pass

    def persist(self):
        """Ecrit le mode et les cumuls. Appele aux moments qui comptent,
        pas a chaque paquet : un fichier par seconde suffit largement."""
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            total = dict(self.total)
            total["harvests"] = self.total["harvests"] + self.harvests
            total["kills"] = self.total["kills"] + self.kills
            total["fights"] = self.total["fights"] + self.fights
            total["xp"] = self.total["xp"] + self._xp_gained()
            total["kamas"] = self.total["kamas"] + self._kamas_gained()
            total["prestige"] = self.total.get("prestige", 0) + self.prestige
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"mode": self.mode, "total": total,
                           "craft": {str(k): v
                                     for k, v in self.craft_targets.items()},
                           "harvest_jobs": sorted(self.harvest_jobs),
                           "capture_souls": self.capture_souls,
                           "auto_maitrise": self.auto_maitrise,
                           "auto_tir": self.auto_tir,
                           "auto_coffre": self.auto_coffre,
                           "fight_scripts": list(self.fight_scripts),
                           "combat_spells": self.combat_spells,
                           "combat_buffs": self.combat_buffs,
                           "combat_move": self.combat_move,
                           "combat_delay": self.combat_delay,
                           "engage_max_steps": self.engage_max_steps}, f)
        except OSError:
            pass

    def event(self, kind, text, **extra):
        # Le texte des butins n'est pas figé ici : le nom de l'objet n'est
        # parfois connu qu'après coup, une fois la correspondance
        # exemplaire -> modèle reçue. On garde l'identifiant et on résout à
        # l'affichage.
        self.events.appendleft({"t": time.time(), "kind": kind,
                                "text": text, **extra})

    def add_prestige(self, n):
        """Comptabilise n jetons de prestige gagnes (depuis le bilan de combat)."""
        if n <= 0:
            return
        self.prestige += n
        self.event("drop", f"+{n} Jeton de prestige", gfx="")
        self.persist()

    def craft_add(self, tid):
        self.craft_targets[tid] = self.craft_targets.get(tid, 0) + 1
        self.persist()

    def craft_set(self, tid, qty):
        if qty <= 0:
            self.craft_targets.pop(tid, None)
        else:
            self.craft_targets[tid] = qty
        self.persist()

    def craft_clear(self):
        self.craft_targets = {}
        self.persist()

    def craft_snapshot(self):
        """Cibles choisies + arbre de fabrication, avec ce qu'on possede
        deja (sac, plus l'equipe pour Bouclier/Familier)."""
        b = craft.book()
        gd = gamedata.get()
        def icon(tid):
            return gd.icon_key(gd.items.get(str(tid))) or ""
        # L'equipe ne compte que pour les Bouclier et Familier : un tel item
        # deja porte est acquis. Les Dofus equipes (dans le Dofus) restent
        # exclus, car utilises.
        def bank_of(tid):
            return self.bank.get(str(tid), 0)

        def have(tid):
            h = self.bag.get(str(tid), 0) + bank_of(tid)
            if b.type.get(tid) in ("Bouclier", "Familier"):
                h += self.equipped.get(str(tid), 0)
            return h
        targets = [{"id": t, "name": b.name.get(t, f"#{t}"),
                    "gfx": icon(t), "qty": q}
                   for t, q in self.craft_targets.items()]
        # A-t-on en sac tous les ingredients DIRECTS pour fusionner ce noeud ?
        def can_fuse(tid):
            ing = b.by_result.get(tid)
            if not ing:
                return False
            return all(self.bag.get(str(i["templateId"]), 0) >= i["quantity"]
                       for i in ing)

        # Arbre hierarchique : intermediaires + sous-composants indentes.
        tree = []
        for node in b.tree(self.craft_targets):
            tree.append({
                "id": node["id"],
                "name": b.name.get(node["id"], f"#{node['id']}"),
                "gfx": icon(node["id"]),
                "need": node["need"],
                "have": have(node["id"]),
                "bank": bank_of(node["id"]),   # dont X en banque (indicateur)
                "depth": node["depth"],
                "craftable": node["craftable"],
                "canfuse": can_fuse(node["id"]),
            })
        return {"targets": targets, "tree": tree}

    def _load_bank(self):
        try:
            with open(_data("bank.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def set_bank(self, bank):
        """Mémorise la banque capturée et la persiste (elle survit au restart)."""
        self.bank = bank
        try:
            with open(_data("bank.json"), "w", encoding="utf-8") as f:
                json.dump(bank, f)
        except OSError:
            pass

    def observe_xp(self, total):
        """Accumule le gain d'XP. Le premier total sert de reference sans
        compter (sinon toute l'XP deja acquise serait vue comme gagnee)."""
        if self._xp_prev is not None and total > self._xp_prev:
            self._xp_acc += total - self._xp_prev
        self._xp_prev = total

    def observe_kamas(self, total):
        self.kamas = total
        if self._kamas_prev is not None and total > self._kamas_prev:
            self._kamas_acc += total - self._kamas_prev
        self._kamas_prev = total

    def reset_session(self):
        """Remet les compteurs de session a zero (nouveau bilan).

        Les cumuls totaux sont d'abord verses via persist(), puis on repart de
        zero. Appele a chaque reconnexion : une session = une partie de jeu.
        """
        self.persist()
        self.total = json_or(self.total)   # garde les totaux a jour
        self.started = time.time()
        self.xp_start = None
        self.kamas_start = None
        self._xp_prev = None
        self._xp_acc = 0
        self._kamas_prev = None
        self._kamas_acc = 0
        self.level_start = None
        self.session_levels = 0
        self.xp_floor = None
        self.kills = 0
        self.harvests = 0
        self.fights = 0
        self.fights_won = 0
        self.items = {}

    def log_line(self, text):
        self.logs.appendleft({"t": time.time(), "text": text})

    def harvest_done(self, cell):
        self.harvests += 1
        self.event("harvest", f"Fauché cellule {cell}")

    def fight_start(self):
        self.fights += 1
        self._fight_started = time.time()
        # Photo des totaux : le gain du combat se mesure par différence,
        # comme le reste. Le serveur annonce bien des montants dans ses
        # messages, mais les lire supposerait de parser du texte traduit.
        self._xp_at_fight = self._xp_acc
        self._kamas_at_fight = self._kamas_acc
        self.event("fight", "Combat engagé")

    def fight_end(self):
        if self._fight_started:
            secs = time.time() - self._fight_started
            self.fights_won += 1
            self.event("fight", f"Combat terminé en {secs:.0f} s")
            self._fight_started = None
        # Les totaux arrivent juste après la fin du combat : on laisse le
        # temps aux paquets d'arriver avant de calculer l'écart.
        self._pending_gains = time.time()
        self.persist()

    def seed(self, item_id, qty):
        """Enregistre une quantité de départ sans la compter comme gagnée.

        Appelé sur l'inventaire reçu à la connexion : sans ça, le premier OQ
        d'un objet servirait de référence et son gain serait perdu.
        """
        self.items.setdefault(item_id,
                              {"qty": qty, "gained": 0, "last": time.time()})

    def item_added(self, item_id, qty):
        """Objet ajouté à l'inventaire (OAK).

        Un objet dont on ne possédait aucun exemplaire n'arrive jamais par OQ :
        le serveur crée un nouveau lot et l'annonce ici. Ne guetter que OQ
        faisait manquer tous les butins d'un type inédit.
        """
        if qty <= 0:
            return
        prev = self.items.get(item_id)
        if prev is None:
            self.items[item_id] = {"qty": qty, "gained": qty,
                                   "last": time.time()}
        else:
            prev["qty"] += qty
            prev["gained"] += qty
            prev["last"] = time.time()
        self.event("drop", "", item=item_id, delta=qty)

    def settle_fight_gains(self):
        """Publie le gain du combat une fois les totaux à jour. Ne touche PAS
        aux accumulateurs de session — un reset ici les remettait à zéro à
        chaque paquet As."""
        if self._pending_gains is None:
            return
        self._pending_gains = None
        if self._xp_at_fight is not None:
            gain = self._xp_acc - self._xp_at_fight
            if gain > 0:
                self.event("xp", f"+{gain:,} XP".replace(",", " "))
        if self._kamas_at_fight is not None:
            gain = self._kamas_acc - self._kamas_at_fight
            if gain > 0:
                self.event("kamas", f"+{gain:,} kamas".replace(",", " "))
        self._xp_at_fight = None
        self._kamas_at_fight = None

    def job_levelup(self, job_id, level):
        # Le nom du métier est résolu à l'affichage, comme pour les objets.
        self.event("levelup", "", job=job_id, level=level)

    def item_update(self, item_id, qty):
        """OQ donne le total du lot, pas le gain : on en déduit l'écart."""
        prev = self.items.get(item_id)
        if prev is None:
            # Premier passage : on note le total sans compter de gain, sinon
            # tout l'inventaire déjà possédé serait compté comme ramassé.
            self.items[item_id] = {"qty": qty, "gained": 0, "last": time.time()}
            return
        delta = qty - prev["qty"]
        prev["qty"] = qty
        prev["last"] = time.time()
        if delta > 0:
            prev["gained"] += delta
            if delta <= 5:
                # Petit gain isolé : plus probablement un butin qu'une récolte.
                self.event("drop", "", item=item_id, delta=delta)
        elif qty <= 0:
            # Lot vide : plus besoin de retenir l'entrée.
            self.items.pop(item_id, None)

    def item_remove(self, item_id):
        """OR : objet retiré — on oublie l'UID pour ne pas faire grossir items."""
        self.items.pop(str(item_id), None)

    # ── restitution ──────────────────────────────────────────────────────────

    def snapshot(self):
        uptime = max(1.0, time.time() - self.started)
        cur, mx = self.pods
        gd = gamedata.get()
        items = sorted(self.items.items(), key=lambda kv: -kv[1]["gained"])
        rows = []
        for uid, v in items:
            if v["gained"] <= 0:
                continue
            model = gd.model_of(uid) or uid
            entry = gd.items.get(str(model))
            rows.append({
                "id": model,
                "name": entry["name"] if entry else f"objet {model}",
                "gfx": gd.icon_key(entry) or "",
                "qty": v["qty"],
                "gained": v["gained"],
            })
        return {
            "uptime": uptime,
            "harvests": self.harvests,
            "harvests_per_hour": self.harvests / uptime * 3600,
            "fights": self.fights,
            "fights_won": self.fights_won,
            "pods": cur,
            "pods_max": mx,
            "pods_pct": (cur / mx * 100) if mx else 0,
            "map_id": self.map_id,
            "items": rows[:12],
            "rare": self._rare_rows(gd),
            "craft": self.craft_snapshot(),
            "enabled": self.enabled,
            "mode": self.mode,
            "capture_souls": self.capture_souls,
            "auto_maitrise": self.auto_maitrise,
            "auto_tir": self.auto_tir,
            "auto_coffre": self.auto_coffre,
            "fight_scripts": list(self.fight_scripts),
            "script_korriandre": "korriandre" in self.fight_scripts,
            "kills": self.kills,
            "total_harvests": self.total["harvests"] + self.harvests,
            "total_kills": self.total["kills"] + self.kills,
            "total_fights": self.total["fights"] + self.fights,
            "total_xp": self.total["xp"] + self._xp_gained(),
            "total_kamas": self.total["kamas"] + self._kamas_gained(),
            "kills_per_hour": self.kills / uptime * 3600,
            "xp_gained": self._xp_gained(),
            "kamas": self.kamas or 0,
            "kamas_gained": self._kamas_gained(),
            "kamas_per_hour": self._kamas_gained() / uptime * 3600,
            "xp_per_hour": self._xp_gained() / uptime * 3600,
            "client": self.client_connected,
            "level": self.level,
            "session_levels": self.session_levels,
            "session_xp": self._xp_gained(),
            "session_kamas": self._kamas_gained(),
            "prestige": self.prestige,
            "total_prestige": self.total.get("prestige", 0) + self.prestige,
            "xp": self._xp_progress(),
            "jobs": self._job_rows(gd),
            "events": [self._render_event(e, gd) for e in self.events],
            "logs": list(self.logs),
        }

    def _rare_rows(self, gd):
        """Loot rare ramasse cette session, plus recent en premier."""
        out = []
        for uid, v in self.items.items():
            if v["gained"] <= 0:
                continue
            model = gd.model_of(uid) or uid
            entry = gd.items.get(str(model))
            cat = _rare_category(model, entry)
            if not cat:
                continue
            out.append({
                "name": entry["name"] if entry else f"objet {model}",
                "gfx": gd.icon_key(entry) or "",
                "gained": v["gained"],
                "cat": cat,
                "last": v["last"],
            })
        out.sort(key=lambda r: -r["last"])
        return out

    def _render_event(self, event, gd):
        """Complète les événements avec les noms, connus seulement à l'affichage."""
        if event["kind"] == "levelup":
            entry = gd.jobs.get(str(event["job"]))
            name = entry["name"] if entry else f"métier {event['job']}"
            return {"t": event["t"], "kind": "levelup",
                    "text": f"{name} niveau {event['level']} !", "gfx": ""}

        if event["kind"] != "drop" or "item" not in event:
            return event
        model = gd.model_of(event["item"]) or event["item"]
        entry = gd.items.get(str(model))
        return {
            "t": event["t"], "kind": "drop",
            "text": f"+{event['delta']} {entry['name'] if entry else f'objet {model}'}",
            "gfx": gd.icon_key(entry) or "",
        }

    def _kamas_gained(self):
        """Kamas engranges depuis le debut de la session.

        Comme pour l'XP, on mesure l'ecart sur le total renvoye par le
        serveur plutot que d'additionner les gains annonces : un paquet
        manque fausserait un cumul maison, pas une difference.
        """
        return self._kamas_acc

    def _xp_gained(self):
        """XP engrangée depuis le début de la session.

        Mesurée sur le total renvoyé par le serveur, jamais accumulée de notre
        côté : un paquet manqué fausserait un compteur maison, pas celui-ci.
        """
        return self._xp_acc

    def _xp_progress(self):
        if not self.xp:
            return None
        cur, floor, nxt = self.xp
        span = nxt - floor
        return {
            "current": cur, "floor": floor, "next": nxt,
            "pct": ((cur - floor) / span * 100) if span > 0 else 0,
            "remaining": max(0, nxt - cur),
        }

    def _job_rows(self, gd):
        rows = []
        for job_id, (level, floor, xp, nxt) in self.jobs.items():
            entry = gd.jobs.get(str(job_id))
            span = nxt - floor
            rows.append({
                "name": entry["name"] if entry else f"métier {job_id}",
                "level": level,
                "pct": ((xp - floor) / span * 100) if span > 0 else 0,
                "xp": xp, "next": nxt,
            })
        # Le métier le plus avancé en premier : c'est celui qu'on fait tourner.
        rows.sort(key=lambda r: (-r["level"], -r["pct"]))
        return rows[:4]


PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bot Paradox</title><style>
*{box-sizing:border-box}
body{margin:0;font:14px/1.5 system-ui,sans-serif;background:#14161a;color:#e6e8eb;padding:24px}
h1{font-size:16px;font-weight:600;margin:0 0 20px;color:#9aa4b2;letter-spacing:.08em;text-transform:uppercase}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:24px}
.card{background:#1c1f26;border:1px solid #262a33;border-radius:10px;padding:16px}
.k{font-size:11px;color:#7d8797;text-transform:uppercase;letter-spacing:.06em}
.v{font-size:28px;font-weight:600;margin-top:6px;font-variant-numeric:tabular-nums}
.sub{font-size:12px;color:#7d8797;margin-top:2px}
.bar{height:6px;background:#262a33;border-radius:3px;overflow:hidden;margin-top:10px}
.bar i{display:block;height:100%;background:#4a9eff}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:720px){.cols{grid-template-columns:1fr}}
table{width:100%;border-collapse:collapse}
th{text-align:left;font-size:11px;color:#7d8797;text-transform:uppercase;padding:6px 8px;font-weight:500}
td{padding:6px 8px;border-top:1px solid #262a33;font-variant-numeric:tabular-nums}
.ev{max-height:340px;overflow-y:auto}
.ev div{padding:5px 8px;border-top:1px solid #262a33;display:flex;gap:10px}
.ts{color:#5f6875;font-size:12px;min-width:62px}
.tabs{display:flex;gap:8px;margin-bottom:16px}
.tabs button{background:#1c1f26;color:#7d8797;border:1px solid #262a33;border-radius:8px;padding:8px 18px;font:inherit;cursor:pointer}
.tabs button.on{background:#4a9eff;color:#0d1117;border-color:#4a9eff;font-weight:600}
.it{display:flex;align-items:center;gap:8px}
.it img{width:26px;height:26px;object-fit:contain;flex:none}
.ei{width:18px;height:18px;object-fit:contain;flex:none;margin-right:-4px}
.ev div{align-items:center}
.harvest{color:#7dd3a0}.fight{color:#ffb454}.drop{color:#4a9eff;font-weight:600}.levelup{color:#c084fc;font-weight:600}.xp{color:#7dd3a0;font-weight:600}.kamas{color:#ffd166;font-weight:600}
.off{color:#e05561}
.sp{display:flex;align-items:center;gap:8px;padding:6px 8px;border-top:1px solid #262a33}
.sp:first-child{border-top:0}
.sp .nm{flex:1;font-weight:500}
.sp .tag{font-size:10px;color:#7d8797;border:1px solid #262a33;border-radius:4px;padding:1px 5px}
.sp .tag.z{color:#ffb454;border-color:#4a3a22}
.sp button{background:#262a33;color:#9aa4b2;border:0;border-radius:5px;width:24px;height:22px;cursor:pointer;font:inherit;line-height:1}
.sp button:hover{background:#4a9eff;color:#0d1117}
.sp .rank{color:#4a9eff;font-weight:700;min-width:16px;font-variant-numeric:tabular-nums}
.sp.na{opacity:.45}
.box{background:#14161a;border:1px solid #262a33;border-radius:8px;max-height:260px;overflow-y:auto}
.opt{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:12px;font-size:13px}
.opt label{display:inline-flex;align-items:center;gap:6px;cursor:pointer}
.opt select,.opt input[type=number]{background:#14161a;color:#e6e8eb;border:1px solid #262a33;border-radius:6px;padding:4px 6px;font:inherit}
.hint{font-size:12px;color:#5f6875;padding:10px 8px}
</style></head><body>
<h1>Bot Paradox <span id="live" class="off">— hors ligne</span>
  <a href="/assist" target="_blank" style="float:right;font-size:12px;
     text-transform:none;letter-spacing:0;color:#3fb950;text-decoration:none;
     border:1px solid #2ea043;border-radius:8px;padding:6px 12px">Assistant de combat ↗</a>
</h1>
<div class="tabs">
  <button id="tObserve" onclick="setMode('off')">Observer</button>
  <button id="tHarvest" onclick="setMode('harvest')">Harvest</button>
  <button id="tFarm" onclick="setMode('farm')">Farming</button>
  <button id="tKrala" onclick="setMode('kralamoure')" title="Combat scripté du Kralamoure Géant (placement fixe)">Kralamoure</button>
</div>
<label style="display:inline-flex;align-items:center;gap:8px;margin:0 0 12px;
              cursor:pointer;font-size:13px;color:#e6e8eb">
  <input type="checkbox" id="soulCap" onchange="toggleSoul()" style="width:16px;height:16px">
  <span>Capture d'âmes <span class="sub">(413, boss, début de tour)</span></span>
</label>
<label style="display:inline-flex;align-items:center;gap:8px;margin:0 0 12px 16px;
              cursor:pointer;font-size:13px;color:#e6e8eb">
  <input type="checkbox" id="prepMaitrise" onchange="togglePrep('maitrise')" style="width:16px;height:16px">
  <span>Maîtrise de l'Arc <span class="sub">(180, tour 1)</span></span>
</label>
<label style="display:inline-flex;align-items:center;gap:8px;margin:0 0 12px 16px;
              cursor:pointer;font-size:13px;color:#e6e8eb">
  <input type="checkbox" id="prepTir" onchange="togglePrep('tir')" style="width:16px;height:16px">
  <span>Tir Puissant <span class="sub">(166, tour 1)</span></span>
</label>
<label style="display:inline-flex;align-items:center;gap:8px;margin:0 0 12px 16px;
              cursor:pointer;font-size:13px;color:#e6e8eb">
  <input type="checkbox" id="prepCoffre" onchange="togglePrep('coffre')" style="width:16px;height:16px">
  <span>Coffre animé <span class="sub">(6019, tour 1)</span></span>
</label>
<label style="display:inline-flex;align-items:center;gap:8px;margin:0 0 12px 16px;
              cursor:pointer;font-size:13px;color:#e6e8eb">
  <input type="checkbox" id="scriptKorriandre" onchange="toggleScript('korriandre')" style="width:16px;height:16px">
  <span>Script Korriandre <span class="sub">(glyphes — farming)</span></span>
</label>
<div class="grid">
  <div class="card farmOnly"><div class="k">Monstres tués</div><div class="v" id="kills">0</div><div class="sub" id="killsRate"></div></div>
  <div class="card farmOnly"><div class="k">XP gagnée</div><div class="v" id="xpg">0</div><div class="sub" id="xpgRate"></div></div>
  <div class="card"><div class="k">Kamas gagnés</div><div class="v" id="kam">0</div><div class="sub" id="kamRate"></div></div>
  <div class="card harvestOnly"><div class="k">Fauchages</div><div class="v" id="h">0</div><div class="sub" id="hr"></div></div>
  <div class="card"><div class="k">Combats</div><div class="v" id="f">0</div><div class="sub" id="fw"></div></div>
  <div class="card"><div class="k">Pods</div><div class="v" id="p">0</div><div class="bar"><i id="pb" style="width:0"></i></div></div>
  <div class="card"><div class="k">Session</div><div class="v" id="u">0:00</div><div class="sub" id="map"></div></div>
  <div class="card"><div class="k">Niveau</div><div class="v" id="lvl">—</div>
    <div class="bar"><i id="xb" style="width:0"></i></div><div class="sub" id="xt"></div></div>
</div>
<div class="card farmOnly" id="combatCard" style="margin-bottom:12px">
  <div class="k" style="margin-bottom:10px">Sorts de combat
    <span class="sub" style="text-transform:none;letter-spacing:0">l'ordre est
    la priorité : le premier sort qui a une cible valide est lancé</span></div>
  <div id="spWarn"></div>
  <div class="cols">
    <div><div class="k" style="margin-bottom:6px">Utilisés</div>
      <div class="box" id="spSel"></div></div>
    <div><div class="k" style="margin-bottom:6px">Disponibles</div>
      <div class="box" id="spAll"></div></div>
  </div>
  <div class="k" style="margin:14px 0 6px">Buffs lancés sur soi en début de tour</div>
  <div class="box" id="spBuff"></div>
  <div class="opt">
    <label><input type="checkbox" id="cMove" onchange="setMove()">
      Se rapprocher si aucune cible n'est à portée</label>
    <label>Délai entre actions
      <select id="cDelay" onchange="setDelay()">
        <option value="15">0,15 s — rapide</option>
        <option value="30">0,30 s — normal</option>
        <option value="60">0,60 s — prudent</option>
      </select></label>
    <label>Trajet max pour agresser
      <input type="number" id="cSteps" min="1" max="60" onchange="setSteps()"
             style="width:64px"> pas</label>
    <button onclick="clearSpells()" style="background:#262a33;color:#9aa4b2;
      border:0;border-radius:6px;padding:5px 10px;cursor:pointer;font:inherit">
      Tout réinitialiser</button>
  </div>
</div>
<div class="card" id="jobsCard" style="margin-bottom:12px;display:none">
  <div class="k" style="margin-bottom:8px">Métiers</div><div id="jobs"></div></div>
<div class="cols">
  <div class="card"><div class="k" style="margin-bottom:8px">Objets ramassés</div>
    <table><thead><tr><th>Objet</th><th>Gagné</th><th>Total</th></tr></thead><tbody id="items"></tbody></table></div>
  <div class="card"><div class="k" style="margin-bottom:8px">Journal</div><div class="ev" id="ev"></div></div>
</div>
<script>
const pad=n=>String(n).padStart(2,'0');
const dur=s=>`${Math.floor(s/3600)}:${pad(Math.floor(s/60)%60)}:${pad(Math.floor(s%60))}`;
const hhmm=t=>{const d=new Date(t*1000);return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`};
async function tick(){
  let d;
  try{ d=await (await fetch('/stats')).json(); }
  catch(e){ document.getElementById('live').textContent='— hors ligne'; return; }
  document.getElementById('live').textContent='';
  document.getElementById('h').textContent=d.harvests;
  document.getElementById('hr').textContent=d.harvests_per_hour.toFixed(0)+' / heure';
  document.getElementById('f').textContent=d.fights;
  document.getElementById('fw').textContent=d.fights_won+' terminés';
  document.getElementById('p').textContent=d.pods.toLocaleString('fr');
  document.getElementById('pb').style.width=Math.min(100,d.pods_pct)+'%';
  document.getElementById('u').textContent=dur(d.uptime);
  document.getElementById('map').textContent=d.map_id?('carte '+d.map_id):'';
  document.getElementById('items').innerHTML=d.items.map(i=>
    `<tr><td class="it">${i.gfx?`<img src="/icon/${i.gfx}" alt="" loading="lazy">`:''}<span>${i.name}</span></td>`
    +`<td>+${i.gained.toLocaleString('fr')}</td><td>${i.qty.toLocaleString('fr')}</td></tr>`).join('')
    ||'<tr><td colspan="3" style="color:#5f6875">rien encore</td></tr>';
  const fmt=n=>n.toLocaleString('fr');
  document.getElementById('tObserve').className=d.mode==='off'?'on':'';
  document.getElementById('tHarvest').className=d.mode==='harvest'?'on':'';
  document.getElementById('tFarm').className=d.mode==='farm'?'on':'';
  document.getElementById('tKrala').className=d.mode==='kralamoure'?'on':'';
  document.getElementById('soulCap').checked=!!d.capture_souls;
  document.getElementById('prepMaitrise').checked=!!d.auto_maitrise;
  document.getElementById('prepTir').checked=!!d.auto_tir;
  document.getElementById('prepCoffre').checked=!!d.auto_coffre;
  document.getElementById('scriptKorriandre').checked=!!d.script_korriandre;
  document.querySelectorAll('.farmOnly').forEach(e=>e.style.display=(d.mode==='farm'||d.mode==='kralamoure')?'':'none');
  document.querySelectorAll('.harvestOnly').forEach(e=>e.style.display=d.mode==='harvest'?'':'none');
  document.getElementById('kills').textContent=fmt(d.kills);
  document.getElementById('killsRate').textContent=d.kills_per_hour.toFixed(0)+' / heure';
  document.getElementById('xpg').textContent=fmt(d.xp_gained);
  document.getElementById('xpgRate').textContent=fmt(Math.round(d.xp_per_hour))+' / heure';
  document.getElementById('kam').textContent=fmt(d.kamas_gained);
  document.getElementById('kamRate').textContent=fmt(Math.round(d.kamas_per_hour))+' / heure';
  document.getElementById('lvl').textContent=d.level??'—';
  if(d.xp){
    document.getElementById('xb').style.width=Math.min(100,d.xp.pct)+'%';
    document.getElementById('xt').textContent=
      d.xp.pct.toFixed(1)+'% — reste '+d.xp.remaining.toLocaleString('fr')+' xp';
  }
  const jc=document.getElementById('jobsCard');
  jc.style.display=d.jobs.length?'block':'none';
  document.getElementById('jobs').innerHTML=d.jobs.map(j=>
    `<div style="margin-bottom:8px"><div style="display:flex;justify-content:space-between">
     <span>${j.name}</span><span class="sub">niv. ${j.level} — ${j.pct.toFixed(0)}%</span></div>
     <div class="bar"><i style="width:${Math.min(100,j.pct)}%"></i></div></div>`).join('');
  document.getElementById('ev').innerHTML=d.events.map(e=>
    `<div><span class="ts">${hhmm(e.t)}</span>`
    +(e.gfx?`<img class="ei" src="/icon/${e.gfx}" alt="">`:'')
    +`<span class="${e.kind}">${e.text}</span></div>`).join('');
}
async function setMode(m){ try{ await fetch('/mode/'+m); tick(); }catch(e){} }
async function toggleSoul(){ try{ await fetch('/capture/toggle'); tick(); }catch(e){} }
async function togglePrep(k){ try{ await fetch('/prep/toggle/'+k); tick(); }catch(e){} }
async function toggleScript(k){ try{ await fetch('/script/toggle/'+k); tick(); }catch(e){} }

// ── sorts de combat ──
// Le panneau ne se redessine qu'au chargement et après une action : le
// rafraîchissement des compteurs (1,5 s) ne doit pas voler le focus d'un champ
// ni faire clignoter la liste.
let combat=null;
const tags=s=>`<span class="tag">${s.pa} PA</span>`
  +`<span class="tag">${s.rmin}-${s.rmax}</span>`
  +(s.zone?`<span class="tag z">zone ${s.zone}</span>`:'')
  +(s.max?`<span class="tag">${s.max}/tour</span>`:'');
async function combatFetch(url){
  try{ combat=await (await fetch(url)).json(); paintCombat(); }catch(e){}
}
function paintCombat(){
  if(!combat) return;
  // Hors connexion la liste vient du cache disque : elle peut appartenir à une
  // autre classe que celle du personnage qui va jouer.
  document.getElementById('spWarn').innerHTML = combat.known ? ''
    : '<div class="hint" style="color:#ffb454">Sorts non confirmés par le '
      +'serveur : connecte-toi pour que la liste corresponde à la classe du '
      +'personnage. En attendant, le bot n\\'utilisera que les sorts qu\\'il '
      +'possède réellement.</div>';
  const by={}; combat.spells.forEach(s=>by[s.id]=s);
  const sel=combat.selected.map(id=>by[id]).filter(Boolean);
  document.getElementById('spSel').innerHTML = sel.length
    ? sel.map((s,i)=>`<div class="sp ${s.learned?'':'na'}">`
        +`<span class="rank">${i+1}</span><span class="nm">${s.name}`
        +(s.learned?'':' <span class="sub">non appris</span>')+`</span>${tags(s)}`
        +`<button onclick="order(${s.id},'up')" title="monter">&#9650;</button>`
        +`<button onclick="order(${s.id},'down')" title="descendre">&#9660;</button>`
        +`<button onclick="toggleSpell(${s.id})" title="retirer">&#10005;</button>`
        +`</div>`).join('')
    : '<div class="hint">Aucun sort choisi : le bot utilise tous les sorts '
      +'offensifs appris, les plus chers en PA d\\'abord.</div>';
  const rest=combat.spells.filter(s=>!combat.selected.includes(s.id));
  document.getElementById('spAll').innerHTML = rest.length
    ? rest.map(s=>`<div class="sp ${s.learned?'':'na'}">`
        +`<span class="nm">${s.name}`
        +(s.learned?'':' <span class="sub">non appris</span>')+`</span>${tags(s)}`
        +`<button onclick="toggleSpell(${s.id})" title="ajouter">+</button>`
        +`</div>`).join('')
    : '<div class="hint">catalogue de sorts vide — connecte-toi une fois en '
      +'combat pour que le serveur l\\'envoie.</div>';
  document.getElementById('spBuff').innerHTML = combat.spells.length
    ? combat.spells.map(s=>{
        const on=combat.buffs.includes(s.id);
        return `<div class="sp ${s.learned?'':'na'}">`
          +`<label class="nm" style="cursor:pointer;font-weight:500">`
          +`<input type="checkbox" ${on?'checked':''} `
          +`onchange="toggleBuff(${s.id})"> ${s.name}</label>${tags(s)}</div>`;
      }).join('')
    : '<div class="hint">aucun sort connu</div>';
  document.getElementById('cMove').checked=!!combat.move;
  document.getElementById('cSteps').value=combat.engage_max_steps;
  const d=document.getElementById('cDelay');
  d.value=String(Math.round(combat.delay*100));
  if(!d.value||!d.selectedOptions.length) d.value='30';
}
const toggleSpell=id=>combatFetch('/combat/toggle/'+id);
const toggleBuff=id=>combatFetch('/combat/buff/'+id);
const order=(id,d)=>combatFetch('/combat/order/'+id+'/'+d);
const clearSpells=()=>combatFetch('/combat/clear');
const setMove=()=>combatFetch('/combat/move/'
  +(document.getElementById('cMove').checked?1:0));
const setDelay=()=>combatFetch('/combat/delay/'
  +document.getElementById('cDelay').value);
const setSteps=()=>combatFetch('/combat/steps/'
  +document.getElementById('cSteps').value);

combatFetch('/combat');
tick();setInterval(tick,1500);
</script></body></html>"""


async def _handle(reader, writer):
    try:
        line = await asyncio.wait_for(reader.readline(), 5)
        path = line.decode("latin-1").split(" ")[1] if b" " in line else "/"
        while True:                     # on vide les en-têtes
            h = await asyncio.wait_for(reader.readline(), 5)
            if h in (b"\r\n", b"\n", b""):
                break

        if path.startswith("/mode/"):
            wanted = path.rsplit("/", 1)[-1].split("?")[0]
            if wanted in ("off", "harvest", "farm", "kralamoure"):
                _stats.mode = wanted
                _stats.event("info", f"mode {wanted}")
                _stats.persist()
            body = json.dumps({"mode": _stats.mode}).encode()
            writer.write(_headers("application/json", len(body)) + body)
            await writer.drain()
            return

        if path.startswith("/capture/toggle"):
            _stats.capture_souls = not _stats.capture_souls
            _stats.persist()
            _stats.event("info", "capture d'âmes " +
                         ("activée" if _stats.capture_souls else "désactivée"))
            body = json.dumps({"capture_souls": _stats.capture_souls}).encode()
            writer.write(_headers("application/json", len(body)) + body)
            await writer.drain()
            return

        if path.startswith("/prep/toggle/"):
            key = path.rsplit("/", 1)[-1].split("?")[0]
            names = {
                "maitrise": ("auto_maitrise", "Maîtrise de l'Arc"),
                "tir": ("auto_tir", "Tir Puissant"),
                "coffre": ("auto_coffre", "Coffre animé"),
            }
            if key in names:
                attr, label = names[key]
                setattr(_stats, attr, not getattr(_stats, attr))
                _stats.persist()
                on = getattr(_stats, attr)
                _stats.event("info", f"{label} " +
                             ("activé" if on else "désactivé") +
                             " (auto tour 1)")
            body = json.dumps({
                "auto_maitrise": _stats.auto_maitrise,
                "auto_tir": _stats.auto_tir,
                "auto_coffre": _stats.auto_coffre,
            }).encode()
            writer.write(_headers("application/json", len(body)) + body)
            await writer.drain()
            return

        if path.startswith("/script/toggle/"):
            key = path.rsplit("/", 1)[-1].split("?")[0]
            from fight_scripts import known_ids
            labels = {"korriandre": "Script Korriandre"}
            if key in known_ids():
                cur = list(_stats.fight_scripts)
                if key in cur:
                    cur.remove(key)
                    on = False
                else:
                    cur.append(key)
                    on = True
                _stats.fight_scripts = cur
                _stats.persist()
                _stats.event("info", labels.get(key, key) + " " +
                             ("activé" if on else "désactivé"))
            body = json.dumps({
                "fight_scripts": list(_stats.fight_scripts),
                "script_korriandre": "korriandre" in _stats.fight_scripts,
            }).encode()
            writer.write(_headers("application/json", len(body)) + body)
            await writer.drain()
            return

        if path.startswith("/combat"):
            parts = path.split("?")[0].strip("/").split("/")[1:]
            try:
                state = combat_command(parts)
            except (IndexError, ValueError):
                state = combat_state()
            body = json.dumps(state).encode()
            writer.write(_headers("application/json", len(body)) + body)
            await writer.drain()
            return

        if path.startswith("/craft/"):
            parts = path.split("?")[0].strip("/").split("/")
            body = b'{"ok":true}'
            try:
                if parts[1] == "search":
                    q = ""
                    if "?" in path and "q=" in path:
                        import urllib.parse as up
                        q = up.unquote(path.split("q=", 1)[1].split("&")[0])
                    res = craft.book().search(q)
                    # Icône via items.json (clé "<type>/<gfx>"), comme le reste.
                    gd = gamedata.get()
                    for r in res:
                        r["gfx"] = gd.icon_key(gd.items.get(str(r["id"]))) or ""
                    body = json.dumps(res).encode()
                elif parts[1] == "add":
                    _stats.craft_add(int(parts[2]))
                elif parts[1] == "set":
                    _stats.craft_set(int(parts[2]), int(parts[3]))
                elif parts[1] == "clear":
                    _stats.craft_clear()
                elif parts[1] == "fuse":
                    payload, err = build_fuse(_stats, int(parts[2]))
                    if err:
                        body = json.dumps({"success": False, "error": err}).encode()
                    else:
                        # Appel bloquant vers le pont : dans un thread pour ne
                        # pas geler la boucle.
                        res = await asyncio.to_thread(_post_bridge, payload)
                        body = json.dumps(res).encode()
            except (IndexError, ValueError):
                pass
            writer.write(_headers("application/json", len(body)) + body)
            await writer.drain()
            return

        if path.startswith("/harvest/"):
            import urllib.parse as up
            parts = path.split("?")[0].strip("/").split("/")
            body = b'{"ok":true}'
            try:
                if parts[1] == "jobs":
                    body = json.dumps({
                        "jobs": harvest_jobs_list(),
                        "selected": sorted(_stats.harvest_jobs),
                    }).encode()
                elif parts[1] == "toggle":
                    job = up.unquote(parts[2])
                    if job in _stats.harvest_jobs:
                        _stats.harvest_jobs.discard(job)
                    else:
                        _stats.harvest_jobs.add(job)
                    _stats.persist()
            except (IndexError, ValueError):
                pass
            writer.write(_headers("application/json", len(body)) + body)
            await writer.drain()
            return

        if path.startswith("/pause") or path.startswith("/resume"):
            _stats.enabled = path.startswith("/resume")
            _stats.event("info", "bot relancé" if _stats.enabled
                                 else "bot mis en pause")
            body = b'{"ok":true}'
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                         + f"Content-Length: {len(body)}\r\n".encode()
                         + b"Connection: close\r\n\r\n" + body)
            await writer.drain()
            return

        if path.startswith("/shutdown"):
            # Arrêt commandé par l'interface. Passer par le réseau plutôt que
            # par le processus permet d'arrêter un bot lancé n'importe comment
            # — depuis un terminal comme depuis l'exe.
            body = b'{"ok":true}'
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                         + f"Content-Length: {len(body)}\r\n".encode()
                         + b"Connection: close\r\n\r\n" + body)
            await writer.drain()
            _stats.persist()
            print("[bot] arrêt demandé par l'interface.", flush=True)
            # On laisse la réponse partir avant de couper.
            asyncio.get_running_loop().call_later(0.2, lambda: os._exit(0))
            return

        if path.startswith("/overlay/geo"):
            # Sans parametres -> renvoie la geometrie sauvee ; avec -> sauve.
            import urllib.parse as up
            body = b'{}'
            try:
                q = up.parse_qs(path.split("?", 1)[1]) if "?" in path else {}
                if q:
                    geo = {k: float(v[0]) for k, v in q.items()
                           if k in ("l", "t", "w", "h")}
                    _save_overlay_geo(geo)
                    body = b'{"ok":true}'
                else:
                    body = json.dumps(_load_overlay_geo()).encode()
            except (ValueError, IndexError, KeyError):
                pass
            writer.write(_headers("application/json", len(body)) + body)
            await writer.drain()
            return

        if path.startswith("/assist/state"):
            body = json.dumps(_assist_state()).encode("utf-8")
            writer.write(_headers("application/json", len(body)) + body)
            await writer.drain()
            return

        if path.startswith("/assist/valid"):
            import urllib.parse as up
            q = up.parse_qs(path.split("?", 1)[1]) if "?" in path else {}
            try:
                sid = int(q.get("spell", ["0"])[0])
            except ValueError:
                sid = 0
            body = json.dumps(_assist_valid(sid)).encode("utf-8")
            writer.write(_headers("application/json", len(body)) + body)
            await writer.drain()
            return

        if path.startswith("/assist"):
            body = ASSIST_PAGE.encode("utf-8")
            writer.write(_headers("text/html; charset=utf-8", len(body)) + body)
            await writer.drain()
            return

        if path.startswith("/overlay"):
            body = OVERLAY_PAGE.encode("utf-8")
            ctype = "text/html; charset=utf-8"
        elif path.startswith("/stats"):
            body = json.dumps(_stats.snapshot()).encode("utf-8")
            ctype = "application/json"
        elif path.startswith("/icon/"):
            # Les icônes sont déjà sur le disque, dans les ressources du
            # client : on les sert telles quelles plutôt que de les copier.
            # La clé est "<type>/<gfx>", donc deux segments et non un seul.
            key = path[6:].split("?")[0].strip("/")
            file = gamedata.get().icons.get(key)
            if not file:
                writer.write(b"HTTP/1.1 404 Not Found\r\n"
                             b"Content-Length: 0\r\nConnection: close\r\n\r\n")
                await writer.drain()
                return
            with open(file, "rb") as f:
                body = f.read()
            ctype = "image/png"
        else:
            body = PAGE.encode("utf-8")
            ctype = "text/html; charset=utf-8"

        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            + f"Content-Type: {ctype}\r\n".encode()
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"Cache-Control: no-store\r\nConnection: close\r\n\r\n"
            + body)
        await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


_stats = Stats()

# Référence vers le Brain courant (posée par bot.py). Sert à l'assistant de
# combat, qui lit l'état vif du combat (combattants, carte, sorts) sans passer
# par le réseau. None hors connexion.
_brain = None


def stats():
    return _stats


def set_brain(brain):
    global _brain
    _brain = brain


def _assist_state():
    """Photo de l'état de combat pour l'assistant : combattants + mes sorts +
    géométrie de la carte (pour le rendu). {'active': False} hors combat."""
    b = _brain
    if b is None:
        return {"active": False}
    c = getattr(b, "combat", None)
    g = getattr(c, "gmap", None) if c else None
    if c is None or g is None or not c.active or c.char_id is None:
        return {"active": False}
    me = c.fighters.get(c.char_id)
    fighters = []
    for f in c.fighters.values():
        if f.hp <= 0:
            continue
        tpl = c.fighter_templates.get(f.id)
        fighters.append({
            "cell": f.cell,
            "me": f.id == c.char_id,
            "enemy": f.is_monster,
            "boss": bool(tpl and tpl in DUNGEON_BOSS_IDS),
            "hp": f.hp,
            "hpmax": f.pvmax,
        })
    spells = []
    seen = set()
    for sid, lvl in c.levels.items():
        sp = c.catalog.get((sid, lvl)) or c.catalog.get((sid, 1))
        if sp is None or sp.id in seen:
            continue
        seen.add(sp.id)
        spells.append({"id": sp.id, "name": sp.name, "pa": sp.pa,
                       "rmin": sp.range_min, "rmax": sp.range_max,
                       "los": sp.los, "free_cell": sp.free_cell,
                       "summon": getattr(sp, "summon", False)})
    spells.sort(key=lambda s: s["id"])
    cells = [{"w": g.walkable(i), "l": g.cells[i]["los"]} for i in range(len(g))]
    turns = getattr(c, "confusion_turns", 0)
    labels = {0: "aucune", 1: "90° horaire", 2: "180°", 3: "90° anti-horaire"}
    return {"active": True, "my_cell": me.cell if me else None,
            "my_pa": me.pa if me else 0, "my_pm": me.pm if me else 0,
            "enemies": sum(1 for f in fighters if f["enemy"]),
            "confusion": {"turns": turns, "label": labels.get(turns, "aucune")},
            "harebourg": _harebourg_plan(c, g, me, turns),
            "fighters": fighters, "spells": spells, "cells": cells}


def _harebourg_plan(c, g, me, turns):
    """Plan de déblocage du Comte Harebourg (Gousset). Pour rendre le Comte
    vulnérable : une entité (ton invocation) doit être sur la symétrique du
    Comte par rapport à toi (toi au milieu), puis tu FRAPPES le Comte sur un
    tour PAIR → il échange avec l'invocation et devient vulnérable. On renvoie
    où poser l'invoc et où cliquer pour taper (rotation de confusion compensée).
    None si le Comte n'est pas là."""
    comte = None
    for f in c.fighters.values():
        if f.hp > 0 and c.fighter_templates.get(f.id) == 31077:
            comte = f
            break
    if comte is None or me is None:
        return None
    total = len(g)
    occupied = {f.cell for f in c.fighters.values() if f.hp > 0}
    dist = losrange.distance(me.cell, comte.cell)
    # Case où poser l'invocation = symétrique du Comte par rapport à moi.
    invoc = losrange.reflect(total, me.cell, comte.cell)
    invoc_ok = invoc >= 0 and g.walkable(invoc) and invoc not in occupied
    # Cases à CLIQUER (rotation de confusion compensée) :
    invoc_click = losrange.rotate_around(total, me.cell, invoc, turns) if invoc >= 0 else -1
    hit_click = losrange.rotate_around(total, me.cell, comte.cell, turns)
    return {
        "comte": comte.cell,
        "dist": dist,
        "adjacent": dist == 1,
        "invoc": invoc if invoc_ok else -1,
        "invoc_click": invoc_click if invoc_ok else -1,
        "hit_click": hit_click,
        "delock_turn": (getattr(c, "comte_turns", 0) % 2 == 1),
    }


def _assist_valid(spell_id):
    """Cases où le sort peut être lancé depuis ma position (portée + LdV),
    calculées en local (losrange). Liste vide si indisponible."""
    b = _brain
    c = getattr(b, "combat", None) if b else None
    g = getattr(c, "gmap", None) if c else None
    if c is None or g is None or not c.active or c.char_id is None:
        return {"cells": []}
    me = c.fighters.get(c.char_id)
    if me is None:
        return {"cells": []}
    lvl = c.levels.get(spell_id, 1)
    sp = c.catalog.get((spell_id, lvl)) or c.catalog.get((spell_id, 1))
    if sp is None:
        return {"cells": []}
    occupied = {f.cell for f in c.fighters.values() if f.hp > 0}
    summon = getattr(sp, "summon", False)
    # Cases où le sort ATTERRIT réellement (portée + LdV depuis ma position).
    # Invocation -> cases libres praticables (où poser la créature).
    landings = losrange.valid_target_cells(
        g, me.cell, sp.range_min, sp.range_max, sp.los, sp.free_cell, occupied,
        placement=summon)
    turns = getattr(c, "confusion_turns", 0)
    total = len(g)
    if turns:
        # Confusion Harebourg : le serveur tourne la case cliquée de -turns
        # autour de moi. Pour toucher E, il faut CLIQUER rotate(E, +turns).
        pairs = []
        for e in landings:
            r = losrange.rotate_around(total, me.cell, e, turns)
            if r >= 0:
                pairs.append([r, e])
        return {"center": me.cell, "spell": spell_id,
                "cells": [p[0] for p in pairs], "pairs": pairs,
                "rmin": sp.range_min, "rmax": sp.range_max, "los": sp.los,
                "summon": summon, "confusion": turns}
    return {"center": me.cell, "spell": spell_id, "cells": landings,
            "pairs": [[e, e] for e in landings],
            "rmin": sp.range_min, "rmax": sp.range_max, "los": sp.los,
            "summon": summon, "confusion": 0}


async def serve():
    try:
        server = await asyncio.start_server(_handle, HOST, PORT)
    except OSError as e:
        print(f"[dashboard] port {PORT} indisponible : {e}")
        return
    print(f"[dashboard] http://{HOST}:{PORT}\n")
    async with server:
        await server.serve_forever()
