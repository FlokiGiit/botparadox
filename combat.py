"""
IA de combat.

Principe : ne rien coder en dur. Le serveur nous donne tout.

  ST   catalogue des sorts (coût en PA, portée, lancers par tour, catégorie)
  SL   niveau appris de chaque sort
  GTM  état des combattants (PV, PA, PM, cellule)

La visée est calculée EN LOCAL (module losrange, porté depuis le
PathFinding.java de l'émulateur : portée et ligne de vue exactes). Le serveur
n'est plus interrogé case par case : la version précédente envoyait une rafale
de ZDC par lancer — une dizaine de paquets et jusqu'à 2 s d'attente chacun —
pour obtenir les dégâts prévus. Ça rendait chaque tour lent et très bavard côté
client, pour un gain nul dès lors que le joueur choisit lui-même ses sorts.

L'ordre des sorts vient donc de l'onglet Farming (le joueur les classe), et non
plus d'une estimation de dégâts. À défaut de choix, on prend les plus chers en
PA d'abord.

Les indices de champs de ST ont été validés en les comparant à la fiche de
sort affichée par le client (Flèche Explosive : 4 PA, portée 1-8, 2 lancers
par tour, ligne de vue requise, probabilité critique 2).
"""

import asyncio
import json
import os

import losrange
from boss_ids import DUNGEON_BOSS_IDS

# Découpage d'une entrée de ST.
ST_PA = 2
ST_RANGE_MIN = 3
ST_RANGE_MAX = 4
# Ligne de vue requise (1/0). Repéré en comparant les entrées ST : =1 pour
# toutes les attaques (Flèche Explosive, Magique, Recul...) et Tir Puissant,
# =0 pour Maîtrise de l'Arc (buff sans LdV).
ST_LOS = 8
ST_FREE_CELL = 9
ST_MAX_PER_TURN = 11      # 0 = pas de limite
ST_ZONE = 16
ST_CATEGORY = 19
# Nom du sort : premier champ non vide et non numérique après la description.
ST_NAME_FROM = 21

# Délais : assez courts pour ne pas perdre de temps, assez présents pour ne pas
# répondre en zéro milliseconde là où un humain met au moins le temps du clic.
DELAY_BEFORE_TURN = 0.2
DELAY_BETWEEN_CASTS = 0.3
DELAY_BEFORE_END_TURN = 0.1
# Attente après un déplacement de combat avant d'enchaîner un sort : le temps
# que le serveur enregistre la nouvelle position. Assez court pour rester
# rapide, assez long pour qu'un sort dépendant de la case d'arrivée passe.
DELAY_AFTER_MOVE = 1.1

# Un tour ne doit jamais durer indéfiniment : au pire on passe.
TURN_DEADLINE = 20.0

# Garde-fou anti-boucle si le décompte de PA dérive. Assez haut pour vider
# 30+ PA (sorts à 2-3 PA) : l'ancien plafond de 8 laissait ~5–20 PA inutilisés
# ("plafond de lancers atteint -> je passe") alors qu'il restait des cibles.
MAX_CASTS_PER_TURN = 40
# Nombre max de repositionnements par tour : un seul empêchait d'enchaîner
# après un premier déplacement (Explosive maxée puis Magique hors portée).
MAX_MOVES_PER_TURN = 3

# Régénération via vol de vie (ex. Flèche Absorbante, effet 93) :
# on passe ces sorts en tête de liste sous HEAL_ENTER, et on y reste jusqu'à
# HEAL_EXIT — hystérésis pour ne pas osciller à chaque lancer.
HEAL_ENTER_RATIO = 0.50
HEAL_EXIT_RATIO = 0.80

# Capture d'âmes : buff (2 PA, sur soi) qui active la capture des âmes des
# créatures vaincues. L'effet ne dure que 2 tours -> on le RELANCE toutes les
# 2 tours pour qu'il reste actif jusqu'à la mort du dernier mob, quel que soit
# le nombre de tours du combat.
SOUL_CAPTURE_SPELL = 413
SOUL_CAPTURE_REFRESH = 2   # tours de validité du buff

# ── Nileza (Laboratoire) — mécanique Cohobation du serveur 1.43 ──────────────
# Source : Eternal/1.43/.../NilezaMechanics.java
# Taper un Nileza à distance (>1 PO) déclenche Ogavodra : swap sur SA case,
# puis Molalité (renvoi 200 % air dans un rayon 2 autour de sa nouvelle case).
# Conséquences pour le bot :
#   - distance 2 = suicide (après swap on est encore dans le rayon → on mange
#     son propre renvoi) ;
#   - Nileza collé à d'autres Nileza = atterrissage dans le pack → OS au tour
#     suivant (Glace sèche / Fraction) ;
#   - mêlée (dist 1) évite le swap, mais Liqueur de Fée Ling soigne/boost 2
#     tours sur 3 (lancée tour 1 puis tous les 3 tours).
NILEZA_TEMPLATE = 31071
NILEZA_MELEE_DIST = 1
NILEZA_MOLALITY_RADIUS = 2

# ── Script de combat Kralamoure Géant ────────────────────────────────────────
# Séquence fixe tour par tour, reconstituée depuis une capture réelle du combat
# (placement toujours identique confirmé par le joueur). Un tour = une liste
# d'actions ; une action est soit ("move", chemin_encodé) soit (id_sort, case).
# Case du boss, aussi bien pour l'engagement (position sur la carte, vue dans
# GM) que pour les sorts en combat. Toujours la même (confirmé joueur) : on
# vise donc cette case précise pour engager, en ignorant percepteur et mobs
# alentour qui pourraient être plus proches.
KRALAMOURE_BOSS_CELL = 509
# Cases figées : boss = 509, Cawotte = 635 (T1) puis 636 (repositionnement).
# Sorts (id catalogue) :
#   367   Cawotte (invocation)
#   46371 Flamiche (eau)   350 Flamiche (feu)
#   46373 Flamiche (terre) 46372 Flamiche (air)
#   179   Flèche Explosive
# Le déplacement T1 ("move_tr") est calculé au moment du combat : on va le plus
# loin possible vers le haut-droite, dans la limite des PM réels et sur cases
# libres — jamais une case en dur (qui pouvait être injoignable). Ne lance que
# des sorts possédés : un GA300 d'un sort absent du catalogue est ignoré serveur.
KRALAMOURE_SCRIPT = [
    [("move_tr",), (367, 635), (46371, 509)],      # T1
    [(350, 509)],                                   # T2 Flamiche feu
    [(46373, 509)],                                 # T3 Flamiche terre
    [],                                             # T4 passe
    [],                                             # T5 passe
    [],                                             # T6 passe
    [(46372, 509)],                                 # T7 Flamiche air
    [(367, 636)],                                   # T8 Cawotte repositionnée
    [],                                             # T9 passe
    [(179, 509)],                                   # T10 Flèche Explosive
]

# Le serveur n'envoie le catalogue (ST) qu'occasionnellement — une session
# entiere peut n'en recevoir aucun. Sans lui l'IA n'a aucun sort a lancer et
# passe tous ses tours. On le conserve donc sur disque : il ne change qu'a
# la montee d'un sort, alors que les niveaux courants arrivent via SL a
# chaque combat.
from apppaths import data as _data
CATALOG_FILE = _data("spells.json")


class Spell:
    def __init__(self, fields):
        self.id = int(fields[0])
        self.level = int(fields[1])
        self.pa = int(float(fields[ST_PA]))
        self.range_min = int(float(fields[ST_RANGE_MIN]))
        self.range_max = int(float(fields[ST_RANGE_MAX]))
        self.max_per_turn = int(float(fields[ST_MAX_PER_TURN]))
        self.category = fields[ST_CATEGORY] if len(fields) > ST_CATEGORY else ""
        # Zone d'effet, ex. 'Cc' : lettre de forme puis rayon code a..z.
        # Verifie sur la fiche du client : Fleche Explosive niveau 1 porte
        # 'Cc' et l'infobulle annonce "cercle sur un rayon de 2 cases".
        # "Cellules libres" sur la fiche du client : indique si le sort
        # accepte une case vide comme cible. Flèche Explosive vaut 0, et le
        # serveur a refuse 42 tentatives de suite avant que ce champ soit lu.
        self.free_cell = fields[ST_FREE_CELL] not in ("0", "")
        self.los = len(fields) > ST_LOS and fields[ST_LOS] == "1"
        # Sort d'invocation : porte l'effet 181 ("Invoque une créature"). Pour
        # ces sorts, l'assistant montre où POSER (case libre praticable) au lieu
        # d'où frapper. Les blocs d'effets sont aux champs 17 (normal) et 18 (crit).
        # Effet 93 = vol de vie (Flèche Absorbante, etc.) : priorisé sous 50 % PV.
        self.summon = False
        self.life_steal = False
        for _idx in (17, 18):
            if len(fields) > _idx and fields[_idx]:
                import urllib.parse as _up
                for _eff in _up.unquote(fields[_idx]).split("|"):
                    eid = _eff.split(";")[0].strip()
                    if eid == "181":
                        self.summon = True
                    elif eid == "93":
                        self.life_steal = True
        zone = fields[ST_ZONE] if len(fields) > ST_ZONE else ""
        self.zone_radius = (ord(zone[1]) - ord("a")) if len(zone) >= 2 else 0
        # Nom lisible (pour l'assistant de combat), sinon "sort <id>".
        self.name = f"sort {self.id}"
        import urllib.parse as _up
        for f in fields[ST_NAME_FROM:ST_CATEGORY + 6]:
            if f and not f.replace(".", "").isdigit():
                cand = _up.unquote(f)
                if cand and not cand.isdigit():
                    self.name = cand
                    break
        # Repli si le catalogue n'a pas l'effet 93 mais le nom le dit.
        if not self.life_steal and "absorb" in self.name.lower():
            self.life_steal = True

    @property
    def offensive(self):
        return self.category == "ATTACK"

    def __repr__(self):
        return f"<sort {self.id} niv{self.level} {self.pa}PA {self.range_min}-{self.range_max}>"


def load_catalog():
    """Catalogue mis en cache sur disque : (id, niveau) -> Spell.

    Exposé hors de la classe pour que l'onglet Farming puisse proposer la liste
    des sorts même jeu fermé — sinon il faudrait être connecté et en combat
    pour pouvoir régler quoi que ce soit."""
    out = {}
    try:
        with open(CATALOG_FILE, encoding="utf-8") as f:
            for fields in json.load(f).values():
                sp = Spell(fields)
                out[(sp.id, sp.level)] = sp
    except (OSError, ValueError, IndexError):
        pass
    return out


class Fighter:
    """Un combattant tel que décrit par GTM : id;?;PV;PA;PM;cellule;;PVmax;%"""

    def __init__(self, blob):
        f = blob.split(";")
        self.id = f[0]
        self.hp = int(f[2])
        self.pa = int(f[3])
        self.pm = int(f[4])
        self.cell = int(f[5])
        # PVmax (champ 7) : sert a reperer le boss (plus gros PV) et les
        # Pougnettes (4700 pile) en mode Obsidiantre. Repli sur hp si absent.
        try:
            self.pvmax = int(f[7]) if len(f) > 7 and f[7] else self.hp
        except ValueError:
            self.pvmax = self.hp

    @property
    def is_monster(self):
        # Les monstres portent un identifiant négatif ; les personnages non.
        return self.id.startswith("-")


class CombatAI:
    def __init__(self, session, say, stats=None):
        self.s = session
        self.say = say
        self.char_id = None
        # Réglages de l'onglet Farming (sorts choisis, buffs, vitesse...). Lus à
        # chaque tour, donc un changement dans l'interface s'applique au combat
        # suivant sans redémarrer. None = valeurs par défaut.
        self.stats = stats

        self.catalog = {}      # (id, niveau) -> Spell
        self.levels = {}       # id -> niveau appris
        self.fighters = {}     # id -> Fighter
        self.pa = 0
        self.pm = 0
        self.casts = {}        # id de sort -> nombre de lancers ce tour
        self.playing = False
        self.active = False    # un combat est réellement en cours
        self.gmap = None       # carte courante, pour le calcul de zone
        # Script de combat fixe (ex. Kralamoure Géant) : une liste d'actions par
        # tour. Si défini, il remplace l'IA générique. None = IA normale.
        self.script = None
        self.script_step = 0
        # Capture d'âmes (sort 413) : buff lancé une fois par combat, tôt (avant
        # de tuer le dernier mob) pour capturer les âmes des vaincus.
        self.capture_souls = False
        # Observateur + capture : le bot ne joue pas le tour, il lance seulement
        # Capture d'âmes (le joueur joue lui-même). Positionné par bot.py.
        self.capture_only = False
        # Un vrai boss de donjon est-il présent dans ce combat ? Renseigné en
        # lisant le paquet GM d'ouverture (template du mob vs boss_definitions).
        # Conditionne la Capture d'âmes : on ne la lance que sur les vrais boss.
        self.boss_in_fight = False
        # id de combat -> template du monstre (lu dans le GM). Sert à marquer le
        # boss sur la grille de l'assistant de combat.
        self.fighter_templates = {}
        # Confusion du Comte Harebourg : quarts de tour horaires appliqués à la
        # visée (0 = aucune). L'assistant s'en sert pour compenser la rotation.
        self.confusion_turns = 0
        # Comte Harebourg : id de combat du boss + nombre de ses tours joués
        # (parité = fenêtre de déblocage Gousset).
        self.comte_id = None
        self.comte_turns = 0
        self._turn_no = 0          # numéro de tour dans le combat courant
        self._soul_last_turn = -10  # tour du dernier lancer de Capture d'âmes
        self._no_spell_warned = False   # sélection inadaptée déjà signalée
        self._nileza_pack_warned = False  # pack multi-Nileza déjà signalé
        self._heal_prio = False         # priorité vol de vie (hystérésis PV)
        self._load_catalog()

    # ── lecture du flux ──────────────────────────────────────────────────────

    def on_character(self, char_id):
        """Personnage sélectionné (paquet ASK).

        Changer de personnage peut changer de classe : on oublie les sorts du
        précédent, sinon ils resteraient candidats. Le catalogue, lui, peut
        rester — un sort n'est retenu que s'il figure aussi dans les niveaux
        appris du personnage courant (voir _spell)."""
        if self.char_id is not None and self.char_id != char_id:
            self.levels.clear()
            self._no_spell_warned = False
        self.char_id = char_id

    def on_packet(self, msg):
        if msg.startswith("ST"):
            self._parse_catalog(msg[2:])

        elif msg.startswith("SL"):
            for entry in msg[2:].split(";"):
                bits = entry.split("~")
                if len(bits) >= 2 and bits[0].isdigit():
                    self.levels[int(bits[0])] = int(bits[1])

        elif msg.startswith("GM|"):
            self._read_fight_gm(msg)

        elif msg.startswith("GTM|"):
            for blob in msg.split("|")[1:]:
                if blob and ";" in blob:
                    try:
                        f = Fighter(blob)
                        self.fighters[f.id] = f
                    except (ValueError, IndexError):
                        pass

        elif msg.startswith("GTS") and self.char_id:
            # GTS<id>|<durée>|<numéro de tour>
            who = msg[3:].split("|")[0]
            if who == self.comte_id:
                # Un tour du Comte de plus : la parité conditionne la fenêtre
                # de déblocage Gousset (déblocable quand le nb de ses tours est impair).
                self.comte_turns += 1
            if who == self.char_id and not self.playing:
                self.playing = True
                asyncio.create_task(self._play_turn())

        elif msg.startswith("GA0;1;"):
            # Déplacement en combat : GA0;1;<id>;<chemin>. La dernière case du
            # chemin est l'arrivée. Sans ça, la position d'un combattant (dont la
            # mienne) restait figée à sa case de début de tour — l'assistant
            # calculait alors la portée depuis la mauvaise case.
            parts = msg.split(";")
            if len(parts) >= 4 and parts[3]:
                f = self.fighters.get(parts[2])
                if f is not None:
                    from protocol import path_decode
                    steps = path_decode(parts[3])
                    if steps:
                        f.cell = steps[-1][1]

        elif msg.startswith("GA;103;"):
            # Mort d'un combattant.
            dead = msg.split(";")[2]
            self.fighters.pop(dead, None)

        elif msg.startswith("cMK") and "otation" in msg:
            self._read_confusion(msg)

    def _read_confusion(self, msg):
        """Comte Harebourg — Confusion : le serveur fait pivoter la case ciblée
        autour du lanceur. On lit la rotation annoncée dans le chat pour que
        l'assistant indique où CLIQUER (case déjà compensée). "90° Horaire" = 1
        quart, "180°" = 2, "90° Anti-Horaire" = 3, "Aucune" = 0. Messages :
        "[Confusion] Rotation actuelle : X" et "...rotation de <nom> passe à X"."""
        if "otation actuelle" not in msg and "passe" not in msg:
            return
        low = msg.lower()
        if "anti" in low:
            self.confusion_turns = 3
        elif "180" in low:
            self.confusion_turns = 2
        elif "90" in low:
            self.confusion_turns = 1
        elif "aucune" in low:
            self.confusion_turns = 0

    def _load_catalog(self):
        self.catalog.update(load_catalog())
        if self.catalog:
            self.say(f"catalogue de sorts repris du cache "
                     f"({len(self.catalog)} entrees)")

    def _save_catalog(self, raw):
        try:
            os.makedirs(os.path.dirname(CATALOG_FILE), exist_ok=True)
            with open(CATALOG_FILE, "w", encoding="utf-8") as f:
                json.dump(raw, f)
        except OSError:
            pass

    def _parse_catalog(self, payload):
        raw = {}
        for entry in payload.split(";"):
            fields = entry.split("~")
            if len(fields) > ST_CATEGORY and fields[0].isdigit():
                try:
                    sp = Spell(fields)
                    self.catalog[(sp.id, sp.level)] = sp
                    raw[f"{sp.id}-{sp.level}"] = fields
                except (ValueError, IndexError):
                    pass
        if raw:
            self._save_catalog(raw)

    # ── décision ─────────────────────────────────────────────────────────────

    def _setting(self, name, default):
        """Réglage de l'onglet Farming, ou la valeur par défaut."""
        if self.stats is None:
            return default
        value = getattr(self.stats, name, None)
        return default if value is None else value

    def _spell(self, spell_id):
        """Le sort au niveau réellement appris, ou None s'il ne l'est pas.

        Repli sur le niveau 1 quand le catalogue ne contient pas l'entrée du
        niveau exact : sinon un sort monté d'un cran disparaissait purement et
        simplement de l'arsenal, sans le moindre message."""
        level = self.levels.get(spell_id)
        if level is None:
            return None
        return (self.catalog.get((spell_id, level))
                or self.catalog.get((spell_id, 1)))

    def _hp_ratio(self):
        """PV courants / PVmax du perso, ou 1.0 si inconnu."""
        me = self.fighters.get(self.char_id)
        if me is None or me.pvmax <= 0:
            return 1.0
        return max(0.0, min(1.0, me.hp / me.pvmax))

    def _update_heal_prio(self):
        """Active la priorité vol de vie sous 50 % PV, la coupe au-dessus de 80 %.

        Hystérésis : sans ça, un seul Absorbante qui remonte juste au-dessus
        de 50 % ferait retomber sur Explosive, puis redescendre, etc."""
        ratio = self._hp_ratio()
        was = self._heal_prio
        if self._heal_prio:
            if ratio >= HEAL_EXIT_RATIO:
                self._heal_prio = False
        elif ratio <= HEAL_ENTER_RATIO:
            self._heal_prio = True
        if self._heal_prio and not was:
            pct = int(ratio * 100)
            self.say(f"PV bas ({pct} %) -> priorité vol de vie (Absorbante)")
        elif was and not self._heal_prio:
            self.say(f"PV rétablis ({int(ratio * 100)} %) -> priorité d'attaque")
        return self._heal_prio

    def _plan(self):
        """Sorts à tenter, dans l'ordre de priorité.

        La liste vient de l'onglet Farming : le joueur choisit ses sorts et leur
        ordre, on le suit à la lettre. Sans choix, on prend tous les sorts
        offensifs appris, les plus chers en PA d'abord — un coût élevé est le
        seul indice de puissance dont on dispose sans interroger le serveur.

        Repli automatique si AUCUN sort choisi n'appartient au personnage
        connecté : la sélection est enregistrée pour l'installation, pas par
        personnage, donc changer de perso (ou de classe) la rendait caduque. Sans
        ce repli le bot passait tous ses tours sans rien lancer, en silence.

        Sous 50 % PV : les sorts à vol de vie (Absorbante…) passent en tête,
        le reste garde l'ordre choisi — dès que le plafond de lancers du sort
        de soin est atteint, on reprend l'attaque normalement.
        """
        chosen = self._setting("combat_spells", [])
        if chosen:
            out = [sp for sp in (self._spell(sid) for sid in chosen)
                   if sp is not None]
            if out:
                return self._prioritize_heals(out)
            if not self._no_spell_warned:
                self._no_spell_warned = True
                self.say("aucun sort choisi n'appartient à ce personnage "
                         "-> je reprends le choix automatique (revois la liste "
                         "dans l'onglet Farming)")
        out = [sp for sp in (self._spell(sid) for sid in self.levels)
               if sp is not None and sp.offensive]
        out.sort(key=lambda s: -s.pa)
        return self._prioritize_heals(out)

    def _prioritize_heals(self, spells):
        """Remonte les sorts à vol de vie en tête si les PV sont bas."""
        if not self._update_heal_prio():
            return spells
        heals = [sp for sp in spells if sp.life_steal]
        if not heals:
            return spells
        rest = [sp for sp in spells if not sp.life_steal]
        return heals + rest

    def _castable(self, spell):
        """Assez de PA et plafond de lancers du tour non atteint."""
        return (spell.pa <= self.pa
                and (spell.max_per_turn == 0
                     or self.casts.get(spell.id, 0) < spell.max_per_turn))

    def _zone_hits(self, cell, radius, enemies):
        return sum(1 for e in enemies
                   if losrange.distance(cell, e.cell) <= radius)

    def zone_cells_ranked(self, spell, enemies, gmap=None):
        """Cases couvrant >=2 ennemis, triées par nombre d'ennemis touchés.

        Le rayon est mesuré avec la distance Dofus (|dx|+|dy| en repère de
        grille, cf. losrange), la même que le serveur : l'ancienne version
        additionnait des indices de cellule (+-14/+-15), ce qui débordait en
        biais sur les bords de carte."""
        if spell.zone_radius < 1 or len(enemies) < 2 or self.gmap is None:
            return []
        radius = spell.zone_radius
        if spell.free_cell:
            # Toute case dont la zone peut couvrir au moins un ennemi.
            candidates = {c for c in range(len(self.gmap))
                          if any(losrange.distance(c, e.cell) <= radius
                                 for e in enemies)}
        else:
            # Le sort exige une cible : on se limite aux cases occupées.
            candidates = {e.cell for e in enemies}
        scored = [(c, self._zone_hits(c, radius, enemies)) for c in candidates]
        scored = [t for t in scored if t[1] >= 2]
        scored.sort(key=lambda t: -t[1])
        return scored

    def _landing_cells(self, spell, from_cell, occupied):
        """Cases où ce sort peut réellement atterrir depuis `from_cell` :
        portée, ligne de vue et règle « cellules libres » du sort, calculées en
        local avec les mêmes règles que le serveur (losrange)."""
        return set(losrange.valid_target_cells(
            self.gmap, from_cell, spell.range_min, spell.range_max,
            spell.los, spell.free_cell, occupied,
            placement=getattr(spell, "summon", False)))

    def _is_nileza(self, fighter):
        return self.fighter_templates.get(fighter.id) == NILEZA_TEMPLATE

    def _nilezas(self):
        return [f for f in self._enemies() if self._is_nileza(f)]

    def _nileza_adjacent_count(self, nileza):
        """Autres Nileza collés (dist 1) — atterrir là = pack mortel."""
        return sum(
            1 for o in self._nilezas()
            if o.id != nileza.id
            and losrange.distance(nileza.cell, o.cell) == 1)

    def _liqueur_active(self):
        """Liqueur de Fée Ling : active 2 tours sur 3 (tours 1-2, 4-5, …)."""
        return self._turn_no % 3 != 0

    def _can_hit_nileza(self, from_cell, nileza):
        """True si taper ce Nileza depuis from_cell ne déclenche pas un OS.

        Uniquement en mêlée (dist 1) et hors Liqueur. À distance, Ogavodra
        swap + Molalité (~700k) — même sur un Nileza « isolé ». Les logs du
        Labo confirment que la frappe à PO est mortelle ; on ne tente plus."""
        d = losrange.distance(from_cell, nileza.cell)
        if d <= NILEZA_MELEE_DIST:
            return not self._liqueur_active()
        return False

    def _nileza_forbidden_cells(self, from_cell):
        """Cases Nileza qu'on ne doit pas blesser depuis from_cell."""
        return {n.cell for n in self._nilezas()
                if not self._can_hit_nileza(from_cell, n)}

    def _splash_hits_forbidden(self, cell, spell, forbidden):
        """True si atterrir sur `cell` blesse un Nileza interdit (cible ou zone).

        Bug réel (log 13:20) : Explosive mono sur escorte 298, zone rayon 2
        → Nileza en 328 (dist 2) → Ogavodra. L'ancien code ne testait le
        splash que pour les tirs « zone classés », pas pour le mono-cible."""
        if cell in forbidden:
            return True
        radius = spell.zone_radius
        if radius < 1 or not forbidden:
            return False
        return any(losrange.distance(cell, fc) <= radius for fc in forbidden)

    def _hittable_enemies(self, from_cell):
        """Ennemis qu'on a le droit de viser depuis from_cell.

        Escortes d'abord (pas de swap), puis Nileza en mêlée hors Liqueur.
        À distance on ne vise jamais un Nileza."""
        enemies = self._enemies()
        nilezas = [e for e in enemies if self._is_nileza(e)]
        if not nilezas:
            return enemies

        others = [e for e in enemies if not self._is_nileza(e)]
        safe_n = [n for n in nilezas if self._can_hit_nileza(from_cell, n)]
        if nilezas and not safe_n and not self._nileza_pack_warned:
            self._nileza_pack_warned = True
            self.say("Nileza : pas de tir à distance (Ogavodra/Molalité) — "
                     "escortes d'abord, mêlée hors Liqueur, ou j'attends")

        # Achève d'abord les plus bas PV dans chaque groupe.
        others.sort(key=lambda f: f.hp)
        safe_n.sort(key=lambda f: f.hp)
        return others + safe_n

    def _aim(self, spell, enemies, from_cell, occupied):
        """(sort, case, ennemis touchés) pour ce sort depuis `from_cell`, ou
        None si rien de valide.

        La zone passe avant le mono-cible dès qu'elle touche au moins deux
        ennemis. À défaut, on achève l'ennemi le plus bas en PV : un mob mort
        ne riposte pas, ce qui vaut mieux que d'étaler les dégâts.

        Avec Nileza : `enemies` est filtré, et toute atterrissage (zone ou
        mono) dont le splash touche un Nileza interdit est rejetée."""
        landings = self._landing_cells(spell, from_cell, occupied)
        if not landings:
            return None
        forbidden = self._nileza_forbidden_cells(from_cell)
        if spell.zone_radius >= 1 and len(enemies) >= 2:
            for cell, hits in self.zone_cells_ranked(spell, enemies):
                if cell not in landings:
                    continue
                if self._splash_hits_forbidden(cell, spell, forbidden):
                    continue
                return (spell, cell, hits)
        for enemy in enemies:   # déjà ordonnés par _hittable_enemies
            if enemy.cell not in landings:
                continue
            if self._splash_hits_forbidden(enemy.cell, spell, forbidden):
                continue
            return (spell, enemy.cell, 1)
        return None

    def _next_action(self, from_cell=None):
        """Prochain sort à lancer, ou None. Aucun paquet n'est émis pour
        décider : tout est calculé sur la carte et l'état des combattants."""
        me = self.fighters.get(self.char_id)
        if me is None or self.gmap is None:
            return None
        cell = me.cell if from_cell is None else from_cell
        enemies = self._hittable_enemies(cell)
        if not enemies:
            return None
        occupied = {f.cell for f in self.fighters.values() if f.hp > 0}
        occupied.discard(me.cell)
        occupied.add(cell)
        for spell in self._plan():
            if not self._castable(spell):
                continue
            hit = self._aim(spell, enemies, cell, occupied)
            if hit is not None:
                return hit
        return None

    def _enemies(self):
        return [f for f in self.fighters.values()
                if f.is_monster and f.id != self.char_id and f.hp > 0]

    def _read_fight_gm(self, msg):
        """Repère un vrai boss de donjon dans le paquet GM d'ouverture de combat.

        Format d'une entrée mob en combat : `+<cell>;<ori>;0;<id>;<template>;-2;
        <gfx>^<size>;<grade>;...` — le champ [4] est le TEMPLATE du monstre
        (getPacketsName renvoie le template id pour un mob côté serveur), le
        champ [5] vaut "-2" pour un monstre (les joueurs y ont leur classe). On
        compare ce template à la liste `boss_definitions` (DUNGEON_BOSS_IDS)."""
        for entry in msg.split("|")[1:]:
            if not entry.startswith("+"):
                continue
            fields = entry[1:].split(";")
            if len(fields) > 5 and fields[5] == "-2":
                try:
                    template = int(fields[4])
                    self.fighter_templates[fields[3]] = template
                    if template == 31077:      # Comte Harebourg
                        self.comte_id = fields[3]
                    if template in DUNGEON_BOSS_IDS:
                        if not self.boss_in_fight:
                            self.say("boss de donjon détecté — Capture d'âmes armée")
                        self.boss_in_fight = True
                except ValueError:
                    pass

    async def _maybe_cast_capture(self, my_cell):
        """Lance Capture d'âmes (413) en début de tour, uniquement si un VRAI
        boss de donjon est présent (template reconnu via boss_definitions dans
        le GM d'ouverture — voir _read_fight_gm). Le type de monstre n'étant pas
        dans GTM, l'ancien filtre par PVmax ratait les boss < 80k et se faussait
        quand le PVmax manquait ; le template est fiable et sans collision.
        Relancée toutes les 2 tours (l'effet dure 2 tours). Sort ignoré par le
        serveur s'il n'est pas possédé."""
        if not (self.capture_souls and my_cell is not None and self.boss_in_fight):
            return
        if self._turn_no - self._soul_last_turn < SOUL_CAPTURE_REFRESH:
            return
        sp = self.catalog.get(
            (SOUL_CAPTURE_SPELL, self.levels.get(SOUL_CAPTURE_SPELL, 1)))
        cost = sp.pa if sp else 2
        if self.pa < cost:
            return
        self._soul_last_turn = self._turn_no
        self.say(f"capture d'âmes ({SOUL_CAPTURE_SPELL}) — reste actif 2 tours")
        self.s.to_server(f"GA300{SOUL_CAPTURE_SPELL};{my_cell}")
        self.pa -= cost
        await asyncio.sleep(DELAY_BETWEEN_CASTS)

    async def _cast_buffs(self, my_cell):
        """Buffs choisis dans l'onglet Farming, lancés sur soi en début de tour.

        Ils ne visent personne d'autre : la case est la nôtre, donc rien à
        calculer. Un seul lancer par tour et par sort."""
        for spell_id in self._setting("combat_buffs", []):
            sp = self._spell(spell_id)
            if sp is None or my_cell is None or sp.pa > self.pa:
                continue
            if not self.active:
                return
            self.say(f"buff {sp.name} ({spell_id}) sur soi — {self.pa} PA")
            self.s.to_server(f"GA300{spell_id};{my_cell}")
            self.pa -= sp.pa
            self.casts[spell_id] = self.casts.get(spell_id, 0) + 1
            await asyncio.sleep(self._cast_delay())

    def _cast_delay(self):
        """Temps entre deux actions. Réglable dans l'onglet Farming : assez
        court pour enchaîner, jamais nul (un vrai client met au moins le temps
        du clic)."""
        try:
            delay = float(self._setting("combat_delay", DELAY_BETWEEN_CASTS))
        except (TypeError, ValueError):
            delay = DELAY_BETWEEN_CASTS
        return max(0.1, min(2.0, delay))

    def _approach(self):
        """Case où se déplacer pour pouvoir frapper, et le chemin pour y aller.

        Renvoie (case, chemin) ou None. On garde la case la moins coûteuse en PM
        depuis laquelle le sort le plus prioritaire a une cible ; à défaut, on se
        contente de réduire la distance à l'ennemi le plus proche pour être en
        position au tour suivant.

        Nileza : on ne se place JAMAIS à dist 2 d'un Nileza pour « se
        rapprocher » (case suicide Molalité). Si le pack est collé, on vise
        une case de mêlée (dist 1) hors Liqueur, sinon on attend."""
        me = self.fighters.get(self.char_id)
        enemies = self._enemies()
        if me is None or not enemies or self.gmap is None or me.pm <= 0:
            return None
        blocked = {f.cell for f in self.fighters.values() if f.cell != me.cell}
        came, dist = self._reachable(me.cell, me.pm, blocked)
        occupied = {f.cell for f in self.fighters.values() if f.hp > 0}
        spells = [sp for sp in self._plan() if self._castable(sp)]

        best = None
        for cell, steps in dist.items():
            if cell == me.cell:
                continue
            # Case à dist 2 d'un Nileza = piège : on ne s'y arrête pas.
            if any(self._is_nileza(n)
                   and losrange.distance(cell, n.cell) == NILEZA_MOLALITY_RADIUS
                   for n in enemies):
                continue
            occ = (occupied - {me.cell}) | {cell}
            hittable = self._hittable_enemies(cell)
            if not hittable:
                continue
            for rank, spell in enumerate(spells):
                if self._aim(spell, hittable, cell, occ) is None:
                    continue
                key = (rank, steps)
                if best is None or key < best[0]:
                    best = (key, cell)
                break
        if best is None:
            # Aucune case de tir : se placer pour le prochain coup sûr.
            nilezas = self._nilezas()
            if nilezas and not self._liqueur_active():
                # Fenêtre mêlée : coller un Nileza (pack ou pas — pas de swap).
                target = min(nilezas, key=lambda n: (
                    self._nileza_adjacent_count(n),
                    losrange.distance(me.cell, n.cell)))
                goal_dist = NILEZA_MELEE_DIST
            elif nilezas:
                # Sous Liqueur : viser un isolé à >2 PO, sinon rester loin.
                isolated = [n for n in nilezas
                            if self._nileza_adjacent_count(n) == 0]
                if not isolated:
                    return None
                target = min(isolated,
                             key=lambda n: losrange.distance(me.cell, n.cell))
                goal_dist = NILEZA_MOLALITY_RADIUS + 1
            else:
                target = min(enemies,
                             key=lambda e: losrange.distance(me.cell, e.cell))
                goal_dist = 1
            scored = []
            for c, steps in dist.items():
                if c == me.cell:
                    continue
                d = losrange.distance(c, target.cell)
                if any(self._is_nileza(n)
                       and losrange.distance(c, n.cell) == NILEZA_MOLALITY_RADIUS
                       for n in enemies):
                    continue
                # Plus on est proche de la distance-cible, mieux c'est.
                scored.append((abs(d - goal_dist), d if d >= goal_dist else 99,
                               steps, c))
            if not scored:
                return None
            best = (None, min(scored)[3])
        return best[1], self._path(came, best[1])

    @staticmethod
    def _path(came, cell):
        """Chemin reconstruit depuis l'arbre de parcours de _reachable."""
        path = []
        cur = cell
        while cur is not None:
            path.append(cur)
            cur = came[cur]
        path.reverse()
        return path

    async def _play_turn(self):
        deadline = asyncio.get_running_loop().time() + TURN_DEADLINE
        self.casts.clear()
        total = 0
        me = self.fighters.get(self.char_id)
        self.pa = me.pa if me else 0
        self.pm = me.pm if me else 0
        my_cell = me.cell if me else None
        self.say(f"tour de combat — {self.pa} PA, {self.pm} PM, "
                 f"{len(self._enemies())} ennemi(s)")

        self._turn_no += 1

        # Mode Observateur + Capture d'âmes : le bot ne joue pas le tour (le
        # joueur s'en charge), il lance seulement Capture d'âmes puis rend la
        # main — surtout PAS de Gt (c'est le joueur qui finit son tour).
        if self.capture_only:
            try:
                await self._maybe_cast_capture(my_cell)
            finally:
                self.playing = False
            return

        try:
            await asyncio.sleep(DELAY_BEFORE_TURN)
            await self._maybe_cast_capture(my_cell)
            # Script fixe (ex. Kralamoure) : on rejoue la séquence tour par tour
            # et on saute l'IA générique.
            if self.script is not None:
                await self._play_script()
                return
            await self._cast_buffs(my_cell)
            moves = 0
            while asyncio.get_running_loop().time() < deadline and self.active:
                action = self._next_action()
                if action is None:
                    # Rien à portée : se replacer puis réessayer. Plusieurs
                    # fois si besoin (ex. Explosive épuisée, il faut un pas
                    # pour enchaîner Magique) — plafonné pour éviter une boucle.
                    if (moves >= MAX_MOVES_PER_TURN
                            or not self._setting("combat_move", True)):
                        if self.pa > 0:
                            self.say(f"plus de cible joignable — {self.pa} PA "
                                     f"restants, je passe")
                        break
                    if not await self._step_closer():
                        if self.pa > 0:
                            self.say(f"aucun déplacement utile — {self.pa} PA "
                                     f"restants, je passe")
                        break
                    moves += 1
                    continue
                spell, cell, hits = action
                zone = f", {hits} ennemis" if hits > 1 else ""
                self.say(f"{spell.name} ({spell.id}) sur cellule {cell} "
                         f"— {spell.pa} PA{zone}, {self.pa} PA avant lancer")
                if not self.active:
                    break
                self.s.to_server(f"GA300{spell.id};{cell}")
                # Les PA sont décomptés ici, immédiatement, et non en attendant
                # l'accusé du serveur : si celui-ci tarde ou se perd, l'IA
                # croirait ses PA intacts et enchaînerait les lancers. La vraie
                # valeur est resynchronisée par GTM au tour suivant.
                self.pa -= spell.pa
                self.casts[spell.id] = self.casts.get(spell.id, 0) + 1
                total += 1
                if total >= MAX_CASTS_PER_TURN:
                    self.say("plafond de lancers atteint -> je passe")
                    break
                await asyncio.sleep(self._cast_delay())
        except Exception as e:
            # En cas d'imprévu on passe le tour : un combat perdu lentement
            # vaut mieux qu'un bot qui s'entête et fait n'importe quoi.
            self.say(f"erreur en combat ({e!r}) -> je passe le tour")
        finally:
            await asyncio.sleep(DELAY_BEFORE_END_TURN)
            # Le dernier sort tue souvent le monstre : le combat se termine
            # pendant qu'on attend. Envoyer un Gt après coup serait un paquet
            # qu'aucun client ne produit.
            if self.active:
                self.s.to_server("Gt")
            self.playing = False

    async def _play_script(self):
        """Rejoue un script de combat fixe (cases figées) : un tour = une liste
        d'actions. Une action est soit ("move", chemin_encodé) soit
        (id_sort, cellule). On avance d'un cran par tour ; le Gt final est
        envoyé par le bloc `finally` de _play_turn.

        Sûr par construction : on ne lance que des sorts du catalogue (le
        serveur ignore un GA300 d'un sort non possédé) et on ne rejoue qu'un
        déplacement réellement capturé — rien qu'un vrai client ne produirait."""
        step = self.script_step
        actions = self.script[step] if step < len(self.script) else []
        self.say(f"script Kralamoure — tour {step + 1}/{len(self.script)} "
                 f"({len(actions)} action(s))")
        for act in actions:
            if not self.active:
                break
            if act[0] == "move_tr":
                await self._move_topright()
            else:
                spell_id, cell = act
                self.say(f"script: sort {spell_id} sur {cell}")
                self.s.to_server(f"GA300{spell_id};{cell}")
                await asyncio.sleep(DELAY_BETWEEN_CASTS)
        self.script_step += 1

    async def _combat_move(self, encoded):
        """Déplacement en combat : GA001 PUIS confirmation GKK1. Sans ce GKK1,
        le serveur ne fige pas le mouvement (le client injecté envoie un GKK0
        qui ne le valide pas) et le perso reste sur place — d'où l'Araknée
        poussée dans le vide. Même schéma que la récolte (qui, elle, marche)."""
        # On envoie SEULEMENT le GA001 : c'est le CLIENT qui finalise le
        # déplacement en répondant au GAF du serveur par son GKK (cf. son code
        # onActionsFinish). Bloquer ce GKK ou en envoyer un nous-mêmes cassait
        # justement la finalisation (le serveur accordait le move mais le perso
        # ne bougeait pas). On laisse donc le handshake naturel opérer, comme
        # hors combat.
        self.s.to_server("GA001" + encoded)
        await asyncio.sleep(DELAY_AFTER_MOVE)

    async def _step_closer(self):
        """Se replace pour pouvoir frapper. Vrai si on a bougé.

        La position est mise à jour tout de suite, sans attendre l'accusé du
        serveur : le sort qui suit est visé depuis la case d'arrivée."""
        from gamemap import compress_path
        plan = self._approach()
        if plan is None:
            self.say("rien à portée et nulle part où aller -> je passe")
            return False
        cell, path = plan
        encoded = compress_path(path)
        if not encoded:
            return False
        steps = len(path) - 1
        me = self.fighters.get(self.char_id)
        self.say(f"aucune cible à portée -> déplacement vers {cell} "
                 f"({steps} pas, {self.pm} PM)")
        await self._combat_move(encoded)
        if me is not None:
            me.cell = cell
            me.pm = max(0, me.pm - steps)
        self.pm = max(0, self.pm - steps)
        return True

    def _reachable(self, start, pm, blocked):
        """Cellules praticables atteignables en au plus `pm` pas : renvoie
        (parents, nombre de pas). BFS sur les vraies cases marchables de la
        carte, en évitant les cases occupées."""
        came = {start: None}
        dist = {start: 0}
        queue = [start]
        i = 0
        while i < len(queue):
            cur = queue[i]
            i += 1
            if dist[cur] >= pm:
                continue
            for _, nb in self.gmap.neighbours(cur):
                if nb in came or nb in blocked or not self.gmap.walkable(nb):
                    continue
                came[nb] = cur
                dist[nb] = dist[cur] + 1
                queue.append(nb)
        return came, dist

    async def _move_topright(self):
        """Va le plus loin possible vers le haut-droite (NE), sans dépasser les
        PM et en restant sur des cases libres. Remplace un déplacement figé :
        c'est calculé depuis la position réelle, donc jamais injoignable.

        Sûr : on n'envoie qu'un GA001 vers une case réellement atteignable par
        un chemin marchable — exactement ce qu'un clic du vrai client produit."""
        from gamemap import compress_path
        me = self.fighters.get(self.char_id)
        if me is None or self.gmap is None or me.pm <= 0:
            return
        blocked = {f.cell for f in self.fighters.values() if f.cell != me.cell}
        came, _ = self._reachable(me.cell, me.pm, blocked)
        # "Haut-droite" = colonne maximale, rangée minimale : score x - y.
        def score(cell):
            x, y = self.gmap.coords(cell)
            return x - y
        best = max(came, key=score)
        if best == me.cell:
            self.say("script: déjà au plus haut-droite atteignable")
            return
        path = self._path(came, best)
        encoded = compress_path(path)
        if not encoded:
            return
        self.say(f"script: déplacement haut-droite -> {best} "
                 f"({len(path) - 1} pas, {me.pm} PM)")
        await self._combat_move(encoded)

    def reset(self, active=False):
        self.fighters.clear()
        self.casts.clear()
        self.playing = False
        self.active = active
        # Le script de combat persiste (c'est un réglage de mode) mais son
        # compteur de tour repart à zéro à chaque nouveau combat.
        self.script_step = 0
        # Compteur de tours + Capture d'âmes à relancer dès le 1er tour.
        self._turn_no = 0
        self._soul_last_turn = -10
        # Nouveau combat : on ne sait pas encore si un boss est présent.
        self.boss_in_fight = False
        self.fighter_templates = {}
        self.confusion_turns = 0
        self.comte_id = None
        self.comte_turns = 0
        self._nileza_pack_warned = False
        self._heal_prio = False
