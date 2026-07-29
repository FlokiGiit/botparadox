"""
Bot de récolte — mode assisté.

Il observe le flux de jeu via le proxy, repère les ressources disponibles
annoncées par le serveur, calcule un chemin et injecte les paquets de
déplacement et de fauche.

Mode assisté : dès que le joueur agit lui-même (déplacement ou récolte lancée
depuis le client), le bot se met en pause. Il reprend après un délai de calme.
Comme les paquets du bot sont injectés directement vers le serveur, ils ne
passent jamais par le flux client->serveur : tout GA001/GA500 observé dans ce
flux vient donc forcément du joueur, sans ambiguïté possible.

Usage :  python bot.py
"""

import asyncio
import re
import time

import bridge_patch
import client_patch
import dashboard
import gamedata
import proxy
from combat import CombatAI
from gamemap import GameMap, compress_path

# Mettre à False pour observer les combats sans que le bot y touche.
AUTO_COMBAT = True

# Délai avant d'annoncer "prêt" au placement.
#
# L'epee affichee sur la carte EST la phase de placement : elle dure tant
# que tout le monde n'a pas valide, et GJK annonce 45 s de battement. Plus
# on tarde, plus elle reste. Attendre un signal du client allongeait donc
# precisement ce qu'on cherchait a raccourcir.
#
# Ce qui reste ici sert juste a laisser une fenetre pour reprendre la main.
# Analyse du code AS2 du client : la vue de combat se construit de facon
# asynchrone (attachMovie, Sequencer par sprite, CyclicExecutor). Le GS
# decharge le menu de placement et adresse des composants tout juste
# demandes. Valider en 0,27 s alors qu'un humain met ~3,9 s ne laisse pas
# le temps a cette construction d'aboutir.
#
# L'epee restera visible plus longtemps — c'est la phase de placement, elle
# dure ce qu'on met a valider. Ce qu'on cherche a corriger, c'est que le
# client bascule bien en vue de combat : deux choses differentes.
DELAY_BEFORE_READY = 2.0

# Outil de récolte. 45 = faucille (blé) — relevé dans `GA500402;45`.
SKILL_ID = 45

# Pause après une action du joueur, réarmée à chaque geste : le bot reprend
# donc ce délai après le dernier clic, pas après le premier.
PAUSE_AFTER_PLAYER_ACTION = 5.0

# Garde-fou : si le serveur ne confirme jamais une récolte, on se débloque
# plutôt que de rester figé indéfiniment.
# Une récolte dure ~10 s : au-delà de 15 s sans réponse, c'est que le paquet
# s'est perdu. Trente secondes rendaient chaque incident très visible.
HARVEST_TIMEOUT = 15.0

# Combien de temps on écarte une cellule que le serveur a refusée. Assez long
# pour ne pas y revenir en boucle, assez court pour la retenter après repop.
FAILED_COOLDOWN = 120.0

# Intervalle minimum entre deux demandes de resynchronisation de la carte.
RESYNC_INTERVAL = 20.0

# Fréquence du battement qui relance le bot quand aucun paquet ne le réveille.
TICK_INTERVAL = 2.0

# Delai minimum entre deux engagements. Le verrou qui compte est le test
# in_fight ; celui-ci ne couvre que la fenetre ou le serveur a lance le
# combat sans qu'on l'ait encore appris. Quelques centaines de
# millisecondes suffisent — 3 s rendaient l'enchainement poussif.
ENGAGE_COOLDOWN = 1.0

# Un engagement reste "en vol" pendant le trajet vers le groupe. Un simple
# delai ne suffit pas : il expire pendant la marche et le bot relance une
# attaque avant meme que le combat se soit ouvert.
ENGAGE_TIMEOUT = 10.0

# Delai au-dela duquel on considere la carte rétablie meme sans confirmation.
# Le GDF ne vient qu'apres un GI du client : en faire une condition stricte
# bloquait le bot pour toujours des que le client etait desynchronise.
MAP_READY_TIMEOUT = 2.5

# Paquets qui ne circulent QUE pendant un combat. Leur simple presence
# prouve l'etat, sans dependre d'avoir vu le debut ni la fin.
COMBAT_TRAFFIC = re.compile(r"^(GT[MSCFRL]|GP[be]|GA;(1|3)\d\d;)")

# Silence au-dela duquel on considere le combat termine. Le serveur parle
# plusieurs fois par seconde pendant un tour, donc 4 s est large.
COMBAT_IDLE = 4.0

# Démarrage de récolte, toutes variantes : GA0;501;... comme GA1;501;...
HARVEST_START = re.compile(r"^GA\d+;501;")

# Marge avant saturation des pods : en dessous, on arrête de récolter.
PODS_STOP_RATIO = 0.95

# Petit délai avant d'enchaîner, pour ne pas coller au millimètre le rythme
# d'un client humain.
DELAY_BETWEEN_HARVESTS = 1.2


class Brain:
    def __init__(self, session):
        self.s = session
        self.char_id = None
        self.pos = None
        self.gmap = None
        self.map_id = None
        self.resources = {}        # cellule -> état (1 dispo, 2 en cours, 3 vide)
        self.pods = (0, 1)
        self.busy_since = None     # instant d'envoi de la récolte en cours
        self.paused_until = 0.0
        self.client_sent_gkk1 = False
        self.last_blocked = None
        self.in_fight = False
        self.groups = {}         # cellule -> identifiant du groupe de monstres
        self.target = None       # cellule visée par la récolte en cours
        self.failed = {}         # cellule -> instant du dernier refus
        self.last_resync = 0.0
        self.last_engage = 0.0
        self.client_in_fight = False   # le client a bascule en vue combat
        self.last_combat_packet = 0.0
        self.engaging_since = None   # attaque envoyee, combat pas encore ouvert
        self.map_ready = False       # contenu de carte recu depuis le combat
        self.map_wait_since = 0.0
        self.bank_mode = False       # un echange de type banque est ouvert
        # stats en premier : CombatAI appelle say() des sa construction
        # (reprise du catalogue), et say() ecrit dans la trace.
        self.stats = dashboard.stats()
        self.combat = CombatAI(session, lambda t: self.say(t))
        self.combat_manual = False   # le joueur a pris la main sur ce combat
        self.stats.reset_session()   # nouvelle partie = bilan a zero
        self.stats.client_connected = True
        self.stats.event("info", "jeu connecté")
        # Sans ce battement le bot resterait bloqué après une pause : il n'agit
        # que sur réception d'un paquet, or si rien ne bouge sur la carte, rien
        # n'arrive et rien ne le réveille.
        self.ticker = asyncio.create_task(self._tick())

    async def _tick(self):
        while not self.s.server_writer.is_closing():
            await asyncio.sleep(TICK_INTERVAL)
            try:
                self._maybe_act()
            except Exception as e:
                self.say(f"battement : {e!r}")

    # ── journal ──────────────────────────────────────────────────────────────

    def say(self, text):
        self.s.log.note(f"BOT {text}")
        try:
            self.stats.log_line(text)
        except Exception:
            pass   # une trace perdue ne doit jamais couper la partie

    # ── entrée principale ────────────────────────────────────────────────────

    def on_packet(self, direction, msg, session):
        if direction == "C>S":
            self._from_player(msg)
        else:
            self._from_server(msg)

    def _from_player(self, msg):
        # Le joueur reprend la main : on s'efface.
        if msg.startswith("GA001") or msg.startswith("GA500"):
            self.paused_until = time.monotonic() + PAUSE_AFTER_PLAYER_ACTION
            self.say(f"joueur actif -> pause {PAUSE_AFTER_PLAYER_ACTION:.0f}s")
        elif msg.startswith("bR"):
            # Accuse de reception du client pendant la phase de placement.
            # Valider "Pret" avant lui laissait le combat demarrer sans
            # qu'il ait bascule en vue de combat : il restait sur la carte
            # avec une epee posee dessus.
            self.client_in_fight = True

        elif msg.startswith("GKK1"):
            # Le client a confirmé la fin d'animation lui-même : inutile de
            # doubler, le serveur refuserait la seconde confirmation.
            self.client_sent_gkk1 = True

        elif msg.startswith("GA300") or msg.rstrip() in ("Gt", "GR1"):
            # Le joueur joue le combat lui-même : l'IA se retire pour cette
            # bagarre, sans quoi on lancerait des sorts par-dessus lui.
            if self.in_fight and not self.combat_manual:
                self.combat_manual = True
                self.say("tu joues le combat -> IA de combat en retrait")

    def _from_server(self, msg):
        # Verite de terrain : tant que du trafic de combat circule, on est
        # en combat. Un GJK manque se rattrape au paquet suivant, et un GE
        # fantome ne fige plus rien. L'etat ne peut donc plus diverger.
        if COMBAT_TRAFFIC.match(msg):
            self.last_combat_packet = time.monotonic()
            if not self.in_fight:
                self.in_fight = True
                self.say("trafic de combat detecte -> etat resynchronise")

        if msg.startswith("GJK"):
            # Début de combat. Un vrai client ne peut rien récolter dans cet
            # état : continuer à envoyer des GA500 serait à la fois inutile et
            # très visible côté serveur.
            self.in_fight = True
            self.busy_since = None
            self.engaging_since = None   # l'engagement a abouti
            self.combat_manual = False
            self.client_in_fight = False
            self.combat.reset(active=True)
            self.stats.fight_start()
            self.say("combat détecté -> récolte en veille")
            # Mode observateur : on regarde le combat sans y toucher — donc pas
            # d'auto-"prêt" non plus (cohérent avec l'IA de combat plus bas).
            if AUTO_COMBAT and self.stats.enabled and self.stats.mode != "off":
                asyncio.create_task(self._announce_ready())

        elif msg.startswith("GE"):
            self.in_fight = False
            self.combat.reset()
            self.stats.fight_end()
            # Le combat déplace le personnage, et sa position réelle n'arrive
            # qu'au GM suivant, quelques centaines de millisecondes plus tard.
            # Repartir tout de suite revenait à calculer un chemin depuis la
            # case de placement : le serveur ignorait le paquet et le bot
            # restait figé jusqu'à son délai de sécurité.
            self.pos = None
            self.busy_since = None
            # Le groupe vaincu n'existe plus, mais compter sur un paquet de
            # retrait pour l'apprendre s'est révélé peu fiable : le bot
            # réattaquait le cadavre et perdait son délai de sécurité entier.
            # On repart d'une liste vide, les GM qui suivent la reconstruisent.
            self.groups.clear()
            self.engaging_since = None
            # Sortir d'un combat ne suffit pas : tant que le serveur n'a pas
            # renvoye le contenu de la carte, un deplacement part dans le
            # vide. On perdait 10 s d'abandon a chaque fois avant de
            # reessayer la meme cible, qui marchait alors du premier coup.
            self.map_ready = False
            self.map_wait_since = time.monotonic()
            self.say("fin de combat -> attente de la position")

        elif msg.startswith("GA;103;"):
            # Mort d'un combattant. Les monstres portent un identifiant
            # négatif : les nôtres et les alliés ne comptent pas comme kills.
            dead = msg.split(";")[2] if len(msg.split(";")) > 2 else ""
            if dead.startswith("-"):
                self.stats.kills += 1

        if (self.in_fight and AUTO_COMBAT and not self.combat_manual
                and self.stats.enabled and self.stats.mode != "off"):
            self.combat.on_packet(msg)

        if msg.startswith("OAK"):
            # Objet ajouté à l'inventaire : c'est le seul endroit où le réseau
            # associe l'exemplaire à son modèle. Sans ça, impossible de nommer
            # quoi que ce soit, les autres paquets ne citent que l'exemplaire.
            for uid, model, qty in gamedata.get().learn_objects(msg[3:]):
                self.stats.item_added(uid, qty)
                # Un objet ramassé tombe dans le sac : le calculateur de fusion
                # doit le voir sans attendre une reconnexion.
                if qty > 0:
                    self.stats.bag[model] = self.stats.bag.get(model, 0) + qty
                    self.stats.bag_uids.setdefault(model, {})[uid] = qty

        elif msg.startswith("ASK|"):
            self.char_id = msg.split("|")[1]
            self.combat.char_id = self.char_id
            # L'inventaire complet arrive avec la sélection du personnage :
            # on y apprend le modèle de tout ce qu'on possède déjà.
            # L'inventaire est le dernier champ, mais il contient lui-même des
            # '|' : prendre parts[10] seul tronquait la liste et laissait la
            # plupart des objets sans nom.
            parts = msg.split("|")
            if len(parts) > 3 and parts[3].isdigit():
                self.stats.level = int(parts[3])
                if self.stats.level_start is None:
                    self.stats.level_start = int(parts[3])
            if len(parts) > 10:
                inv = "|".join(parts[10:])
                for uid, _m, qty in gamedata.get().learn_objects(inv):
                    self.stats.seed(uid, qty)
                # Sac et équipé séparés : le calculateur compte l'équipé
                # seulement pour les Bouclier et Familier.
                self.stats.bag = gamedata.get().parse_bag(inv)
                self.stats.equipped = gamedata.get().parse_equipped(inv)
                # Sac détaillé par uid : nécessaire pour crafter (le serveur
                # veut le guid exact de chaque ingrédient).
                self.stats.bag_uids = gamedata.get().parse_bag_uids(inv)
            self.say(f"personnage {self.char_id}")

        elif msg.startswith("GDM|"):
            _, map_id, date, key = msg.split("|")[:4]
            self.map_id = int(map_id)
            self.stats.map_id = self.map_id
            self.map_ready = True
            self.resources.clear()
            # La carte a changé sous nos pieds : toute récolte en cours visait
            # l'ancienne et n'aboutira jamais. L'attendre coûtait le délai de
            # sécurité complet à chaque transition.
            self.busy_since = None
            self.target = None
            self.pos = None
            self.failed.clear()
            # Filet de sécurité : recevoir une carte veut dire qu'on est bien
            # hors combat. Sans ça, un GE manqué figerait le bot définitivement.
            self.in_fight = False
            asyncio.create_task(self._load_map(int(map_id), date, key))

        elif msg.startswith("GDF|"):
            self.map_ready = True
            for part in msg.split("|")[1:]:
                bits = part.split(";")
                if len(bits) >= 2 and bits[0].isdigit():
                    self.resources[int(bits[0])] = int(bits[1])
            self._maybe_act()

        elif msg.startswith("GM|"):
            self._read_positions(msg)

        elif msg.startswith("GA0;1;") and self.char_id:
            # Déplacement validé : la dernière cellule du chemin est l'arrivée.
            parts = msg.split(";")
            if len(parts) >= 4 and parts[2] == self.char_id and parts[3]:
                path = parts[3]
                if len(path) >= 3:
                    from protocol import path_decode
                    steps = path_decode(path)
                    if steps:
                        self.pos = steps[-1][1]

        elif HARVEST_START.match(msg) and self.char_id:
            # Récolte lancée : GA<n>;501;<id>;<cellule>,<durée ms>
            # Le serveur utilise indifféremment GA0;501 et GA1;501 — ne traiter
            # que la seconde laissait le bot bloqué jusqu'au délai de sécurité.
            parts = msg.split(";")
            if len(parts) >= 4 and parts[2] == self.char_id:
                cell, _, duration = parts[3].partition(",")
                self.stats.harvest_done(cell)
                self.failed.pop(int(cell), None)
                self.busy_since = time.monotonic()
                self.client_sent_gkk1 = False
                asyncio.create_task(self._finish_harvest(int(duration) / 1000))

        elif msg.startswith("As"):
            # As<xp>,<plancher du niveau>,<palier suivant>,...|<kamas>#...
            fields = msg[2:].split("|")
            head = fields[0].split(",")
            if len(head) >= 3 and all(x.lstrip("-").isdigit() for x in head[:3]):
                floor = int(head[1])
                # Un plancher de niveau qui monte = au moins un niveau
                # gagne. C'est le seul signal fiable en cours de partie :
                # le niveau lui-meme n'est transmis qu'a la connexion (ASK).
                if self.stats.xp_floor is not None and floor > self.stats.xp_floor:
                    self.stats.session_levels += 1
                    if self.stats.level:
                        self.stats.level += 1
                self.stats.xp_floor = floor
                self.stats.xp = (int(head[0]), floor, int(head[2]))
                self.stats.observe_xp(int(head[0]))
            if len(fields) > 1:
                kamas = fields[1].split("#")[0]
                if kamas.isdigit():
                    self.stats.observe_kamas(int(kamas))
            # Les totaux viennent d'être remis à jour : c'est le moment de
            # publier le gain du combat qui vient de se terminer.
            self.stats.settle_fight_gains()

        elif msg.startswith("JX|"):
            # JX|<métier>;<niveau>;<plancher>;<xp>;<suivant>;|...
            for part in msg.split("|")[1:]:
                f = part.split(";")
                if len(f) >= 5 and f[0].isdigit():
                    try:
                        level = int(f[1])
                        previous = self.stats.jobs.get(f[0])
                        self.stats.jobs[f[0]] = (level, int(f[2]),
                                                 int(f[3]), int(f[4]))
                        # Le premier JX arrive à la connexion : il fixe l'état
                        # de départ et ne doit pas être annoncé comme un gain.
                        if previous and level > previous[0]:
                            self.stats.job_levelup(f[0], level)
                    except ValueError:
                        pass

        elif msg.startswith("Ow"):
            cur, _, mx = msg[2:].partition("|")
            if cur.isdigit() and mx.isdigit():
                self.pods = (int(cur), int(mx))
                self.stats.pods = self.pods

        elif msg.startswith("OQ"):
            # OQ<numéro d'objet>|<quantité totale du lot>
            item, _, qty = msg[2:].partition("|")
            if item.isdigit() and qty.isdigit():
                qty = int(qty)
                self.stats.item_update(item, qty)
                # Un drop qui grossit une pile déjà possédée passe par OQ (pas
                # OAK) : sans ça, le calculateur de fusion ne voyait la nouvelle
                # quantité qu'après avoir sorti/remis l'objet. On tient le sac à
                # jour ici, par modèle, comme le fait le handler OAK.
                model = gamedata.get().model_of(item)
                if model is not None:
                    self.stats.bag_uids.setdefault(model, {})[item] = qty
                    self.stats.bag[model] = sum(
                        self.stats.bag_uids.get(model, {}).values())

        elif msg.startswith("OR"):
            # OR<uid> : objet retiré de l'inventaire (consommé par un craft,
            # jeté, vendu...). Sans ça, un ingrédient fusionné restait "présent"
            # dans le calculateur -> ressources non mises à jour, et l'item cible
            # ne devenait jamais craftable faute de voir ses ingrédients partir.
            uid = msg[2:].strip()
            if uid:
                gd = gamedata.get()
                # Le sac indexe les uid en décimal ; l'uid d'OR peut arriver en
                # décimal ou en hex — on résout les deux.
                if gd.model_of(uid) is None:
                    try:
                        uid = str(int(uid, 16))
                    except ValueError:
                        pass
                model = gd.model_of(uid)
                if model is not None:
                    self.stats.bag_uids.get(model, {}).pop(uid, None)
                    self.stats.bag[model] = sum(
                        self.stats.bag_uids.get(model, {}).values())

        elif msg.startswith("ECK"):
            # Ouverture d'un échange. Type 5 = banque (coffre) : les paquets ELO
            # qui suivent listent son contenu, qu'on compte dans le calculateur.
            self.bank_mode = (msg[3:].strip() == "5")

        elif msg.startswith("ELO") and self.bank_mode:
            # Liste complète de la banque : on remplace et on mémorise sur disque
            # (le contenu survit ainsi aux redémarrages, sans rouvrir la banque).
            self.stats.set_bank(gamedata.get().parse_bank(msg[3:]))

        elif msg.startswith("EV"):
            self.bank_mode = False   # échange fermé

    # ── état ─────────────────────────────────────────────────────────────────

    def _read_positions(self, msg):
        """GM|+<cellule>;<dir>;...;<id>;... — notre position et les groupes.

        Les monstres portent un identifiant négatif. Leur cellule est tout ce
        dont on a besoin pour les attaquer : engager un combat, c'est marcher
        dessus, il n'existe pas de paquet d'attaque distinct.
        """
        fresh = False   # un groupe a-t-il ete vu dans ce paquet ?
        for entry in msg.split("|")[1:]:
            if entry.startswith("-"):
                # Acteur retiré de la carte (mort, parti, combat engagé).
                # Le '-' de tête marque la suppression (GM|-2854 retire
                # l'acteur 2854). Pour un monstre, dont l'identifiant est déjà
                # négatif, la forme exacte n'a jamais été observée : on accepte
                # les deux lectures. Retirer un groupe à tort est sans gravité,
                # le GM suivant le réinscrit ; l'oublier coûterait un cooldown
                # de deux minutes sur un groupe qui n'existe plus.
                gone = entry[1:].strip()
                dead = {gone, "-" + gone}
                self.groups = {c: i for c, i in self.groups.items()
                               if i not in dead}
                continue
            if not entry.startswith("+"):
                continue
            fields = entry[1:].split(";")
            if len(fields) < 4:
                continue
            try:
                cell = int(fields[0])
            except ValueError:
                continue
            if fields[3] == self.char_id:
                self.pos = cell
            elif fields[3].startswith("-"):
                self.groups[cell] = fields[3]
                fresh = True
        # En farm, ce paquet est le signal qu'on attendait apres un combat :
        # position retrouvee et groupes a jour. Attendre le battement de 2 s
        # ajoutait un temps mort visible entre chaque bagarre.
        if fresh and self.stats.mode == "farm" and not self.in_fight:
            self._maybe_act()

    def close(self):
        """Appelé par le proxy quand la session de jeu se termine."""
        self.stats.client_connected = False
        self.stats.event("info", "jeu déconnecté")

    async def _announce_ready(self):
        """Signale "prêt" au placement, en gardant le placement par défaut."""
        await asyncio.sleep(DELAY_BEFORE_READY)
        # La pause doit valoir aussi pour le placement : sans ce contrôle, le
        # bot validait "prêt" avant même qu'on ait le temps de se placer.
        if self.in_fight and not self.combat_manual and self.stats.enabled:
            self.say("placement accepté -> prêt")
            self.s.to_server("GR1")

    async def _load_map(self, map_id, date, key):
        loop = asyncio.get_running_loop()
        try:
            # Téléchargement + déchiffrement : hors boucle d'événements pour
            # ne pas retarder le relais.
            self.gmap = await loop.run_in_executor(
                None, GameMap, map_id, date, key)
            # L'IA en a besoin pour calculer les zones d'effet.
            self.combat.gmap = self.gmap
            self.say(f"carte {map_id} chargée "
                     f"({sum(1 for i in range(len(self.gmap)) if self.gmap.walkable(i))} "
                     f"cellules praticables)")
            self._maybe_act()
        except Exception as e:
            self.gmap = None
            self.say(f"carte {map_id} illisible : {e!r}")

    async def _finish_harvest(self, seconds):
        await asyncio.sleep(seconds + 0.3)
        if not self.client_sent_gkk1:
            self.s.to_server("GKK1")
        self.busy_since = None
        await asyncio.sleep(DELAY_BETWEEN_HARVESTS)
        self._maybe_act()

    # ── décision ─────────────────────────────────────────────────────────────

    def _blocked(self):
        if not self.stats.enabled:
            return "en pause"
        # Un GE manque ne peut plus nous bloquer : passe ce silence, le
        # combat est forcement fini.
        if (self.in_fight and self.last_combat_packet
                and time.monotonic() - self.last_combat_packet > COMBAT_IDLE):
            self.in_fight = False
            self.say("plus de trafic de combat -> etat resynchronise")
        if self.in_fight:
            return "combat en cours"
        if self.busy_since is not None:
            if time.monotonic() - self.busy_since < HARVEST_TIMEOUT:
                return None                   # récolte en cours, silencieux
            # Le serveur a ignoré cette cellule : la réessayer indéfiniment
            # bloquait le bot pour de bon. On la met de côté et on passe à la
            # suivante, sans chercher à savoir pourquoi il a refusé.
            if self.target is not None:
                self.failed[self.target] = time.monotonic()
                self.say(f"cellule {self.target} sans réponse -> écartée, "
                         f"je passe à une autre")
            self.busy_since = None
        if time.monotonic() < self.paused_until:
            return None                       # pause joueur, silencieuse
        if self.gmap is None or self.pos is None or self.char_id is None:
            return None                       # pas encore prêt
        cur, mx = self.pods
        if mx and cur / mx >= PODS_STOP_RATIO:
            return f"pods pleins ({cur}/{mx})"
        return False

    def _farm(self):
        """Engage le groupe de monstres le plus proche.

        Il n'y a rien à décoder de plus : on marche sur leur cellule et le
        serveur déclenche le combat, exactement comme quand le joueur clique.
        """
        now = time.monotonic()
        # Double verrou volontaire. Le combat commence quelques centaines de
        # millisecondes avant que tout le monde soit d'accord dessus, et une
        # attaque lancee dans cet intervalle part dans le vide : elle laisse une
        # épée sur la carte et coûte le délai de sécurité complet.
        if self.in_fight or now - self.last_engage < ENGAGE_COOLDOWN:
            return
        # Trajet en cours vers un groupe : on ne relance rien tant que le
        # combat ne s'est pas ouvert, sauf si l'engagement a manifestement
        # echoue.
        if not self.map_ready:
            # Jamais de blocage definitif sur un paquet absent : passe le
            # delai, on repart quand meme.
            if now - self.map_wait_since < MAP_READY_TIMEOUT:
                return
            self.map_ready = True
            self.say("carte non confirmee -> je repars quand meme")
        if self.engaging_since is not None:
            if now - self.engaging_since < ENGAGE_TIMEOUT:
                return
            self.say("engagement sans suite -> abandon")
            if self.target is not None:
                self.failed[self.target] = now
            self.engaging_since = None
        targets = [c for c in self.groups
                   if now - self.failed.get(c, 0) > FAILED_COOLDOWN]
        if not targets:
            if self.last_blocked != "vide":
                self.say(f"aucun groupe accessible ({len(self.groups)} sur la carte)"
                         " — j'attends un repop")
                self.last_blocked = "vide-farm"
            return
        self.last_blocked = None

        best = None
        for cell in targets:
            if self.gmap.walkable(cell):
                # Mob sur case marchable (extérieur) : on marche dessus, ce qui
                # déclenche le combat.
                path = self.gmap.find_path(self.pos, cell)
            else:
                # Mob sur case NON marchable (boss de donjon, décor) : on ne peut
                # pas s'y rendre. On rejoint la case marchable adjacente la plus
                # proche, puis on ajoute la case du mob comme pas d'attaque final
                # — le serveur ouvre alors le combat, comme le vrai client.
                path = None
                for _, nb in self.gmap.neighbours(cell):
                    if not self.gmap.walkable(nb):
                        continue
                    p = self.gmap.find_path(self.pos, nb)
                    if p and (path is None or len(p) < len(path)):
                        path = p
                if path is not None:
                    path = path + [cell]
            # Un chemin sans déplacement veut dire qu'on est déjà sur la case :
            # le groupe n'y est plus, aucun combat ne se déclenchera.
            if path and len(path) > 1 and (best is None or len(path) < len(best[1])):
                best = (cell, path)
        if best is None:
            self.say(f"{len(targets)} groupes, aucun atteignable")
            return

        cell, path = best
        self.target = cell
        self.busy_since = now
        self.last_engage = now
        self.engaging_since = now
        self.say(f"attaque du groupe en {cell} ({len(path) - 1} pas)")
        if len(path) > 1:
            self.s.to_server("GA001" + compress_path(path))

    def _maybe_act(self):
        # Mode observateur : le proxy relaie et compte le loot, mais le
        # personnage ne joue pas. C'est ce qui permet d'avoir l'overlay
        # sans que le bot bouge.
        if self.stats.mode == "off":
            return
        blocked = self._blocked()
        if blocked is not False:
            # Le battement repasse ici toutes les 2 s : on ne signale qu'un
            # changement d'état, sinon le journal serait noyé.
            if blocked and blocked != self.last_blocked:
                self.say(f"en attente : {blocked}")
            self.last_blocked = blocked
            return
        self.last_blocked = None

        if self.stats.mode == "farm":
            self._farm()
            return

        now = time.monotonic()
        available = [c for c, state in self.resources.items()
                     if state == 1
                     and now - self.failed.get(c, 0) > FAILED_COOLDOWN]

        # Plus rien de jouable alors que la carte annonce des ressources :
        # notre vision est périmée. On redemande le contenu de la carte, ce
        # que le client fait lui-même après chaque chargement.
        if not available and self.resources and now - self.last_resync > RESYNC_INTERVAL:
            self.last_resync = now
            self.say("plus rien de récoltable -> je redemande la carte")
            self.s.to_server("GI")
            return

        if not available:
            # Ne surtout pas rester muet : un bot silencieux est indiscernable
            # d'un bot planté, et on finit par reprendre la main pour rien.
            if self.last_blocked != "vide":
                self.say(f"champ vidé ({len(self.resources)} ressources, "
                         f"aucune disponible) — attente des repops")
                self.last_blocked = "vide"
            return
        self.last_blocked = None

        best = None
        for cell in available:
            path = self.gmap.find_path(self.pos, cell)
            if path and (best is None or len(path) < len(best[1])):
                best = (cell, path)

        if best is None:
            self.say(f"{len(available)} ressources, aucune accessible")
            return

        cell, path = best
        self.target = cell
        self.say(f"récolte cellule {cell} ({len(path) - 1} pas), "
                 f"pods {self.pods[0]}/{self.pods[1]}")

        # Marqué occupé dès l'envoi, pas à la confirmation : le serveur met
        # parfois plusieurs secondes à répondre (il attend la fin de l'action
        # précédente), et sans ça le bot renvoyait la même récolte en double.
        self.busy_since = time.monotonic()

        if len(path) > 1:
            self.s.to_server("GA001" + compress_path(path))
        self.s.to_server(f"GA500{cell};{SKILL_ID}")


async def main():
    # Réapplique au client les greffes qu'une mise à jour aurait effacées :
    # l'overlay loot (HTML) et les ponts Node (redirection proxy + panneau).
    # Fait au démarrage, avant que le joueur lance le jeu : le prochain
    # lancement du client prend les patchs en compte.
    try:
        client_patch.patch_client()
    except Exception as e:
        print(f"[patch] overlay ignoré : {e}")
    try:
        bridge_patch.patch_all()
    except Exception as e:
        print(f"[patch] ponts ignorés : {e}")
    # Le tableau de bord tourne à côté du relais, dans la même boucle.
    await asyncio.gather(dashboard.serve(), proxy.serve(Brain))


def selftest():
    """Diagnostic sans ouvrir de port : verifie chemins, detection du client
    et donnees. Utile pour depanner une install (botcore.exe --selftest)."""
    import os
    import apppaths
    import client_config
    ok = True
    print("=== Bot Paradox — autotest ===")
    print("dossier appli :", apppaths.BASE_DIR)
    print("dossier data  :", apppaths.DATA_DIR,
          "" if os.path.isdir(apppaths.DATA_DIR) else "  << MANQUANT")
    client_found = os.path.exists(client_config.CLIENT_HTML)
    print("client Nexus  :", client_config.APP_ROOT,
          "  [OK]" if client_found else "  << INTROUVABLE")
    if not client_found:
        ok = False
        print("  -> lance l'installateur et indique ton dossier Nexus,")
        print("     ou pose data/client_override.txt avec le chemin ...\\resources\\app")
    for f in ("items.json", "fusion_recipes.json", "spells.json"):
        p = apppaths.data(f)
        present = os.path.exists(p)
        ok = ok and present
        print(f"  data/{f:<20}", "[OK]" if present else "<< MANQUANT")
    try:
        g = gamedata.GameData()
        g.load()
        print(f"noms/icones   : {len(g.items)} items, {len(g.icons)} icones")
    except Exception as e:
        ok = False
        print("chargement donnees : ERREUR", e)
    print("=== resultat :", "OK" if ok else "PROBLEME(S) DETECTE(S)", "===")
    return 0 if ok else 1


def detect_client():
    """Imprime le dossier ...\\resources\\app du client s'il est trouve, sinon
    'NOTFOUND'. Code retour 0 si trouve, 2 sinon. Utilise par l'installateur."""
    import os
    import client_config
    if os.path.exists(client_config.CLIENT_HTML):
        print(client_config.APP_ROOT)
        return 0
    print("NOTFOUND")
    return 2


def apply_patches():
    """Applique overlay + ponts au client detecte (ou au dossier force via
    data/client_override.txt). Code retour 0 si le client est present."""
    import os
    import client_config
    if not os.path.exists(client_config.CLIENT_HTML):
        print("[patch] client introuvable :", client_config.APP_ROOT)
        return 2
    try:
        client_patch.patch_client()
    except Exception as e:
        print(f"[patch] overlay: {e}")
    try:
        bridge_patch.patch_all()
    except Exception as e:
        print(f"[patch] ponts: {e}")
    print("[patch] client patche :", client_config.APP_ROOT)
    return 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv or "--check" in sys.argv:
        sys.exit(selftest())
    if "--detect" in sys.argv:
        sys.exit(detect_client())
    if "--patch" in sys.argv:
        sys.exit(apply_patches())
    print("[bot] mode assisté — toute action de ta part met le bot en pause")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[bot] arrêt.")
