"""Page HTML de l'overlay, servie par le bot dans une iframe.

Chargee depuis 127.0.0.1:8765, donc same-origin avec le bot : elle interroge
/stats et /craft/* et affiche /icon sans se heurter a la CSP du client, qui
bloquerait tout acces reseau depuis l'interieur du jeu.

Deux onglets : Loot (bilan + loot rare) et Fusion (recherche + arbre).
"""

OVERLAY_PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<title>Bot Paradox</title><style>
*{box-sizing:border-box;margin:0}
:root{
  --bg:rgba(14,11,12,.94); --panel:rgba(31,24,25,.96); --panel2:rgba(39,31,32,.96);
  --edge:#2a2022; --text:#ece6e5; --muted:#9a8c8b; --faint:#6b5f5f;
  --accent:#c4675f; --accent2:#8e4640; --ok:#86b48f; --bad:#e8574f;
  --xp:#86b48f; --kam:#d9a85c; --lvl:#c9a0e8; --ink:#150f0f;
}
body{font:12.5px/1.35 "Segoe UI Variable Text","Segoe UI",system-ui,sans-serif;
     color:var(--text);padding:9px;background:transparent}
/* onglets collants : la liste de loot peut defiler, la navigation reste la */
.tabs{display:flex;gap:5px;margin-bottom:9px;position:sticky;top:-9px;z-index:5;
      padding:9px 0 6px;background:linear-gradient(180deg,rgba(14,11,12,.96) 70%,transparent)}
.tabs b{flex:1;text-align:center;padding:7px;border-radius:8px;cursor:pointer;
        background:var(--panel);color:var(--muted);font-weight:700;font-size:11px;
        letter-spacing:.06em;text-transform:uppercase;
        border:1px solid var(--edge);transition:background .18s,color .18s,border-color .18s}
.tabs b:hover{color:var(--text)}
.tabs b.on{background:linear-gradient(180deg,#3a2422,#2a1c1c);color:var(--accent);
           border-color:var(--accent2)}
.sum{display:grid;grid-template-columns:1fr 1fr;gap:5px;margin-bottom:2px}
/* Six tuiles de meme taille : niveau et Omega se lisent en paire, jetons et
   kamas aussi. L'Omega avait sa place ici, pas en petite ligne sous le niveau. */
.stat{background:var(--panel);border-radius:9px;padding:7px 8px 6px;
      border:1px solid var(--edge);position:relative;overflow:hidden}
.stat::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--edge)}
.stat .lbl{font-size:9px;color:var(--faint);text-transform:uppercase;letter-spacing:.08em;
           font-weight:600}
.stat .val{font-size:16px;font-weight:700;margin-top:2px;font-variant-numeric:tabular-nums}
.stat.xp::before{background:var(--xp)}.stat.xp .val{color:var(--xp)}
.stat.kam::before{background:var(--kam)}.stat.kam .val{color:var(--kam)}
.stat.inv::before{background:var(--kam)}.stat.inv .val{color:var(--kam)}
.stat.lvl::before{background:var(--lvl)}.stat.lvl .val{color:var(--lvl)}
.stat.jet::before{background:var(--accent)}.stat.jet .val{color:var(--accent)}
.stat.omg::before{background:var(--lvl)}.stat.omg .val{color:var(--lvl)}
.stat .sub{font-size:10px;color:var(--faint);margin-top:1px}
h1{font-size:9.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--faint);
   font-weight:700;margin:13px 0 7px;display:flex;align-items:center;gap:7px}
h1::after{content:"";flex:1;height:1px;background:var(--edge);order:2}
/* Action discrete dans un titre de section : visible seulement au survol. */
.act{order:3;cursor:pointer;color:var(--faint);font-size:9px;letter-spacing:.06em;
     border:1px solid var(--edge);border-radius:5px;padding:2px 6px;opacity:.45;
     transition:opacity .15s,color .15s,border-color .15s}
.act:hover{opacity:1;color:var(--accent);border-color:var(--accent2)}
.row{display:flex;align-items:center;gap:8px;padding:6px 8px;margin-bottom:4px;
     border-radius:8px;background:var(--panel);border:1px solid var(--edge);
     animation:fadeIn .25s ease}
@keyframes fadeIn{from{opacity:0;transform:translateY(3px)}to{opacity:1;transform:none}}
.row img{width:25px;height:25px;object-fit:contain;flex:none}
.row .n{flex:1;font-weight:600;line-height:1.15;font-size:12px}
.row .q{font-size:13px;font-weight:700;font-variant-numeric:tabular-nums}
.dofus{border-left:3px solid #e8c98a}.dofus .q{color:#e8c98a}
.relique{border-left:3px solid #c9a0e8}.relique .q{color:#c9a0e8}
.energie{border-left:3px solid #7fa6c9}.energie .q{color:#7fa6c9}
.essence{border-left:3px solid var(--ok)}.essence .q{color:var(--ok)}
.box{border-left:3px solid var(--accent)}.box .q{color:var(--accent)}
.cat{font-size:9px;color:var(--faint);text-transform:uppercase;letter-spacing:.06em}
input,select{width:100%;padding:8px 9px;border-radius:8px;border:1px solid var(--edge);
      background:rgba(14,11,12,.9);color:var(--text);font:inherit;margin-bottom:6px}
input:focus{outline:none;border-color:var(--accent)}
input::placeholder{color:var(--faint)}
.go{width:100%;padding:9px;border:0;border-radius:8px;cursor:pointer;
    background:linear-gradient(180deg,#c4675f,#8e4640);color:var(--ink);font-weight:700}
.res{max-height:170px;overflow-y:auto}
.res .row{cursor:pointer}.res .row:hover{border-color:var(--accent)}
.tgt{border-left:3px solid var(--accent)}
.tgt .x{cursor:pointer;color:var(--muted);font-weight:700;padding:0 4px}
.tgt .x:hover{color:var(--accent)}
.have{color:var(--ok)}.miss{color:var(--bad)}
.done{opacity:.45}
.cr{font-size:8px;color:var(--accent);text-transform:uppercase;margin-left:4px}
.node{border-left:2px solid var(--edge)}
.node.cl{cursor:pointer}.chev{width:12px;flex:none;color:var(--faint);font-size:9px}
.fuse{cursor:pointer;background:var(--accent);color:var(--ink);font-weight:700;border-radius:5px;
      padding:2px 7px;font-size:11px;margin-left:6px}
.fuse:hover{background:var(--accent2);color:var(--text)}
.empty{color:var(--faint);text-align:center;padding:13px;font-size:11px}
::-webkit-scrollbar{width:8px}
::-webkit-scrollbar-thumb{background:var(--panel2);border-radius:4px}
::-webkit-scrollbar-thumb:hover{background:var(--accent2)}
::-webkit-scrollbar-track{background:transparent}
</style></head><body>
<div class="tabs">
  <b id="tabLoot" class="on" onclick="show('loot')">Loot</b>
  <b id="tabFus" onclick="show('fus')">Fusion</b>
</div>

<div id="loot">
  <div class="sum">
    <div class="stat inv"><div class="lbl">Kamas inventaire</div><div class="val" id="sinv">0</div></div>
    <div class="stat lvl"><div class="lbl">Niveau</div><div class="val" id="slvl">—</div>
      <div class="sub" id="slvlsub">session +0</div></div>
    <div class="stat xp"><div class="lbl">XP session</div><div class="val" id="sxp">0</div></div>
    <div class="stat kam"><div class="lbl">Kamas session</div><div class="val" id="skam">0</div></div>
    <div class="stat omg" title="Progression au-dela du niveau 16000"><div class="lbl">Oméga</div>
      <div class="val" id="somega">—</div>
      <div class="sub" id="somegasub"></div></div>
    <div class="stat jet"><div class="lbl">Jetons de prestige</div>
      <div class="val" id="sjet">0</div>
      <div class="sub" id="sjettot"></div></div>
  </div>
  <h1><span>Loot rare</span><b class="act" onclick="clearRare()"
      title="Vider la liste (les objets restent dans le sac)">vider</b></h1>
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
const label={dofus:"Dofus",relique:"Relique",energie:"Energie",essence:"Essence",box:"Box"};
function compact(n){
  if(n==null||n===undefined)return "0";
  n=+n;
  if(n>=1e9)return (n/1e9).toFixed(1)+"Md";
  if(n>=1e6)return (n/1e6).toFixed(1)+"M";
  if(n>=1e3)return (n/1e3).toFixed(1)+"k";
  return String(n);
}
// L'onglet actif est memorise : cette page est servie par le bot, donc son
// localStorage survit au relancement du client (qui vide le sien).
function show(t){
  ['loot','fus'].forEach(function(x){
    document.getElementById(x).style.display=x===t?'':'none';
    document.getElementById('tab'+x[0].toUpperCase()+x.slice(1)).className=x===t?'on':'';
  });
  try{localStorage.setItem('botov_tab',t);}catch(e){}
}
try{var _t=localStorage.getItem('botov_tab');if(_t==='fus')show('fus');}catch(e){}
const img=g=>g?`<img src="/icon/${g}" alt="">`:'<div style="width:26px"></div>';
function paint(id,html){
  const el=document.getElementById(id);
  if(el && el._h!==html){ el.innerHTML=html; el._h=html; }
}

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
// Vide le compteur de loot rare (les objets restent dans le sac) : la liste
// d'une session de plusieurs heures devient illisible.
async function clearRare(){
  if(!confirm("Vider la liste du loot rare ?"))return;
  try{ await fetch('/rare/clear'); }catch(e){}
  tick();
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

let treeCache=[];
const expanded=new Set();
function toggle(i){ expanded.has(i)?expanded.delete(i):expanded.add(i); renderTree(); }
function renderTree(){
  let cut=Infinity, html="";
  treeCache.forEach((n,i)=>{
    if(n.depth>cut) return;
    cut=Infinity;
    const kids=n.craftable && treeCache[i+1] && treeCache[i+1].depth>n.depth;
    const open=expanded.has(i);
    if(kids && !open) cut=n.depth;
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
  document.getElementById("sinv").textContent=compact(d.kamas||0);
  document.getElementById("skam").textContent=compact(d.session_kamas||0);
  document.getElementById("sxp").textContent=compact(d.session_xp||0);
  const lvl=d.level;
  document.getElementById("slvl").textContent=(lvl==null||lvl==="")?"—":String(lvl);
  document.getElementById("slvlsub").textContent="session +"+(d.session_levels||0);
  // Au-dela du cap le niveau ne bouge plus : c'est l'Omega qui progresse.
  document.getElementById("somega").textContent=
    d.omega!=null?("Ω "+d.omega):"—";
  document.getElementById("somegasub").textContent=
    d.prestige_rank!=null?("prestige "+d.prestige_rank):"";
  document.getElementById("sjet").textContent="+"+compact(d.prestige||0);
  document.getElementById("sjettot").textContent=
    d.total_prestige?("total "+compact(d.total_prestige)):"";
  const rare=d.rare||[];
  paint("rareList", rare.length ? rare.map(i=>
    `<div class="row ${i.cat}">${img(i.gfx)}<div class="n">${i.name}<div class="cat">${label[i.cat]||""}</div></div><div class="q">x${i.gained}</div></div>`
  ).join("") : '<div class="empty">aucun loot rare</div>');
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
