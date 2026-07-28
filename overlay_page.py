"""Page HTML de l'overlay, servie par le bot dans une iframe.

Chargee depuis 127.0.0.1:8765, donc same-origin avec le bot : elle interroge
/stats et /craft/* et affiche /icon sans se heurter a la CSP du client, qui
bloquerait tout acces reseau depuis l'interieur du jeu.

Deux onglets : Loot (bilan de session + loot rare, lecture seule) et Fusion
(recherche d'items a fusionner + arbre de fabrication avec fusion directe).
"""

OVERLAY_PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<title>Bot Paradox</title><style>
*{box-sizing:border-box;margin:0}
body{font:13px system-ui,sans-serif;color:#e6e8eb;padding:8px;background:transparent}
.tabs{display:flex;gap:6px;margin-bottom:10px}
.tabs b{flex:1;text-align:center;padding:7px;border-radius:7px;cursor:pointer;
        background:rgba(28,31,38,.92);color:#7d8797;font-weight:600;font-size:12px}
.tabs b.on{background:#4a9eff;color:#0d1117}
.sum{display:flex;gap:6px;margin-bottom:12px}
.stat{flex:1;background:rgba(28,31,38,.92);border-radius:8px;padding:8px 6px;
      text-align:center;border-top:2px solid #444}
.stat .ic{font-size:16px;line-height:1}
.stat .val{font-size:15px;font-weight:700;margin-top:2px;font-variant-numeric:tabular-nums}
.stat .lbl{font-size:9px;color:#7d8797;text-transform:uppercase}
.stat.xp{border-top-color:#7dd3a0}.stat.xp .val{color:#7dd3a0}
.stat.kam{border-top-color:#ffd166}.stat.kam .val{color:#ffd166}
.stat.lvl{border-top-color:#c084fc}.stat.lvl .val{color:#c084fc}
h1{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:#7d8797;
   margin:12px 0 8px;text-shadow:0 1px 3px #000}
.row{display:flex;align-items:center;gap:8px;padding:6px 8px;margin-bottom:5px;
     border-radius:8px;background:rgba(28,31,38,.95)}
.row img{width:26px;height:26px;object-fit:contain;flex:none}
.row .n{flex:1;font-weight:600;line-height:1.1;font-size:12px}
.row .q{font-size:13px;font-weight:700;font-variant-numeric:tabular-nums}
.dofus{border-left:3px solid #ffd166}.dofus .q{color:#ffd166}
.relique{border-left:3px solid #c084fc}.relique .q{color:#c084fc}
.energie{border-left:3px solid #4a9eff}.energie .q{color:#4a9eff}
.cat{font-size:9px;color:#7d8797;text-transform:uppercase}
input{width:100%;padding:8px;border-radius:7px;border:1px solid #333;
      background:#12141a;color:#e6e8eb;font:inherit;margin-bottom:6px}
.res{max-height:180px;overflow-y:auto}
.res .row{cursor:pointer}.res .row:hover{background:#2a2f3a}
.tgt{border-left:3px solid #4a9eff}
.tgt .x{cursor:pointer;color:#e05561;font-weight:700;padding:0 4px}
.have{color:#7dd3a0}.miss{color:#e05561}
.done{opacity:.45}
.cr{font-size:8px;color:#4a9eff;text-transform:uppercase;margin-left:4px}
.node{border-left:2px solid #262a33}
.node.cl{cursor:pointer}.chev{width:12px;flex:none;color:#7d8797;font-size:9px}
.fuse{cursor:pointer;background:#4a9eff;color:#0d1117;font-weight:700;border-radius:5px;padding:2px 7px;font-size:11px;margin-left:6px}
.empty{color:#5f6875;text-align:center;padding:14px;font-size:11px}
</style></head><body>
<div class="tabs">
  <b id="tabLoot" class="on" onclick="show('loot')">Loot</b>
  <b id="tabFus" onclick="show('fus')">Fusion</b>
</div>

<div id="loot">
  <div class="sum">
    <div class="stat xp"><div class="ic">&#10022;</div><div class="val" id="sxp">0</div><div class="lbl">XP</div></div>
    <div class="stat kam"><div class="ic">&#9673;</div><div class="val" id="skam">0</div><div class="lbl">Kamas</div></div>
    <div class="stat lvl"><div class="ic">&#9650;</div><div class="val" id="slvl">0</div><div class="lbl">Niveaux</div></div>
  </div>
  <h1>Loot rare</h1>
  <div id="rareList"></div>
</div>

<div id="fus" style="display:none">
  <input id="q" placeholder="Chercher un item a fusionner..." oninput="searchDeb()">
  <div class="res" id="results"></div>
  <h1>Cibles</h1>
  <div id="targets"></div>
  <h1>Detail de fabrication</h1>
  <div id="detail"></div>
</div>

<script>
const label={dofus:"Dofus",relique:"Relique",energie:"Energie"};
function compact(n){
  if(n>=1e9)return (n/1e9).toFixed(1)+"Md";
  if(n>=1e6)return (n/1e6).toFixed(1)+"M";
  if(n>=1e3)return (n/1e3).toFixed(1)+"k";
  return String(n);
}
function show(t){
  document.getElementById('loot').style.display=t==='loot'?'':'none';
  document.getElementById('fus').style.display=t==='fus'?'':'none';
  document.getElementById('tabLoot').className=t==='loot'?'on':'';
  document.getElementById('tabFus').className=t==='fus'?'on':'';
}
const img=g=>g?`<img src="/icon/${g}" alt="">`:'<div style="width:26px"></div>';
// N'ecrit dans un conteneur que si son HTML a change : sinon les <img> se
// rechargeraient a chaque rafraichissement, d'ou un clignotement.
function paint(id,html){
  const el=document.getElementById(id);
  if(el && el._h!==html){ el.innerHTML=html; el._h=html; }
}

// ── recherche (debounce) ──
let deb;
function searchDeb(){ clearTimeout(deb); deb=setTimeout(search,250); }
async function search(){
  const q=document.getElementById('q').value.trim();
  const el=document.getElementById('results');
  if(!q){ el.innerHTML=''; return; }
  let r;
  try{ r=await (await fetch('/craft/search?q='+encodeURIComponent(q))).json(); }catch(e){ return; }
  el.innerHTML=r.map(i=>
    `<div class="row" onclick="add(${i.id})">${img(i.gfx)}<span class="n">${i.name}</span><span class="q">+</span></div>`
  ).join('')||'<div class="empty">aucun resultat</div>';
}
async function add(id){ try{ await fetch('/craft/add/'+id); tick(); }catch(e){} }
async function fuse(id,ev){
  if(ev) ev.stopPropagation();
  if(!confirm("Fusionner cet item ? Les ingredients seront consommes."))return;
  try{
    const r=await (await fetch('/craft/fuse/'+id)).json();
    const ok=r.success!==false && (r.data===undefined||r.data.success!==false);
    if(!ok) alert("Echec : "+(r.error||(r.data&&r.data.error)||"refuse par le serveur"));
  }catch(e){ alert("erreur reseau"); }
  tick();
}
async function setQty(id,q){ try{ await fetch('/craft/set/'+id+'/'+q); tick(); }catch(e){} }

// ── arbre pliable ──
let treeCache=[];
const expanded=new Set();   // indices deplies (par defaut : tout replie)
function toggle(i){ expanded.has(i)?expanded.delete(i):expanded.add(i); renderTree(); }
function renderTree(){
  let cut=Infinity, html="";
  treeCache.forEach((n,i)=>{
    if(n.depth>cut) return;          // masque : un ancetre est replie
    cut=Infinity;
    // A-t-il des enfants ? (le noeud suivant est plus profond)
    const kids=n.craftable && treeCache[i+1] && treeCache[i+1].depth>n.depth;
    const open=expanded.has(i);
    if(kids && !open) cut=n.depth;   // replie : on masque ses descendants
    const ok=n.have>=n.need, sz=Math.max(10,13-n.depth);
    const chev=kids?(open?"▾":"▸"):"";
    html+=`<div class="row node ${ok?'done':''} ${kids?'cl':''}" `
      +(kids?`onclick="toggle(${i})" `:"")
      +`style="margin-left:${n.depth*13}px;font-size:${sz}px">`
      +`<span class="chev">${chev}</span>${img(n.gfx)}`
      +`<span class="n">${n.name}${n.craftable?'<span class="cr">craft</span>':''}</span>`
      +`<span class="q ${ok?'have':'miss'}">${n.have}/${n.need}</span>`
      +(n.canfuse?`<span class="fuse" onclick="fuse(${n.id},event)">Fusionner</span>`:"")
      +`</div>`;
  });
  paint("detail", html);
}

async function tick(){
  let d;
  try{ d=await (await fetch("/stats")).json(); }catch(e){ return; }
  // bilan
  document.getElementById("sxp").textContent=compact(d.session_xp||0);
  document.getElementById("skam").textContent=compact(d.session_kamas||0);
  document.getElementById("slvl").textContent="+"+(d.session_levels||0);
  // loot rare
  const rare=d.rare||[];
  paint("rareList", rare.length ? rare.map(i=>
    `<div class="row ${i.cat}">${img(i.gfx)}<div class="n">${i.name}<div class="cat">${label[i.cat]||""}</div></div><div class="q">x${i.gained}</div></div>`
  ).join("") : '<div class="empty">aucun loot rare</div>');
  // fusion
  const c=d.craft||{targets:[],tree:[]};
  paint("targets", c.targets.length ? c.targets.map(t=>
    `<div class="row tgt">${img(t.gfx)}<span class="n">${t.name}</span>`
    +`<span class="q">x${t.qty}</span>`
    +`<span class="x" onclick="setQty(${t.id},${t.qty+1})">+</span>`
    +`<span class="x" onclick="setQty(${t.id},${t.qty-1})">-</span>`
    +`<span class="x" onclick="setQty(${t.id},0)" title="retirer">&#10005;</span></div>`
  ).join("") : '<div class="empty">choisis un item ci-dessus</div>');
  treeCache=c.tree||[];
  renderTree();
}
tick();setInterval(tick,1500);
</script></body></html>"""
