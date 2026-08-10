"""Recupere les icones de sorts et les ecrit en PNG dans data/spell_icons/.

Le client ne livre les icones de sorts qu'en Flash, et uniquement en silhouette
blanche (`clips/spells/icons/up/<id>.swf`) : la couleur, elle, est appliquee par
le jeu. Le grimoire du serveur publie la version finie en SVG —
`grimoire.nexus-temporel.com/assets/spells/<id>.svg`, fond degrade par element
inclus — c'est donc la source qu'on utilise.

Le SVG est parfait pour le web mais Avalonia ne le lit pas sans dependance
supplementaire : on convertit donc une fois pour toutes en PNG 96x96, avec Edge
en mode headless (present sur toute machine Windows). La page dessine chaque SVG
dans un canvas et renvoie le PNG au petit serveur local ci-dessous, qui l'ecrit.

    python spell_icons.py             # tout ce qui manque
    python spell_icons.py --force     # refait meme ce qui existe
    python spell_icons.py 20          # les 20 premiers, pour verifier

Les PNG sont ensuite lus directement par la fenetre Sorts et servis par le bot
(/spellicon/<id>). Rien n'appelle ce script au demarrage : il ne tourne qu'a la
demande, et le resultat est versionne avec le reste de data/.
"""

import base64
import hashlib
import http.server
import json
import os
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from apppaths import data as _data

GRIMOIRE = "https://grimoire.nexus-temporel.com/assets/spells/%d.svg"
# Les deux formats servent : le SVG pour les pages web (net a toute taille), le
# PNG pour la fenetre Avalonia, qui ne lit pas le SVG sans dependance en plus.
OUT_DIR = _data("spell_icons")
SVG_DIR = _data("spell_svg")
PORT = 8791
SIZE = 96

# Sorts pilotes par le bot qui n'ont pas d'icone dans le client (invocations,
# objets) mais que le grimoire connait quand meme.
EXTRA_IDS = (6019,)


def _client_ids():
    """Ids de sorts connus du client (une icone Flash par sort du jeu)."""
    try:
        from client_config import RETROCLIENT
        d = os.path.join(RETROCLIENT, "clips", "spells", "icons", "up")
        return {int(f[:-4]) for f in os.listdir(d)
                if f.endswith(".swf") and f[:-4].isdigit() and int(f[:-4]) > 0}
    except (OSError, ImportError):
        return set()


def _catalog_ids():
    """Ids du catalogue de sorts memorise par le bot (cles « id-niveau »)."""
    try:
        with open(_data("spells.json"), encoding="utf-8") as f:
            return {int(k.split("-")[0]) for k in json.load(f)
                    if k.split("-")[0].isdigit()}
    except (OSError, ValueError):
        return set()


def download(ids, force=False):
    """Telecharge les SVG manquants. Renvoie les ids reellement disponibles.

    Un id inconnu ne renvoie pas 404 mais un SVG « placeholder » toujours
    identique : on le reconnait a son empreinte et on l'ecarte.
    """
    os.makedirs(SVG_DIR, exist_ok=True)
    placeholder = None
    have, missing = [], 0
    for i, sid in enumerate(ids):
        path = os.path.join(SVG_DIR, f"{sid}.svg")
        if os.path.exists(path) and not force:
            have.append(sid)
            continue
        try:
            req = urllib.request.Request(
                GRIMOIRE % sid, headers={"User-Agent": "BotParadox-icons"})
            with urllib.request.urlopen(req, timeout=15) as r:
                body = r.read()
        except (urllib.error.URLError, OSError) as e:
            print(f"  {sid} : {e}", flush=True)
            continue
        digest = hashlib.md5(body).hexdigest()
        # Le placeholder est le SVG rendu pour un id bidon : on l'apprend une fois.
        if placeholder is None:
            try:
                req = urllib.request.Request(
                    GRIMOIRE % 99999999,
                    headers={"User-Agent": "BotParadox-icons"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    placeholder = hashlib.md5(r.read()).hexdigest()
            except (urllib.error.URLError, OSError):
                placeholder = ""
        if digest == placeholder:
            missing += 1
            continue
        with open(path, "wb") as f:
            f.write(body)
        have.append(sid)
        if (i + 1) % 100 == 0:
            print(f"  telecharges {len(have)} / essayes {i + 1}", flush=True)
    print(f"[icones] {len(have)} svg disponibles, {missing} sans icone")
    return have


_PAGE = """<!doctype html><html><head><meta charset="utf-8"></head>
<body style="margin:0;background:transparent">
<canvas id="cv" width="__SZ__" height="__SZ__"></canvas>
<script>
const IDS = __IDS__, SZ = __SZ__;
const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
function load(src){
  return new Promise(res => {
    const im = new Image();
    im.onload = () => res(im);
    im.onerror = () => res(null);
    im.src = src;
  });
}
(async () => {
  for (const id of IDS) {
    const im = await load('/svg/' + id + '.svg');
    let url = '';
    if (im) {
      ctx.clearRect(0, 0, SZ, SZ);
      ctx.drawImage(im, 0, 0, SZ, SZ);
      try { url = cv.toDataURL('image/png'); } catch (e) { url = ''; }
    }
    await fetch('/png/' + id, {method: 'POST', body: url});
  }
  await fetch('/done', {method: 'POST', body: 'ok'});
})();
</script></body></html>"""


class _Handler(http.server.BaseHTTPRequestHandler):
    ids = []
    saved = 0
    empty = 0
    done = threading.Event()

    def log_message(self, *a):
        pass

    def _send(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode("utf-8", "replace")
        cls = _Handler
        if self.path == "/done":
            cls.done.set()
        elif self.path.startswith("/png/"):
            sid = self.path[len("/png/"):]
            if body.startswith("data:image/png;base64,"):
                with open(os.path.join(OUT_DIR, sid + ".png"), "wb") as f:
                    f.write(base64.b64decode(body.split(",", 1)[1]))
                cls.saved += 1
            else:
                cls.empty += 1
            if (cls.saved + cls.empty) % 100 == 0:
                print(f"  {cls.saved + cls.empty}/{len(cls.ids)} rendus",
                      flush=True)
        self._send(b"ok", "text/plain")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            page = (_PAGE.replace("__IDS__", json.dumps(_Handler.ids))
                         .replace("__SZ__", str(SIZE)))
            return self._send(page.encode("utf-8"), "text/html; charset=utf-8")
        if path.startswith("/svg/"):
            f = os.path.join(SVG_DIR, os.path.basename(path))
            if os.path.isfile(f):
                with open(f, "rb") as fh:
                    return self._send(fh.read(), "image/svg+xml")
        self.send_error(404)


def _edge():
    for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if os.path.exists(p):
            return p
    return None


def render(ids):
    """Convertit les SVG deja telecharges en PNG SIZExSIZE."""
    edge = _edge()
    if edge is None:
        print("[icones] Microsoft Edge introuvable — conversion impossible")
        return 0
    os.makedirs(OUT_DIR, exist_ok=True)
    _Handler.ids = list(ids)
    _Handler.saved = _Handler.empty = 0
    _Handler.done.clear()

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", PORT), _Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"[icones] conversion de {len(_Handler.ids)} svg en png…", flush=True)
    proc = subprocess.Popen(
        [edge, "--headless", "--disable-gpu", "--no-sandbox",
         "--enable-unsafe-swiftshader", "--window-size=200,200",
         "--user-data-dir=" + os.path.join(
             os.environ.get("TEMP", "."), "bp-edge-icons"),
         f"http://127.0.0.1:{PORT}/"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 120 + 0.5 * len(_Handler.ids)
    while not _Handler.done.is_set() and time.time() < deadline:
        time.sleep(0.5)
    proc.terminate()
    srv.shutdown()
    print(f"[icones] termine : {_Handler.saved} png, {_Handler.empty} echecs"
          f"{'' if _Handler.done.is_set() else ' (delai depasse)'}")
    return _Handler.saved


def shrink(ids):
    """Ramene les PNG a 64 px et 48 couleurs : indiscernable a la taille ou ils
    sont affiches (26-34 px), mais 5 fois plus leger dans l'installateur.

    Sans Pillow on garde les PNG tels quels : c'est un confort, pas une etape
    necessaire, et ce script ne tourne qu'en developpement."""
    try:
        from PIL import Image
    except ImportError:
        print("[icones] Pillow absent : PNG laisses en pleine taille")
        return
    for sid in ids:
        path = os.path.join(OUT_DIR, f"{sid}.png")
        if not os.path.exists(path):
            continue
        try:
            im = Image.open(path).convert("RGBA").resize((64, 64), Image.LANCZOS)
            im.quantize(colors=48, method=Image.FASTOCTREE).save(
                path, optimize=True)
        except OSError:
            pass
    total = sum(os.path.getsize(os.path.join(OUT_DIR, f))
                for f in os.listdir(OUT_DIR) if f.endswith(".png"))
    print(f"[icones] {total / 1e6:.2f} Mo au total")


def main(argv):
    force = "--force" in argv
    nums = [a for a in argv if a.isdigit()]
    ids = sorted(_client_ids() | _catalog_ids() | set(EXTRA_IDS))
    if nums:
        ids = ids[:int(nums[0])]
    print(f"[icones] {len(ids)} sorts candidats")
    have = download(ids, force=force)
    todo = [s for s in have
            if force or not os.path.exists(os.path.join(OUT_DIR, f"{s}.png"))]
    if not todo:
        print("[icones] png deja a jour")
        return
    if render(todo):
        shrink(todo)


if __name__ == "__main__":
    main(sys.argv[1:])
