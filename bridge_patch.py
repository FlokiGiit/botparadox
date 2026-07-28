"""
Patchs des fichiers Node du client (hors projet), reappliques a chaque
demarrage du bot — comme client_patch pour l'overlay HTML.

Deux greffes, toutes deux idempotentes et delimitees par des marqueurs :

  login-tcp-bridge.js   redirige Flash vers le proxy du bot (127.0.0.1:5555)
                        en gardant la vraie cible serveur dans handoff.json,
                        que proxy.py lit pour relayer.

  panel-handlers.js     ouvre un pont HTTP local (127.0.0.1:8790) qui laisse
                        le bot demander au client d'emettre une action de
                        panneau signee (fusion, etc.), sans jamais signer
                        lui-meme.

Une mise a jour du client reecrit ces fichiers et efface les greffes ; il
suffit de relancer le bot pour tout recoller.
"""

import os
import re

from apppaths import BASE_DIR, HANDOFF_FILE
from client_config import LOGIN_BRIDGE, PANEL_HANDLERS

_HANDOFF_JS = HANDOFF_FILE.replace("\\", "\\\\")   # echappe pour une chaine JS

REDIRECT_VERSION = 2   # v2 : handoff.json a un chemin fixe partage (apppaths)
BRIDGE_VERSION = 1


# ── login-tcp-bridge.js : redirection vers le proxy ─────────────────────────

# Ligne canonique du client qui envoie l'IP reelle du serveur de jeu a Flash.
_AYK_ORIG = "send('AYK' + sb.ip + ':' + sb.port + ';' + sb.accountId)"

_AYK_BLOCK = (
    "/*[BOT-REDIRECT v%d START]*/\n"
    "        try { require('fs').writeFileSync('%s', JSON.stringify("
    "{ ip: sb.ip, port: sb.port, accountId: sb.accountId, ts: Date.now() })) } "
    "catch (e) { console.warn('[bot-redirect] handoff:', e && e.message) }\n"
    "        send('AYK' + '127.0.0.1:5555' + ';' + sb.accountId)\n"
    "        /*[BOT-REDIRECT v%d END]*/"
) % (REDIRECT_VERSION, _HANDOFF_JS, REDIRECT_VERSION)

_REDIRECT_RE = re.compile(
    r"/\*\[BOT-REDIRECT[^\]]*START\]\*/.*?/\*\[BOT-REDIRECT[^\]]*END\]\*/",
    re.DOTALL)


def patch_login_bridge(path=LOGIN_BRIDGE, log=print):
    if not os.path.exists(path):
        log(f"[bridge] login-tcp-bridge introuvable : {path}")
        return False
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    if f"[BOT-REDIRECT v{REDIRECT_VERSION} START]" in src:
        return False   # deja a jour

    # Retire une greffe d'une version anterieure en restaurant la ligne d'origine.
    src = _REDIRECT_RE.sub(_AYK_ORIG, src)

    if _AYK_ORIG not in src:
        log("[bridge] ligne AYK d'origine introuvable — structure inattendue")
        return False

    patched = src.replace(_AYK_ORIG, _AYK_BLOCK, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(patched)
    log(f"[bridge] redirection proxy injectee (v{REDIRECT_VERSION})")
    return True


# ── panel-handlers.js : pont HTTP local pour l'automatisation ───────────────

_BRIDGE_BLOCK = ("""/*[BOT-PANEL-BRIDGE v%d START]*/
const fsLog = require('fs');
const PANEL_LOG = '%s';
function logPanel(tag, data) {
  try { fsLog.appendFileSync(PANEL_LOG, new Date().toISOString() + ' ' + tag + ' ' + JSON.stringify(data) + '\\n'); } catch (e) {}
}
const httpBridge = require('http');
const BRIDGE_PORT = 8790;
let scopedApi = null;   // capture au premier appel de panneau authentifie
const bridgeServer = httpBridge.createServer((req, res) => {
  let body = '';
  req.on('data', (c) => (body += c));
  req.on('end', async () => {
    const reply = (code, obj) => { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(obj)); };
    if (req.method !== 'POST') return reply(405, { error: 'POST attendu' });
    if (!scopedApi) return reply(503, { error: 'NON_AMORCE' });
    let p; try { p = JSON.parse(body || '{}'); } catch (e) { return reply(400, { error: 'JSON invalide' }); }
    if (!p.panelId || !p.action) return reply(400, { error: 'panelId et action requis' });
    try {
      const result = await scopedApi.post('/api/panels/' + encodeURIComponent(p.panelId) + '/action', { action: p.action, params: p.params || {} });
      logPanel('BOT', { panelId: p.panelId, action: p.action, params: p.params, result });
      reply(200, result);
    } catch (e) { reply(500, { error: String(e && e.message) }); }
  });
});
bridgeServer.on('error', (err) => { console.warn('[panel-bridge] desactive :', err && err.message); });
bridgeServer.listen(BRIDGE_PORT, '127.0.0.1', () => { console.log('[panel-bridge] 127.0.0.1:' + BRIDGE_PORT); });
/*[BOT-PANEL-BRIDGE v%d END]*/
""") % (BRIDGE_VERSION,
        os.path.join(BASE_DIR, "logs", "panels.log").replace("\\", "\\\\"),
        BRIDGE_VERSION)

_BRIDGE_RE = re.compile(
    r"/\*\[BOT-PANEL-BRIDGE[^\]]*START\]\*/.*?/\*\[BOT-PANEL-BRIDGE[^\]]*END\]\*/\n?",
    re.DOTALL)

# Capture de l'api scopee : inseree apres la resolution de l'api dans chaque
# handler. Le `.resolveScopedApi(event)` du rameau `:` termine l'affectation.
_CAPTURE_ANCHOR = re.compile(
    r"(:\s*apiScope\.resolveScopedApi\(event\)\s*\n)")
_CAPTURE_INS = r"\1    scopedApi = api\n"


def patch_panel_handlers(path=PANEL_HANDLERS, log=print):
    if not os.path.exists(path):
        log(f"[bridge] panel-handlers introuvable : {path}")
        return False
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    if f"[BOT-PANEL-BRIDGE v{BRIDGE_VERSION} START]" in src:
        return False

    # Retire une greffe anterieure (bloc + captures) avant de reinjecter.
    src = _BRIDGE_RE.sub("", src)
    src = re.sub(r"\n?\s*scopedApi = api\n", "\n", src)

    if "apiScope.resolveScopedApi(event)" not in src \
            or "registerPanelHandlers" not in src:
        log("[bridge] panel-handlers : ancrages absents — structure inattendue")
        return False

    # Bloc pont insere juste apres 'use strict'.
    m = re.search(r"('use strict'|\"use strict\");?\n", src)
    if m:
        src = src[:m.end()] + "\n" + _BRIDGE_BLOCK + src[m.end():]
    else:
        src = _BRIDGE_BLOCK + src

    # Capture de scopedApi dans les deux handlers.
    src, n = _CAPTURE_ANCHOR.subn(_CAPTURE_INS, src)
    if n == 0:
        log("[bridge] panel-handlers : aucun point de capture trouve")

    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    log(f"[bridge] pont panneau injecte (v{BRIDGE_VERSION}, {n} captures)")
    return True


def patch_all(log=print):
    a = patch_login_bridge(log=log)
    b = patch_panel_handlers(log=log)
    return a or b


if __name__ == "__main__":
    patch_all()
