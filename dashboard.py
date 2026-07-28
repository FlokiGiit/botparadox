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

import craft
import gamedata
from overlay_page import OVERLAY_PAGE

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


_DUNGEONS = None


def dungeons():
    """Liste des donjons (id, nom, tiers), livree dans data/dungeons.json."""
    global _DUNGEONS
    if _DUNGEONS is None:
        try:
            with open(_data("dungeons.json"), encoding="utf-8") as f:
                _DUNGEONS = json.load(f)
        except (OSError, ValueError):
            _DUNGEONS = []
    return _DUNGEONS


def build_dungeon_start(dungeon_id, tier, no_teleport=False, rusher=False):
    """Action pour lancer un donjon PRECIS (contourne le 'run-current' du serveur
    qui retombe toujours sur Incarnam en ignorant les favoris)."""
    return {"panelId": "mod-dungeon", "action": "start",
            "params": {"modDungeonId": int(dungeon_id), "tierValue": int(tier),
                       "restartExisting": False, "noTeleport": bool(no_teleport),
                       "rusherMode": bool(rusher), "soloOverride": False}}


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
                      "xp": 0, "kamas": 0}
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
        except (OSError, ValueError):
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
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"mode": self.mode, "total": total,
                           "craft": {str(k): v
                                     for k, v in self.craft_targets.items()}}, f)
        except OSError:
            pass

    def event(self, kind, text, **extra):
        # Le texte des butins n'est pas figé ici : le nom de l'objet n'est
        # parfois connu qu'après coup, une fois la correspondance
        # exemplaire -> modèle reçue. On garde l'identifiant et on résout à
        # l'affichage.
        self.events.appendleft({"t": time.time(), "kind": kind,
                                "text": text, **extra})

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
        def have(tid):
            h = self.bag.get(str(tid), 0)
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
                "depth": node["depth"],
                "craftable": node["craftable"],
                "canfuse": can_fuse(node["id"]),
            })
        return {"targets": targets, "tree": tree}

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
</style></head><body>
<h1>Bot Paradox <span id="live" class="off">— hors ligne</span></h1>
<div class="tabs">
  <button id="tObserve" onclick="setMode('off')">Observer</button>
  <button id="tHarvest" onclick="setMode('harvest')">Harvest</button>
  <button id="tFarm" onclick="setMode('farm')">Farming</button>
</div>
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
  document.querySelectorAll('.farmOnly').forEach(e=>e.style.display=d.mode==='farm'?'':'none');
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
            if wanted in ("off", "harvest", "farm"):
                _stats.mode = wanted
                _stats.event("info", f"mode {wanted}")
                _stats.persist()
            body = json.dumps({"mode": _stats.mode}).encode()
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

        if path.startswith("/dungeon/"):
            parts = path.split("?")[0].strip("/").split("/")
            body = b'{"ok":true}'
            try:
                if parts[1] == "list":
                    body = json.dumps(dungeons()).encode()
                elif parts[1] == "start":
                    did = int(parts[2])
                    tier = int(parts[3]) if len(parts) > 3 else 0
                    nt = "noteleport=1" in path.lower()
                    payload = build_dungeon_start(did, tier, no_teleport=nt)
                    res = await asyncio.to_thread(_post_bridge, payload)
                    body = json.dumps(res).encode()
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


def stats():
    return _stats


async def serve():
    try:
        server = await asyncio.start_server(_handle, HOST, PORT)
    except OSError as e:
        print(f"[dashboard] port {PORT} indisponible : {e}")
        return
    print(f"[dashboard] http://{HOST}:{PORT}\n")
    async with server:
        await server.serve_forever()
