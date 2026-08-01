"""
Proxy TCP entre le client Flash (Ruffle) et le serveur de jeu Paradox.

Relaie intégralement les deux sens, journalise tout, et expose un point
d'accroche (`brain`) qui voit passer chaque paquet et peut en injecter.

Le brain injecte directement dans la socket serveur : ses paquets ne
traversent donc jamais le flux client->serveur. C'est ce qui permet de
distinguer sans ambiguïté une action du joueur d'une action du bot.

Usage :  python proxy.py          (relais seul, sans bot)
         python bot.py            (relais + bot de récolte)
"""

import asyncio
import json
import os
import sys
from datetime import datetime

# La console Windows est en cp1252 : sans ça, afficher un octet non-ASCII lève
# UnicodeEncodeError. Cette exception remontait jusqu'à tuer le relais et
# fermer la connexion de jeu — d'où un "Connexion interrompue" dans le client.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from apppaths import HANDOFF_FILE, LOG_DIR

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 5555

DELIM = b"\x00"

# Opcodes bruyants : toujours écrits dans le fichier, masqués dans la console.
MUTE_IN_CONSOLE = {"BD", "BT", "gIG", "gIM", "cMK", "Im", "am", "BN", "cs"}

# Tout ce qu'on a déjà observé en jeu (récolte, déplacement, chat, guilde...).
# Un opcode absent de cette liste est du jamais-vu : il est signalé bruyamment
# et copié dans logs/nouveaux-opcodes.log. C'est ce qui nous donnera la liste
# des paquets de combat au premier affrontement, sans avoir à les deviner.
KNOWN_OPCODES = {
    "AL", "ALK", "AR", "AS", "ASK", "AT", "ATK", "AV", "Af", "Agf", "Ak", "As",
    "BD", "BM", "BN", "BT", "CW", "CWJ", "EV", "EW", "FO", "GA", "GC", "GCK",
    "GDF", "GDK", "GDM", "GKK", "GM", "GV", "Gp", "Gr", "Gz", "HG", "ILS",
    "IQ", "Im", "Ir", "JN", "JO", "JS", "JX", "OQ", "OS", "Ow", "RMC", "RMD",
    "Rx", "SL", "ST", "SV", "ZS", "al", "am", "cC", "cMK", "cs", "eL", "fC",
    "gIG", "gIM", "gSR", "xC", "?",
}


class Tee:
    def __init__(self, path):
        self.fh = open(path, "a", encoding="utf-8", buffering=1)
        self.path = path

    def line(self, direction, opcode, payload, muted):
        # Règle absolue : la journalisation ne doit JAMAIS pouvoir interrompre
        # le relais. Toute erreur ici est avalée — perdre une ligne de log est
        # sans conséquence, perdre la connexion déconnecte le joueur.
        try:
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            text = f"[{ts}] {direction} {opcode:<4} | {payload}"
            self.fh.write(text + "\n")
            if not muted:
                print(text, flush=True)
        except Exception:
            pass

    def novelty(self, direction, opcode, payload):
        """Signale un opcode jamais observé, et l'archive à part.

        Les paquets de combat nous sont totalement inconnus : plutôt que de
        les deviner, on les laisse se révéler tout seuls au premier combat.
        """
        try:
            line = f"{datetime.now():%H:%M:%S.%f} {direction} {opcode} | {payload}"
            with open(os.path.join(LOG_DIR, "nouveaux-opcodes.log"),
                      "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            print(f"  >>> OPCODE INCONNU  {direction} {opcode} | {payload[:160]}",
                  flush=True)
        except Exception:
            pass

    def note(self, text):
        try:
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            line = f"[{ts}] --- {text}"
            self.fh.write(line + "\n")
            print(line, flush=True)
        except Exception:
            pass


class Session:
    """Poignée donnée au brain pour émettre des paquets.

    Les paquets du bot sont injectés sur la MÊME connexion que le vrai client.
    Le serveur (PacketSecurityGuard) compte comme "strike" tout paquet trop
    rapproché (< 40 ms), tout doublon (> 2 identiques/s) et tout dépassement de
    débit (~10/s global) ; au 4e strike il déconnecte, et son compteur ne
    redescend jamais → déconnexion garantie au bout d'un moment. On cadence donc
    la sortie du bot via une file : espacement mini entre paquets, et jamais
    plus de 2 paquets identiques par seconde glissante. Rien n'est jeté, tout
    est simplement lissé. Les paquets du client, eux, ne passent pas par ici."""

    # Espacement mini entre deux paquets DU BOT (~6,5/s max) : bien au-dessus de
    # la fenêtre replay serveur (40 ms) et sous la limite de débit global (10/s),
    # en laissant de la marge au vrai client sur la connexion partagée.
    MIN_GAP = 0.15
    # Fenêtre "même paquet" du serveur : on garde au plus 2 identiques par seconde.
    SAME_WINDOW = 1.0
    SAME_MAX = 2

    def __init__(self, client_writer, server_writer, log):
        self.client_writer = client_writer
        self.server_writer = server_writer
        self.log = log
        # Fenêtre pendant laquelle on bloque les GKK du client (ils annulent
        # nos déplacements de combat injectés). Voir should_drop_client.
        self._gkk_suppress_until = 0.0
        # File d'émission du bot + horodatage par paquet (anti-doublon serveur).
        self._queue = asyncio.Queue()
        self._payload_times = {}
        self._sender_task = None

    def start_sender(self):
        if self._sender_task is None:
            self._sender_task = asyncio.ensure_future(self._drain())

    def stop_sender(self):
        if self._sender_task is not None:
            self._sender_task.cancel()
            self._sender_task = None

    async def _drain(self):
        """Vide la file en cadençant : ≥ MIN_GAP entre paquets, et jamais plus
        de SAME_MAX paquets identiques par SAME_WINDOW (on retarde si besoin,
        on ne jette rien)."""
        import time
        try:
            while True:
                payload = await self._queue.get()
                now = time.monotonic()
                times = self._payload_times.setdefault(payload, [])
                times[:] = [t for t in times if now - t < self.SAME_WINDOW]
                if len(times) >= self.SAME_MAX:
                    wait = self.SAME_WINDOW - (now - times[0]) + 0.02
                    if wait > 0:
                        await asyncio.sleep(wait)
                        now = time.monotonic()
                        times[:] = [t for t in times if now - t < self.SAME_WINDOW]
                try:
                    self.log.note(f"BOT -> {payload}")
                    self.server_writer.write(payload.encode("latin-1") + b"\n" + DELIM)
                    await self.server_writer.drain()
                except Exception:
                    pass
                times.append(time.monotonic())
                if len(self._payload_times) > 256:
                    self._payload_times = {
                        p: ts for p, ts in self._payload_times.items()
                        if ts and now - ts[-1] < 2.0}
                await asyncio.sleep(self.MIN_GAP)
        except asyncio.CancelledError:
            pass

    def suppress_client_gkk(self, seconds):
        """Bloque les GKK envoyés par le client pendant `seconds`. Utilisé
        autour d'un déplacement de combat injecté : sinon le client confirme sa
        propre position (GKK0) et le serveur annule notre mouvement."""
        import time
        self._gkk_suppress_until = time.monotonic() + seconds

    def should_drop_client(self, msg):
        import time
        return (msg.startswith("GKK")
                and time.monotonic() < self._gkk_suppress_until)

    def to_server(self, payload):
        """Met un paquet dans la file d'émission cadencée du bot (voir _drain).
        Non bloquant : le lissage/anti-doublon se fait à la sortie de la file."""
        try:
            self._queue.put_nowait(payload)
        except Exception:
            # File indisponible (avant start_sender) : envoi direct de secours.
            try:
                self.log.note(f"BOT -> {payload}")
                self.server_writer.write(payload.encode("latin-1") + b"\n" + DELIM)
            except Exception:
                pass

    def to_client(self, payload):
        self.server_writer  # noqa - symétrie
        self.client_writer.write(payload.encode("latin-1") + DELIM)


def escape(msg):
    """Rend le paquet affichable en ASCII pur, de façon réversible."""
    return "".join(
        ch if 32 <= ord(ch) < 127 else f"\\x{ord(ch):02x}" for ch in msg)


def opcode_of(msg):
    code = ""
    for ch in msg[:3]:
        if ch.isalpha():
            code += ch
        else:
            break
    return code or "?"


def read_handoff():
    if not os.path.exists(HANDOFF_FILE):
        return None
    try:
        with open(HANDOFF_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("ip") and data.get("port"):
            return data["ip"], int(data["port"])
    except Exception as e:
        print(f"[proxy] handoff.json illisible : {e}", file=sys.stderr)
    return None


async def pump(reader, writer, direction, log, brain, session, seen_new):
    buf = b""
    # Sens client->serveur : on transmet message par message pour pouvoir en
    # bloquer certains (le GKK du client qui annule nos déplacements de combat).
    # Sens serveur->client : on transmet le chunk tel quel, sans latence.
    filtering = (direction == "C>S")
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break

            if not filtering:
                # On transmet d'abord : ni la journalisation ni le bot ne
                # doivent ajouter de latence au jeu.
                writer.write(chunk)
                await writer.drain()

            buf += chunk
            while DELIM in buf:
                raw, buf = buf.split(DELIM, 1)
                if not raw:
                    if filtering:            # DELIM isolé : on le transmet tel quel
                        writer.write(DELIM)
                        await writer.drain()
                    continue
                msg = raw.decode("latin-1")
                # C>S : transmettre ce message SAUF si le bot demande de le
                # bloquer (GKK pendant un déplacement de combat injecté).
                if filtering:
                    if session is not None and session.should_drop_client(msg.strip("\n\r")):
                        log.note(f"BOT bloque GKK client : {msg.strip()!r}")
                    else:
                        writer.write(raw + DELIM)
                    await writer.drain()
                code = opcode_of(msg)
                shown = escape(msg)
                log.line(direction, code, shown, code in MUTE_IN_CONSOLE)
                if code not in KNOWN_OPCODES and code not in seen_new:
                    seen_new.add(code)
                    log.novelty(direction, code, shown)
                if brain is not None:
                    try:
                        brain.on_packet(direction, msg.strip("\n\r"), session)
                    except Exception as e:
                        log.note(f"BOT erreur ignorée : {e!r}")
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


def make_handler(brain_factory=None):
    async def on_client(client_reader, client_writer):
        os.makedirs(LOG_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log = Tee(os.path.join(LOG_DIR, f"session-{stamp}.log"))

        log.note(f"client connecté depuis {client_writer.get_extra_info('peername')}")

        target = read_handoff()
        if not target:
            log.note("ABANDON : handoff.json absent. Le jeu a-t-il redémarré ?")
            client_writer.close()
            return

        host, port = target
        log.note(f"ouverture vers le serveur de jeu {host}:{port}")
        try:
            server_reader, server_writer = await asyncio.open_connection(host, port)
        except Exception as e:
            log.note(f"ABANDON : connexion impossible : {e}")
            client_writer.close()
            return

        session = Session(client_writer, server_writer, log)
        session.start_sender()   # file d'émission cadencée (anti-kick sécurité)
        brain = brain_factory(session) if brain_factory else None
        log.note(f"relais actif — journal : {log.path}")

        # Partagé entre les deux sens : un opcode n'est signalé qu'une fois.
        seen_new = set()

        await asyncio.gather(
            pump(client_reader, server_writer, "C>S", log, brain, session, seen_new),
            pump(server_reader, client_writer, "S>C", log, brain, session, seen_new),
        )
        session.stop_sender()
        if brain is not None and hasattr(brain, "close"):
            try:
                brain.close()
            except Exception:
                pass
        log.note("session terminée")

    return on_client


async def serve(brain_factory=None):
    server = await asyncio.start_server(
        make_handler(brain_factory), LISTEN_HOST, LISTEN_PORT)
    print(f"[proxy] en écoute sur {LISTEN_HOST}:{LISTEN_PORT}")
    print(f"[proxy] logs dans {LOG_DIR}\n")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        print("\n[proxy] arrêt.")
