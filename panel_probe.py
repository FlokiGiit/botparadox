"""Interroge un panneau du client via le pont local, et montre la reponse.

Le client Nexus expose ses panneaux (Ladder, Prestige, Fusion...) derriere une
API signee ; `bridge_patch` en capture l'acces et l'offre sur
127.0.0.1:8790. Le bot s'en sert deja pour les fusions. Cet outil sert a
DECOUVRIR ce qu'un panneau renvoie, quand on ne connait pas encore le nom de
l'action ni la forme des donnees.

    python panel_probe.py ladder-general
    python panel_probe.py prestige status
    python panel_probe.py ladder-general ranking "{\\"page\\": 1}"

Sans action, on essaie les noms les plus courants et on garde ceux qui
repondent. Le jeu doit tourner et un panneau doit avoir ete ouvert au moins une
fois dans la session (c'est ce qui amorce le pont).
"""

import json
import sys
import urllib.error
import urllib.request

BRIDGE = "http://127.0.0.1:8790/panel"

# Noms d'action tentes quand l'appelant n'en donne pas. Volontairement court :
# on interroge l'API du serveur, pas la peine de la marteler.
COMMON = ("load", "init", "get", "list", "status", "overview", "refresh")


def call(panel_id, action, params=None, timeout=15):
    payload = {"panelId": panel_id, "action": action, "params": params or {}}
    req = urllib.request.Request(
        BRIDGE, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return {"_http": e.code, **json.loads(e.read())}
        except Exception:
            return {"_http": e.code}
    except Exception as e:
        return {"_erreur": str(e)}


def summarize(obj, prefix="", depth=0, out=None):
    """Arborescence des cles, avec les valeurs scalaires courtes."""
    out = [] if out is None else out
    if depth > 3:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                out.append(f"{prefix}{k} : {type(v).__name__}"
                           f"({len(v)})")
                summarize(v, prefix + k + ".", depth + 1, out)
            else:
                out.append(f"{prefix}{k} = {v!r}"[:160])
    elif isinstance(obj, list) and obj:
        summarize(obj[0], prefix + "[0].", depth + 1, out)
    return out


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    panel = argv[0]
    params = json.loads(argv[2]) if len(argv) > 2 else {}
    # Sans action : lecture simple du panneau (route GET du pont). C'est ce que
    # fait le client pour remplir ses panneaux, et ca suffit presque toujours.
    actions = [argv[1]] if len(argv) > 1 else [None]

    for action in actions:
        res = call(panel, action, params)
        label = action or "(lecture)"
        if res.get("_erreur"):
            print(f"[{label}] pont injoignable : {res['_erreur']}")
            print("  -> le jeu doit tourner, et un panneau avoir ete ouvert "
                  "une fois (c'est ce qui amorce le pont).")
            return 2
        ok = res.get("success") is not False and not res.get("_http")
        head = "OK " if ok else "KO "
        print(f"\n=== [{head}{panel} / {action}]")
        if not ok:
            print("   ", json.dumps(res, ensure_ascii=False)[:300])
            continue
        for line in summarize(res.get("data", res)):
            print("   ", line)
        with open(f"panel-{panel}-{action or 'data'}.json", "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        print(f"    (reponse complete dans panel-{panel}-{action or 'data'}.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
