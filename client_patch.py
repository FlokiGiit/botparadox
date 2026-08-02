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
VERSION = 14   # a incrementer si le bloc ci-dessous change

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
<script>(function(){
  // Pano evo : le panneau "APPLIQUER UNE ESSENCE" n'applique qu'UNE essence par
  // clic. On ajoute a cote de LEUR bouton un champ nombre + "Appliquer xN" qui
  // reselectionne l'essence choisie et reclique LEUR bouton N fois, avec une
  // pause entre chaque pour laisser le serveur repondre. On s'arrete si
  // l'essence est epuisee (l'option disparait) ou si leur bouton se desactive.
  // On ne recree rien : on pilote leur propre UI, exactement comme N clics.
  var TAG='botEssBatch', pending=false, running=false;
  function findBtn(){
    return [].slice.call(document.querySelectorAll('button')).find(function(b){
      return /appliquer l.essence/i.test((b.textContent||''));
    });
  }
  function findSelect(){
    return [].slice.call(document.querySelectorAll('select')).find(function(s){
      return s.options&&s.options.length&&/choisir une essence/i.test(s.options[0].text||'');
    });
  }
  function reselect(sel,val){
    try{
      var d=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value');
      if(d&&d.set){d.set.call(sel,val);}else{sel.value=val;}
      sel.dispatchEvent(new Event('input',{bubbles:true}));
      sel.dispatchEvent(new Event('change',{bubbles:true}));
    }catch(e){try{sel.value=val;}catch(_){}}
  }
  function inject(){
    var btn=findBtn(), sel=findSelect();
    if(!btn||!sel) return;
    var host=btn.parentElement; if(!host) return;
    if(host.querySelector('#'+TAG)) return;   // deja injecte
    var wrap=document.createElement('div');
    wrap.id=TAG;
    wrap.style.cssText='display:flex;gap:6px;margin-top:8px;align-items:center;flex-wrap:wrap';
    var num=document.createElement('input');
    num.type='number';num.min='1';num.value='10';num.title='Nombre d\\'applications';
    num.style.cssText='width:66px;padding:6px;border-radius:6px;border:1px solid #6a5a3a;background:rgba(0,0,0,.25);color:#f0e6d2;font:inherit';
    var go=document.createElement('button');
    go.type='button';go.textContent='Appliquer \\u00d7N';
    go.style.cssText='padding:6px 12px;border:0;border-radius:6px;cursor:pointer;background:#c9a24b;color:#241a08;font-weight:700;font:inherit';
    var stat=document.createElement('span');
    stat.style.cssText='font:600 12px system-ui,sans-serif;color:#bda87e;margin-left:2px';
    // Quand on choisit une essence, pre-remplit le nombre avec le stock (xN).
    sel.addEventListener('change',function(){
      var o=sel.options[sel.selectedIndex];
      var m=(o?o.text:'').match(/[x\\u00d7]\\s*(\\d+)/i);
      if(m){num.value=m[1];}
    });
    go.addEventListener('click',function(){
      if(running) return;
      var n=parseInt(num.value,10)||0;
      var s=findSelect();
      if(!s||s.selectedIndex<=0){stat.textContent='choisis une essence';return;}
      if(n<1){stat.textContent='nombre invalide';return;}
      var val=s.value, done=0;
      running=true; go.disabled=true;
      function finish(msg){running=false;go.disabled=false;if(stat.isConnected)stat.textContent=msg;}
      function step(){
        if(done>=n) return finish('termine \\u2014 '+done+' appliqu\\u00e9e(s)');
        var sel2=findSelect(), btn2=findBtn();
        if(!sel2||!btn2) return finish('panneau ferm\\u00e9 \\u2014 '+done);
        var opt=[].slice.call(sel2.options).find(function(o){return o.value===val;});
        if(!opt) return finish('essence \\u00e9puis\\u00e9e \\u2014 '+done);
        // Cap atteint (ou doublon interdit) : inutile d'insister, et c'est ce
        // qui bloquait l'UI en spammant une essence qui ne peut plus rien faire.
        if(/cap atteint|doublon interdit/i.test(opt.text||''))
          return finish('cap atteint \\u2014 '+done);
        if(sel2.value!==val||sel2.selectedIndex<=0) reselect(sel2,val);
        setTimeout(function(){
          var b=findBtn();
          if(!b||b.disabled) return finish('bouton indispo \\u2014 '+done);
          try{b.click();}catch(e){return finish('erreur \\u2014 '+done);}
          done++; if(stat.isConnected) stat.textContent=done+'/'+n;
          setTimeout(step,700);
        },140);
      }
      step();
    });
    wrap.appendChild(num);wrap.appendChild(go);wrap.appendChild(stat);
    host.appendChild(wrap);
  }
  function schedule(){if(pending)return;pending=true;setTimeout(function(){pending=false;inject();},400);}
  new MutationObserver(schedule).observe(document.body,{childList:true,subtree:true});
  inject();
})();</script>
<script>(function(){
  // Panneau Rarete : "Tenter" relance la rarete de l'item (coute 1 Energie
  // rare) et ECRASE le jet precedent. On ajoute "Auto : roll jusqu'a Rx" qui
  // relance UN roll a la fois, lit la rarete apres chaque, et S'ARRETE des
  // qu'on atteint la cible (R9/R10) ou que la ressource est epuisee -> plus
  // jamais de bon jet ecrase. On pilote LEUR UI (leur bouton, leurs options),
  // on ne recree rien : c'est exactement ce qu'un joueur ferait a la main.
  // SECURITE : si la rarete est illisible, on stoppe (jamais de roll a
  // l'aveugle). Rien n'est force cote serveur : les probas restent les leurs.
  var TAG='botRareRoll', pending=false, running=false, count=0;
  function q(s){return document.querySelector(s);}
  function rollBtn(){return q('.rp__roll-btn');}
  function rarity(){
    var el=q('.rp__rarity-big-badge'); if(!el) return -1;
    var m=(el.textContent||'').match(/R(\\d+)/); return m?parseInt(m[1],10):-1;
  }
  function dispo(){
    var cards=[].slice.call(document.querySelectorAll('.rp__cost-info .fp2__metric-card'));
    for(var i=0;i<cards.length;i++){
      if(/dispo/i.test(cards[i].textContent||'')){
        var v=cards[i].querySelector('.fp2__metric-value');
        return v?(parseInt((v.textContent||'').replace(/\\D/g,''),10)||0):1e9;
      }
    }
    return 1e9;
  }
  function setNum(input,val){
    try{var d=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');
      if(d&&d.set){d.set.call(input,String(val));}else{input.value=String(val);}
      input.dispatchEvent(new Event('input',{bubbles:true}));
      input.dispatchEvent(new Event('change',{bubbles:true}));}catch(e){}
  }
  function setCheck(txt,desired){
    var lbls=[].slice.call(document.querySelectorAll('.rp__checkbox-label'));
    for(var i=0;i<lbls.length;i++){
      if(new RegExp(txt,'i').test(lbls[i].textContent||'')){
        var cb=lbls[i].querySelector('input[type=checkbox]');
        if(cb&&!cb.disabled&&cb.checked!==desired){try{cb.click();}catch(e){}}
        return;
      }
    }
  }
  function inject(){
    var btn=rollBtn(); if(!btn) return;
    var cta=btn.closest('.rp__roll-cta-row')||btn.parentElement;
    var host=(cta&&cta.parentElement)||cta; if(!host) return;
    if(host.querySelector('#'+TAG)) return;
    var wrap=document.createElement('div'); wrap.id=TAG;
    wrap.style.cssText='display:flex;gap:8px;align-items:center;flex-wrap:wrap;'
      +'margin-top:8px;padding-top:8px;border-top:1px solid rgba(148,163,184,.25)';
    var lbl=document.createElement('span'); lbl.textContent='Auto : roll jusqu\\'a';
    lbl.style.cssText='font:600 12px system-ui,sans-serif;color:#cbd5e1';
    var sel=document.createElement('select');
    sel.style.cssText='padding:5px;border-radius:6px;background:rgba(0,0,0,.25);'
      +'color:#f0e6d2;border:1px solid #6a5a3a;font:inherit';
    ['9','10','8','7','6','5'].forEach(function(v){var o=document.createElement('option');
      o.value=v;o.textContent='R'+v+'+';sel.appendChild(o);});
    var go=document.createElement('button'); go.type='button'; go.textContent='\\u25b6 Lancer';
    go.style.cssText='padding:6px 12px;border:0;border-radius:6px;cursor:pointer;'
      +'background:#eab308;color:#241a08;font-weight:800;font:inherit';
    var stop=document.createElement('button'); stop.type='button'; stop.textContent='\\u25a0 Stop';
    stop.style.cssText='padding:6px 12px;border:0;border-radius:6px;cursor:pointer;'
      +'background:#e05561;color:#fff;font-weight:700;font:inherit;display:none';
    var stat=document.createElement('span');
    stat.style.cssText='font:600 12px system-ui,sans-serif;color:#bda87e';
    function finish(msg){running=false;go.style.display='';stop.style.display='none';
      if(stat.isConnected)stat.textContent=msg;}
    function loop(){
      if(!running) return;
      var target=parseInt(sel.value,10)||9;
      var r=rarity();
      // Rarete illisible -> on n'ose pas relancer (ca ecraserait un bon jet).
      if(r<0){ finish('rarete illisible - arret'); return; }
      if(r>=target){ finish('\\u2705 R'+r+' atteint ('+count+' rolls)'); return; }
      if(dispo()<1){ finish('plus d\\'Energie rare ('+count+' rolls)'); return; }
      if(count>=20000){ finish('limite de securite ('+count+')'); return; }
      var b=rollBtn(); if(!b||b.disabled){ finish('bouton indispo ('+count+')'); return; }
      try{b.click();}catch(e){ finish('erreur'); return; }
      count++;
      setTimeout(function(){
        var dlg=q('.rp__dialog');
        if(dlg){var c=[].slice.call(dlg.querySelectorAll('button')).find(function(x){
          return /confirm|valid|^\\s*oui|tenter/i.test((x.textContent||'').trim());});
          if(c){try{c.click();}catch(e){}}}
        if(stat.isConnected) stat.textContent=count+' rolls\\u2026 (R'+rarity()+')';
        setTimeout(loop,500);
      },160);
    }
    go.addEventListener('click',function(){
      if(running) return;
      running=true;count=0;go.style.display='none';stop.style.display='';
      var ni=q('.rp__roll-count-input'); if(ni) setNum(ni,1);   // 1 roll a la fois
      setCheck('sans animation',true);      // resultat direct = plus rapide
      setCheck('toujours confirmer',true);  // pas de popup a chaque roll
      setTimeout(loop,220);
    });
    stop.addEventListener('click',function(){ finish('arrete ('+count+' rolls)'); });
    wrap.appendChild(lbl);wrap.appendChild(sel);wrap.appendChild(go);
    wrap.appendChild(stop);wrap.appendChild(stat);
    host.appendChild(wrap);
  }
  function schedule(){if(pending)return;pending=true;setTimeout(function(){pending=false;inject();},400);}
  new MutationObserver(schedule).observe(document.body,{childList:true,subtree:true});
  inject();
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
