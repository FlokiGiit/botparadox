"""
Patch de l'overlay loot dans le client Paradox.

Le client a une CSP stricte qui bloque tout acces a 127.0.0.1 depuis l'interieur
du jeu. On assouplit donc la CSP pour la seule origine du bot, et on pose une
iframe vers /overlay dans la bande de droite. Tout le contenu vit dans le bot :
le client ne recoit qu'une iframe, ce qui rend ce patch minimal et stable.

Idempotent et auto-reparant : une mise a jour du client reecrit le fichier et
efface le patch ; le bot rappelle patch_client() a chaque demarrage, ce qui le
reapplique. Le bloc est versionne et delimite, donc un ancien patch est retire
proprement avant d'injecter le nouveau — pas de doublons ni de residus.
"""

import os
import re

from client_config import CLIENT_HTML

ORIGIN = "http://127.0.0.1:8765"
VERSION = 8   # a incrementer si le bloc ci-dessous change

# L'overlay est un conteneur deplacable (barre du haut) et redimensionnable
# (poignee en bas a gauche). La geometrie est memorisee dans localStorage, et
# re-bornee a chaque chargement pour rester a l'ecran quelle que soit la
# resolution — ce qui remplace la hauteur fixe qui dependait de l'ecran.
# L'iframe passe en pointer-events:none pendant un glisser pour que la souris
# atteigne la page du jeu (l'iframe est cross-origin).
_TEMPLATE = """
<!-- LOOT_OVERLAY v__V__ START -->
<div id="botov">
  <div id="botovbar">&#9776; Bot Paradox<span id="botovreset" title="Replacer">&#8634;</span></div>
  <iframe id="botovframe" src="__ORIGIN__/overlay"
          style="flex:1;width:100%;border:0;background:transparent"></iframe>
  <div id="botovgrip" title="Redimensionner"></div>
</div>
<style>
#botov{position:fixed;z-index:2147483000;display:flex;flex-direction:column;
       min-width:180px;min-height:140px;box-shadow:0 0 0 1px rgba(74,158,255,.25)}
#botovbar{height:20px;flex:none;cursor:move;user-select:none;display:flex;
          align-items:center;gap:6px;padding:0 6px;background:rgba(20,22,26,.92);
          color:#7d8797;font:600 11px system-ui,sans-serif}
#botovbar #botovreset{margin-left:auto;cursor:pointer;color:#4a9eff;font-size:13px}
#botovgrip{position:absolute;left:0;bottom:0;width:16px;height:16px;
           cursor:nesw-resize;background:linear-gradient(45deg,#4a9eff 45%,transparent 45%)}
</style>
<script>(function(){
var K='botov_geo_v1',ov=document.getElementById('botov'),bar=document.getElementById('botovbar'),
    grip=document.getElementById('botovgrip'),fr=document.getElementById('botovframe'),
    rst=document.getElementById('botovreset');
function clamp(g){var W=innerWidth,H=innerHeight;
  g.w=Math.min(g.w,W);g.h=Math.min(g.h,H);
  g.l=Math.max(0,Math.min(g.l,W-g.w));g.t=Math.max(0,Math.min(g.t,H-g.h));return g;}
function apply(g){ov.style.left=g.l+'px';ov.style.top=g.t+'px';
  ov.style.width=g.w+'px';ov.style.height=g.h+'px';ov.style.right='auto';}
function geo(){return{l:ov.offsetLeft,t:ov.offsetTop,w:ov.offsetWidth,h:ov.offsetHeight};}
var B='__ORIGIN__';
function save(){var g=geo();try{localStorage.setItem(K,JSON.stringify(g));}catch(e){}
  // Le client du jeu efface son localStorage a chaque lancement : on persiste
  // aussi cote bot pour que la position/taille survivent.
  try{fetch(B+'/overlay/geo?l='+Math.round(g.l)+'&t='+Math.round(g.t)
        +'&w='+Math.round(g.w)+'&h='+Math.round(g.h));}catch(e){}}
function def(){return clamp({w:320,h:Math.round(innerHeight*0.7),l:innerWidth-320,t:0});}
function local(){try{var g=JSON.parse(localStorage.getItem(K));if(g&&g.w)return g;}catch(e){}return null;}
function init(){
  fetch(B+'/overlay/geo').then(function(r){return r.json();}).then(function(g){
    apply(clamp(g&&g.w?g:(local()||def())));
  }).catch(function(){apply(clamp(local()||def()));});}
function drag(e,mode){e.preventDefault();fr.style.pointerEvents='none';
  var sx=e.clientX,sy=e.clientY,b=geo();
  function mv(e){var dx=e.clientX-sx,dy=e.clientY-sy,g;
    if(mode==='move')g={l:b.l+dx,t:b.t+dy,w:b.w,h:b.h};
    else g={l:b.l+dx,t:b.t,w:b.w-dx,h:b.h+dy};
    if(g.w<180){g.w=180;}if(g.h<140){g.h=140;}apply(clamp(g));}
  function up(){document.removeEventListener('mousemove',mv);
    document.removeEventListener('mouseup',up);fr.style.pointerEvents='';save();}
  document.addEventListener('mousemove',mv);document.addEventListener('mouseup',up);}
bar.addEventListener('mousedown',function(e){if(e.target!==rst)drag(e,'move');});
grip.addEventListener('mousedown',function(e){drag(e,'resize');});
rst.addEventListener('click',function(){try{localStorage.removeItem(K);}catch(e){}apply(def());});
addEventListener('resize',function(){apply(clamp(geo()));});
init();
})();</script>
<script>(function(){
  // Panneau donjon du serveur : il selectionne "Donjon d'Incarnam" par defaut
  // au lieu du premier favori (les favoris sont pourtant en haut de la liste).
  // A l'ouverture du panneau, on clique le premier item -> ton favori est
  // selectionne. On utilise leur propre UI, on ne recree rien.
  var handled=null;
  new MutationObserver(function(){
    var panel=document.querySelector('.fp2-panel--mod-dungeon');
    if(!panel){ handled=null; return; }
    if(handled===panel) return;
    var first=panel.querySelector('.feature-panels__dj-list-item')
              ||panel.querySelector('.fp2__list-item');
    if(first){
      handled=panel;
      if(!first.classList.contains('is-selected')){ try{ first.click(); }catch(e){} }
    }
  }).observe(document.body,{childList:true,subtree:true});
})();</script>
<!-- LOOT_OVERLAY v__V__ END -->
"""
BLOCK = "\n" + _TEMPLATE.replace("__ORIGIN__", ORIGIN).replace("__V__", str(VERSION))

# Attrape n'importe quelle version du bloc, pour le retirer avant de reinjecter.
_BLOCK_RE = re.compile(
    r"\n?<!-- LOOT_OVERLAY[^>]*START -->.*?<!-- LOOT_OVERLAY[^>]*END -->\n?",
    re.DOTALL)
# Ancien format (v1) : un simple marqueur + iframe sans balises de fin.
_OLD_RE = re.compile(
    r"\n?<!-- LOOT_OVERLAY_PATCH -->\s*<iframe[^>]*?/overlay[^>]*></iframe>\n?",
    re.DOTALL)


def _relax_csp(html):
    """Ajoute l'origine du bot aux directives CSP qui la bloqueraient."""
    def add(directive, text):
        pat = re.compile(r"(" + directive + r"\s+)([^;\"]*)")
        m = pat.search(text)
        if not m or ORIGIN in m.group(2):
            return text
        return text[:m.end(1)] + ORIGIN + " " + text[m.end(1):]

    html = add("connect-src", html)
    html = add("img-src", html)
    # frame-src absent : sans lui l'iframe tombe sous default-src 'self'.
    if "frame-src" not in html:
        html = re.sub(r'(content="\s*default-src[^"]*?)"',
                      r'\1 frame-src ' + ORIGIN + ';"', html, count=1)
    return html


def patch_client(path=CLIENT_HTML, log=print):
    """Applique (ou met a jour) le patch. Renvoie True si le fichier a change."""
    if not os.path.exists(path):
        log(f"[patch] client introuvable : {path}")
        return False

    with open(path, "r", encoding="utf-8") as f:
        original = f.read()

    # Deja a la bonne version et CSP en place : rien a faire.
    if f"LOOT_OVERLAY v{VERSION} START" in original and ORIGIN in original:
        return False

    # Sauvegarde du fichier propre, une seule fois (avant tout patch).
    backup = path + ".orig"
    if not os.path.exists(backup) and "LOOT_OVERLAY" not in original:
        with open(backup, "w", encoding="utf-8") as f:
            f.write(original)

    # Retire tout patch precedent (nouveau ou ancien format).
    html = _OLD_RE.sub("", _BLOCK_RE.sub("", original))
    html = _relax_csp(html)
    if "</body>" in html:
        html = html.replace("</body>", BLOCK + "</body>", 1)
    else:
        html += BLOCK

    if html == original:
        log("[patch] rien a modifier (structure inattendue)")
        return False

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"[patch] overlay loot injecte (v{VERSION})")
    return True


if __name__ == "__main__":
    patch_client()
