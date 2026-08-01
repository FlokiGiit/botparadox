"""
IA de combat.

Principe : ne rien coder en dur. Le serveur nous donne tout.

  ST   catalogue des sorts (coût en PA, portée, lancers par tour, catégorie)
  SL   niveau appris de chaque sort
  GTM  état des combattants (PV, PA, PM, cellule)
  ZDC  "cette cellule est-elle une cible valide pour ce sort ?"
  ZDM  la réponse, avec les dégâts réellement prévus sur cette cible

Les dégâts de ZDM tiennent compte de l'équipement et des caractéristiques,
là où ST ne donne que la valeur de base : on interroge donc le serveur plutôt
que de tenter de recalculer sa formule.

Les indices de champs de ST ont été validés en les comparant à la fiche de
sort affichée par le client (Flèche Explosive : 4 PA, portée 1-8, 2 lancers
par tour, ligne de vue requise, probabilité critique 2).
"""

import asyncio
import json
import os

# Découpage d'une entrée de ST.
ST_PA = 2
ST_RANGE_MIN = 3
ST_RANGE_MAX = 4
ST_MAX_PER_TURN = 11      # 0 = pas de limite
ST_FREE_CELL = 9
ST_ZONE = 16
ST_CATEGORY = 19

# Délais : assez courts pour ne pas perdre de temps, assez présents pour ne pas
# répondre en zéro milliseconde là où un humain met au moins le temps du clic.
DELAY_BEFORE_TURN = 0.2
DELAY_BETWEEN_CASTS = 0.3
DELAY_BEFORE_END_TURN = 0.1
# Attente après un déplacement de combat avant d'enchaîner un sort : le temps
# que le serveur enregistre la nouvelle position. Assez court pour rester
# rapide, assez long pour qu'un sort dépendant de la case d'arrivée passe.
DELAY_AFTER_MOVE = 0.9

# Un tour ne doit jamais durer indéfiniment : au pire on passe.
TURN_DEADLINE = 20.0
PROBE_TIMEOUT = 2.0

# Garde-fou indépendant du décompte de PA : même si la comptabilité dérive,
# on ne pourra jamais spammer les lancers.
MAX_CASTS_PER_TURN = 6

# Nombre de cases de zone sondees par tour (les meilleures d'abord) avant
# d'abandonner la zone pour du mono-cible. Assez pour trouver une case a
# portee quand la toute meilleure ne l'est pas, sans multiplier les allers-
# retours ZDC.
ZONE_PROBE_LIMIT = 6

# La valeur médiane observée dans les ZDC du vrai client.
ZDC_MODE = 2

# Sorts a privilegier, dans l'ordre. Ils passent avant les autres, mais ne les
# excluent pas : Fleche Explosive est limitee a 2 lancers par tour pour 4 PA,
# soit 8 PA sur les 19 disponibles — interdire le reste gaspillerait la moitie
# du tour. On force donc l'ordre, pas le choix.
#
# Necessaire parce que ZDM ne renvoie que le degat sur la cible visee : l'IA
# ne peut pas voir qu'un sort de zone en touche trois, et sous-estime donc
# systematiquement les zones face a un mono-cible qui frappe plus fort.
# Un numero absent du catalogue est simplement ignore : lister un sort
# qu'on ne possede pas encore n'a aucun effet, et il sera pris en compte
# automatiquement le jour ou le serveur l'annonce dans SL.
PREFER_SPELLS = [179]   # Fleche Explosive : zone en cercle, rayon 2

# Sorts a utiliser EXCLUSIVEMENT, si et seulement si le personnage les
# possede vraiment. Un sort absent du catalogue n'est jamais force : on
# retombe alors sur le choix normal. Sans ce repli, lister un sort non
# acquis ferait passer tous les tours sans rien lancer.
EXCLUSIVE_SPELLS = []

# Sorts lances sur soi en debut de tour, dans l'ordre. Un seul lancer par
# tour chacun. Mettre les numeros des buffs souhaites, par exemple :
#   180 = Maitrise de l'Arc (+dommages)   166 = Tir Puissant (+stats)
# Laisser vide desactive la fonction.
#
# A peser : ces sorts coutent 3 PA chacun. Sur des combats gagnes en un
# tour, 6 PA de buffs valent rarement les deux Fleches Explosives qu'ils
# remplacent — c'est surtout utile si les combats durent.
SELF_BUFFS = []

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
        zone = fields[ST_ZONE] if len(fields) > ST_ZONE else ""
        self.zone_radius = (ord(zone[1]) - ord("a")) if len(zone) >= 2 else 0

    @property
    def offensive(self):
        return self.category == "ATTACK"

    def __repr__(self):
        return f"<sort {self.id} niv{self.level} {self.pa}PA {self.range_min}-{self.range_max}>"


class Fighter:
    """Un combattant tel que décrit par GTM : id;?;PV;PA;PM;cellule;;PVmax;%"""

    def __init__(self, blob):
        f = blob.split(";")
        self.id = f[0]
        self.hp = int(f[2])
        self.pa = int(f[3])
        self.pm = int(f[4])
        self.cell = int(f[5])

    @property
    def is_monster(self):
        # Les monstres portent un identifiant négatif ; les personnages non.
        return self.id.startswith("-")


class CombatAI:
    def __init__(self, session, say):
        self.s = session
        self.say = say
        self.char_id = None

        self.catalog = {}      # (id, niveau) -> Spell
        self.levels = {}       # id -> niveau appris
        self.fighters = {}     # id -> Fighter
        self.pa = 0
        self.casts = {}        # id de sort -> nombre de lancers ce tour
        self.probes = {}       # (cellule, sort) -> Future
        self.playing = False
        self.active = False    # un combat est réellement en cours
        self.gmap = None       # carte courante, pour le calcul de zone
        # Script de combat fixe (ex. Kralamoure Géant) : une liste d'actions par
        # tour. Si défini, il remplace l'IA générique. None = IA normale.
        self.script = None
        self.script_step = 0
        self._load_catalog()

    # ── lecture du flux ──────────────────────────────────────────────────────

    def on_packet(self, msg):
        if msg.startswith("ST"):
            self._parse_catalog(msg[2:])

        elif msg.startswith("SL"):
            for entry in msg[2:].split(";"):
                bits = entry.split("~")
                if len(bits) >= 2 and bits[0].isdigit():
                    self.levels[int(bits[0])] = int(bits[1])

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
            if who == self.char_id and not self.playing:
                self.playing = True
                asyncio.create_task(self._play_turn())

        elif msg.startswith("ZDM|"):
            self._resolve_probe(msg)

        elif msg.startswith("GA;103;"):
            # Mort d'un combattant.
            dead = msg.split(";")[2]
            self.fighters.pop(dead, None)

    def _load_catalog(self):
        try:
            with open(CATALOG_FILE, encoding="utf-8") as f:
                for key, fields in json.load(f).items():
                    sp = Spell(fields)
                    self.catalog[(sp.id, sp.level)] = sp
            if self.catalog:
                self.say(f"catalogue de sorts repris du cache "
                         f"({len(self.catalog)} entrees)")
        except (OSError, ValueError, IndexError):
            pass

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

    def _resolve_probe(self, msg):
        # ZDM|<cellule>|<mode>|<sort>|<0 ou 1>[|<cible>;...;<dégâts>;...]
        parts = msg.split("|")
        if len(parts) < 5:
            return
        try:
            key = (int(parts[1]), int(parts[3]))
        except ValueError:
            return
        fut = self.probes.pop(key, None)
        if fut is None or fut.done():
            return

        if parts[4] != "1" or len(parts) < 6:
            fut.set_result(None)
            return
        # Les dégâts prévus commencent au 3e champ de la description de cible.
        bits = parts[5].split(";")
        try:
            fut.set_result(float(bits[2]))
        except (ValueError, IndexError):
            fut.set_result(0.0)

    # ── décision ─────────────────────────────────────────────────────────────

    def _my_spells(self):
        out = []
        # Exclusivite honoree seulement si le sort est reellement appris.
        # Repli desactive a la demande : l'exclusivite s'applique meme si le
        # sort n'est pas encore dans le catalogue. Tant qu'il n'y est pas,
        # l'IA n'a donc aucun sort a lancer et passe ses tours.
        # Pour retablir le repli, remettre les deux lignes ci-dessous :
        #   owned = [i for i in EXCLUSIVE_SPELLS
        #            if self.catalog.get((i, self.levels.get(i, 0)))]
        #   allowed = owned if owned else None
        owned = [i for i in EXCLUSIVE_SPELLS
                 if self.catalog.get((i, self.levels.get(i, 0)))]
        allowed = owned if owned else None
        for spell_id, level in self.levels.items():
            if allowed and spell_id not in allowed:
                continue
            sp = self.catalog.get((spell_id, level))
            if sp and sp.offensive:
                out.append(sp)
        return out

    @staticmethod
    def _priority(spell):
        """Rang de preference : les sorts imposes d'abord, puis les plus chers."""
        if spell.id in PREFER_SPELLS:
            return (0, PREFER_SPELLS.index(spell.id))
        return (1, -spell.pa)

    def _zone_cells(self, centre, radius, gmap):
        """Cellules couvertes par une zone circulaire, en pas diagonaux.

        On parcourt la grille au lieu de convertir en coordonnees : le pas
        diagonal EST l'unite de distance de Dofus, donc un simple parcours
        en largeur donne le rayon exact sans avoir a deriver de formule.
        """
        seen = {centre}
        edge = [centre]
        for _ in range(radius):
            nxt = []
            for cell in edge:
                for delta in (14, 15, -14, -15):
                    n = cell + delta
                    if 0 <= n < len(gmap) and n not in seen:
                        seen.add(n)
                        nxt.append(n)
            edge = nxt
        return seen

    def zone_cells_ranked(self, spell, enemies, gmap):
        """Cases couvrant >=2 ennemis, triees par nombre d'ennemis touches.

        ZDM ne renvoie que le degat sur la cible visee : le serveur ne nous
        dira jamais qu'une case vide en toucherait trois. On calcule donc le
        classement nous-memes, mais on rend TOUTES les bonnes cases (best
        d'abord) et non une seule : l'appelant sonde dans l'ordre et garde la
        premiere que le serveur accepte. C'est ce qui corrige le cas ou la
        meilleure case couvre 6 ennemis mais est hors de portee/LdV — le
        serveur la refusait et on retombait sur un sort mono-cible.
        """
        if spell.zone_radius < 1 or len(enemies) < 2 or gmap is None:
            return []
        occupied = {e.cell for e in enemies}
        if spell.free_cell:
            # Toutes les cases a portee de zone d'au moins un ennemi.
            candidates = set()
            for e in enemies:
                candidates |= self._zone_cells(e.cell, spell.zone_radius, gmap)
        else:
            # Le sort exige une cible : on se limite aux cases occupees.
            candidates = set(occupied)
        scored = []
        for cell in candidates:
            hits = len(self._zone_cells(cell, spell.zone_radius, gmap) & occupied)
            if hits >= 2:
                scored.append((cell, hits))
        scored.sort(key=lambda t: -t[1])
        return scored

    def _enemies(self):
        return [f for f in self.fighters.values()
                if f.is_monster and f.id != self.char_id and f.hp > 0]

    async def _probe(self, cell, spell_id):
        """Demande au serveur si la cellule est une cible valide."""
        key = (cell, spell_id)
        fut = asyncio.get_running_loop().create_future()
        self.probes[key] = fut
        self.s.to_server(f"ZDC|{cell}|{ZDC_MODE}|{spell_id}")
        try:
            return await asyncio.wait_for(fut, PROBE_TIMEOUT)
        except asyncio.TimeoutError:
            self.probes.pop(key, None)
            return None

    async def _best_action(self):
        """Meilleur couple (sort, cible) parmi ce qui est payable et validé."""
        enemies = self._enemies()
        if not enemies:
            return None

        candidates = [
            sp for sp in self._my_spells()
            if sp.pa <= self.pa
            and (sp.max_per_turn == 0
                 or self.casts.get(sp.id, 0) < sp.max_per_turn)
        ]
        candidates.sort(key=self._priority)

        best = None
        for sp in candidates:
            # Sort de zone et plusieurs ennemis : viser le point qui en couvre
            # le plus, quitte a cibler une case vide. Le serveur ne peut pas
            # nous souffler ce choix — ZDM ne parle que de la cible visee — mais
            # il garde le dernier mot puisque la case passe quand meme par ZDC.
            if sp.zone_radius >= 1 and len(enemies) >= 2:
                ranked = self.zone_cells_ranked(sp, enemies, self.gmap)
                if not ranked:
                    self.say(f"zone {sp.id} : aucune case ne couvre "
                             f"2 ennemis ou plus ({len(enemies)} en jeu, "
                             f"carte {'absente' if self.gmap is None else 'ok'})")
                else:
                    # On sonde les meilleures cases dans l'ordre et on garde la
                    # premiere que le serveur accepte : la meilleure case peut
                    # etre hors de portee/LdV, la suivante souvent non.
                    placed = None
                    for cell, hits in ranked[:ZONE_PROBE_LIMIT]:
                        damage = await self._probe(cell, sp.id)
                        if damage:
                            placed = (sp, cell, damage * hits)
                            self.say(f"zone {sp.id} : case {cell} couvre "
                                     f"{hits} ennemis -> acceptee")
                            break
                    if placed:
                        return placed
                    self.say(f"zone {sp.id} : {len(ranked)} case(s) couvrant 2+ "
                             f"toutes refusees (hors portee/LdV) -> mono-cible")

            for enemy in enemies:
                damage = await self._probe(enemy.cell, sp.id)
                if damage:
                    if best is None or damage > best[2]:
                        best = (sp, enemy.cell, damage)
            if best:
                # Inutile de sonder les sorts moins chers si celui-ci passe.
                break
        return best

    async def _play_turn(self):
        deadline = asyncio.get_running_loop().time() + TURN_DEADLINE
        self.casts.clear()
        total = 0
        me = self.fighters.get(self.char_id)
        self.pa = me.pa if me else 0
        my_cell = me.cell if me else None
        self.say(f"tour de combat — {self.pa} PA, "
                 f"{len(self._enemies())} ennemi(s)")

        try:
            await asyncio.sleep(DELAY_BEFORE_TURN)
            # Script fixe (ex. Kralamoure) : on rejoue la séquence tour par tour
            # et on saute l'IA générique.
            if self.script is not None:
                await self._play_script()
                return
            # Buffs sur soi d'abord : ils ne visent personne d'autre, donc
            # pas de sondage necessaire, la case est la notre.
            for spell_id in SELF_BUFFS:
                sp = self.catalog.get((spell_id, self.levels.get(spell_id, 1)))
                if sp is None or my_cell is None or sp.pa > self.pa:
                    continue
                if not self.active:
                    break
                self.say(f"buff {spell_id} sur soi ({self.pa} PA)")
                self.s.to_server(f"GA300{spell_id};{my_cell}")
                self.pa -= sp.pa
                self.casts[spell_id] = self.casts.get(spell_id, 0) + 1
                await asyncio.sleep(DELAY_BETWEEN_CASTS)
            while asyncio.get_running_loop().time() < deadline:
                action = await self._best_action()
                if action is None:
                    break
                spell, cell, damage = action
                self.say(f"sort {spell.id} sur cellule {cell} "
                         f"(~{damage:.0f} dégâts, {self.pa} PA restants)")
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
                await asyncio.sleep(DELAY_BETWEEN_CASTS)
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

    def _reachable(self, start, pm, blocked):
        """Cellules praticables atteignables en au plus `pm` pas, avec le
        parent de chacune (pour reconstruire le chemin). BFS sur les vraies
        cases marchables de la carte, en évitant les cases occupées."""
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
        return came

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
        came = self._reachable(me.cell, me.pm, blocked)
        # "Haut-droite" = colonne maximale, rangée minimale : score x - y.
        def score(cell):
            x, y = self.gmap.coords(cell)
            return x - y
        best = max(came, key=score)
        if best == me.cell:
            self.say("script: déjà au plus haut-droite atteignable")
            return
        # Reconstruit le chemin case par case, puis l'encode au format serveur.
        path = []
        cur = best
        while cur is not None:
            path.append(cur)
            cur = came[cur]
        path.reverse()
        encoded = compress_path(path)
        if not encoded:
            return
        self.say(f"script: déplacement haut-droite -> {best} "
                 f"({len(path) - 1} pas, {me.pm} PM)")
        self.s.to_server("GA001" + encoded)
        await asyncio.sleep(DELAY_AFTER_MOVE)

    def reset(self, active=False):
        self.fighters.clear()
        self.probes.clear()
        self.casts.clear()
        self.playing = False
        self.active = active
        # Le script de combat persiste (c'est un réglage de mode) mais son
        # compteur de tour repart à zéro à chaque nouveau combat.
        self.script_step = 0
