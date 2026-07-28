"""
Pilotage des panneaux Paradox (amélioration, rareté, craft, boutique).

Ces fonctions n'existent pas dans le protocole de jeu : ce sont des ajouts du
serveur, exposés par une API REST que seul le client sait signer.

Plutôt que d'imiter cette signature depuis Python, on demande au client de
faire l'appel lui-même, via un petit pont local. Le serveur reçoit donc une
requête strictement identique à celle d'un clic — même jeton, même signature,
même origine. Rien n'est usurpé.

Deux règles tirées des captures :

  * l'enveloppe renvoie toujours `success: true` quand la requête aboutit ;
    le vrai résultat de l'action est dans `data.success`
  * chaque tentative consomme des ressources, réussie ou non — d'où le
    plancher obligatoire sur toutes les boucles

Usage :
    from panels import Panels
    p = Panels()
    p.upgrade_until(44288704, target_level=3, floor=10)
"""

import json
import time
import urllib.error
import urllib.request

BRIDGE = "http://127.0.0.1:8790/panel"

# Marge de sécurité par défaut : on n'entame jamais les dernières ressources.
DEFAULT_FLOOR = 5

# Rythme entre deux tentatives, pour rester dans le tempo d'un humain pressé.
DELAY = 0.6


class PanelError(RuntimeError):
    pass


class Panels:
    def __init__(self, url=BRIDGE):
        self.url = url

    # ── appel de base ────────────────────────────────────────────────────────

    def call(self, panel, action, **params):
        payload = json.dumps({"panelId": panel, "action": action,
                              "params": params}).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=payload,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                envelope = json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            if e.code == 503:
                raise PanelError(
                    "pont non amorcé — clique une fois sur un panneau "
                    "dans le jeu, puis relance") from None
            raise PanelError(f"HTTP {e.code} : {detail}") from None
        except OSError as e:
            raise PanelError(f"pont injoignable ({e}) — le jeu est-il lancé ?") from None

        # L'enveloppe dit seulement que la requête est passée.
        if not envelope.get("success"):
            raise PanelError(f"requête refusée : {envelope}")
        return envelope.get("data", {})

    # ── amélioration ─────────────────────────────────────────────────────────

    def upgrade_until(self, item_guid, target_level, floor=DEFAULT_FLOOR,
                      max_attempts=200, log=print):
        """Tente l'amélioration jusqu'au niveau visé.

        S'arrête dès que les ressources passent sous le plancher : le coût est
        prélevé même en cas d'échec, donc sans garde-fou une série de ratés
        vide le stock en quelques secondes.
        """
        attempts = 0
        while attempts < max_attempts:
            data = self.call("upgrade", "upgrade-item", itemGuid=item_guid)
            attempts += 1

            level = data.get("newLevel")
            owned = data.get("resourcesOwned", 0)
            cost = data.get("resourceCost", 1)
            ok = data.get("success")

            log(f"  essai {attempts:3} : niveau {data.get('oldLevel')} -> {level} "
                f"{'réussi' if ok else 'échoué'}  "
                f"({data.get('successRate')}%, coût {cost}, reste {owned})")

            if level is not None and target_level is not None and level >= target_level:
                log(f"  niveau {target_level} atteint en {attempts} essais")
                return data
            if owned - cost < floor:
                log(f"  plancher atteint ({owned} restantes, plancher {floor}) — arrêt")
                return data
            time.sleep(DELAY)

        log(f"  limite de {max_attempts} essais atteinte — arrêt")
        return None

    # ── rareté ───────────────────────────────────────────────────────────────

    def roll_until_up(self, item_guid, floor=DEFAULT_FLOOR, safe=False,
                      max_attempts=200, log=print):
        """Relance la rareté jusqu'à une amélioration.

        Le tirage peut monter, stagner ou **descendre** : on s'arrête au
        premier UP plutôt que de risquer de repartir vers le bas.
        """
        attempts = 0
        while attempts < max_attempts:
            data = self.call("rarity", "roll-rarity",
                             itemGuid=item_guid, useSafeRoll=safe)
            attempts += 1

            direction = data.get("direction")
            owned = data.get("resourcesOwned", 0)
            log(f"  essai {attempts:3} : {direction:5} "
                f"-> {data.get('newName')} ({data.get('bonusPercent')}%), "
                f"reste {owned}")

            if direction == "UP":
                log(f"  amélioré en {attempts} essais")
                return data
            if owned <= floor:
                log(f"  plancher atteint ({owned} restantes) — arrêt")
                return data
            time.sleep(DELAY)

        log(f"  limite de {max_attempts} essais atteinte — arrêt")
        return None

    # ── divers ───────────────────────────────────────────────────────────────

    def lock(self, item_guid):
        return self.call("rarity", "lock-item", itemGuid=item_guid)

    def unlock(self):
        return self.call("rarity", "unlock-item")

    def close_exchange(self):
        return self.call("utility", "close-exchange")


if __name__ == "__main__":
    p = Panels()
    try:
        print("pont :", p.call("utility", "close-exchange"))
        print("le pont répond.")
    except PanelError as e:
        print("échec :", e)
