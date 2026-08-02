"""Page « Assistant de combat » servie par le tableau de bord (/assist).

Affiche en direct la grille isométrique du combat (combattants, boss, PV) et,
quand on clique un de ses sorts, surligne les cases OÙ LE SORT PEUT ÊTRE LANCÉ
(portée + ligne de vue), calculées en local côté bot (aucune requête serveur).

Le joueur joue son combat à la main : la page ne fait qu'indiquer où viser.
Anti-clignotement : le canvas n'est dimensionné qu'une fois, les boutons de
sorts ne sont reconstruits que si la liste change, l'info se met à jour par
texte. On ne redessine que quand l'état change réellement.
"""

ASSIST_PAGE = r"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Assistant de combat</title>
<style>
  :root{--bg:#0d1117;--fg:#e6e8eb;--sub:#8b949e;--card:#161b22;--edge:#262a33;
        --ok:#3fb950;--enemy:#e05561;--me:#4a9eff;--boss:#e3b341;--ally:#7ee787}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
       font-family:Segoe UI,Roboto,system-ui,sans-serif;font-size:13px}
  header{padding:10px 14px;border-bottom:1px solid var(--edge);
         display:flex;align-items:center;gap:12px;flex-wrap:wrap}
  h1{font-size:15px;margin:0;font-weight:700}
  .sub{color:var(--sub);font-size:12px}
  #status{margin-left:auto;font-weight:600}
  #spells{display:flex;gap:6px;flex-wrap:wrap;padding:10px 14px;
          border-bottom:1px solid var(--edge);min-height:20px}
  .sp{display:flex;align-items:center;gap:6px;padding:6px 9px;border-radius:8px;
      background:var(--card);border:1px solid var(--edge);cursor:pointer;color:var(--fg)}
  .sp:hover{border-color:#3d4450}
  .sp.on{border-color:var(--ok);background:#12291a}
  .sp img{width:22px;height:22px;border-radius:4px}
  .sp .pa{color:var(--boss);font-weight:700}
  #main{display:flex;gap:0;align-items:flex-start}
  #wrap{flex:1;overflow:auto;padding:12px}
  canvas{display:block}
  #info{width:280px;flex:none;border-left:1px solid var(--edge);
        padding:12px 14px;height:calc(100vh - 92px);overflow:auto}
  @media(max-width:760px){#main{flex-direction:column}#info{width:100%;
        border-left:0;border-top:1px solid var(--edge);height:auto}}
  .box{background:var(--card);border:1px solid var(--edge);border-radius:10px;
       padding:10px 12px;margin-bottom:10px}
  .box h2{font-size:11px;margin:0 0 8px;color:var(--sub);text-transform:uppercase;
          letter-spacing:.06em;font-weight:600}
  .big{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}
  .row{display:flex;align-items:center;gap:8px;margin:6px 0}
  .dot{width:11px;height:11px;border-radius:50%;flex:none}
  .nm{flex:none;width:56px}
  .hpwrap{flex:1;height:12px;background:#0d1117;border-radius:6px;overflow:hidden}
  .hp{height:100%;border-radius:6px}
  .hpn{flex:none;font-size:11px;color:var(--sub);width:78px;text-align:right;
       font-variant-numeric:tabular-nums}
  .tip{color:var(--sub);line-height:1.5}
  b.ok{color:var(--ok)} b.no{color:var(--enemy)}
</style></head><body>
<header>
  <h1>Assistant de combat</h1>
  <span class="sub">clique un sort → cases où viser en vert</span>
  <span id="status" class="sub">…</span>
</header>
<div id="spells"></div>
<div id="main">
  <div id="wrap"><canvas id="c" width="10" height="10"></canvas></div>
  <div id="info">
    <div class="box"><h2>Toi</h2><div class="big" id="mypapm">— PA · — PM</div>
      <div class="sub" id="mycell"></div></div>
    <div class="box" id="spellbox"><h2>Sort sélectionné</h2>
      <div id="spelldet" class="tip">Aucun sort sélectionné.</div></div>
    <div class="box"><h2>Combattants</h2><div id="fighters" class="tip">—</div></div>
    <div class="tip" id="hint">En attente d'un combat…</div>
  </div>
</div>
<script>
var CW=30, CH=16, STRIDE=29, W=15;
var state=null, selected=null, valid={cells:[],center:null};
var validSet=new Set(), sizedN=-1, spellSig="";

function rowcol(cell){var p=Math.floor(cell/STRIDE),r=cell%STRIDE;
  return r<W?[p*2,r]:[p*2+1,r-W];}
function screenXY(cell){var rc=rowcol(cell),hr=rc[0],col=rc[1];
  return [col*CW+(hr%2?CW/2:0), hr*(CH/2)];}
function diamond(ctx,cx,cy,fill,stroke){
  ctx.beginPath();ctx.moveTo(cx,cy-CH/2);ctx.lineTo(cx+CW/2,cy);
  ctx.lineTo(cx,cy+CH/2);ctx.lineTo(cx-CW/2,cy);ctx.closePath();
  if(fill){ctx.fillStyle=fill;ctx.fill();}
  if(stroke){ctx.strokeStyle=stroke;ctx.lineWidth=1;ctx.stroke();}}

function sizeCanvas(n){
  var cv=document.getElementById('c'),maxX=0,maxY=0;
  for(var i=0;i<n;i++){var s=screenXY(i);if(s[0]>maxX)maxX=s[0];if(s[1]>maxY)maxY=s[1];}
  cv.width=maxX+CW+2;cv.height=maxY+CH+2;sizedN=n;
}
function draw(){
  var cv=document.getElementById('c'),ctx=cv.getContext('2d');
  ctx.clearRect(0,0,cv.width,cv.height);
  if(!state||!state.active||!state.cells)return;
  var off=CW/2+1;
  var occ={}; (state.fighters||[]).forEach(function(f){occ[f.cell]=f;});
  for(var i=0;i<state.cells.length;i++){
    var cell=state.cells[i];
    var show = cell.w || occ[i] || validSet.has(i);
    if(!show) continue;
    var s=screenXY(i),cx=s[0]+off,cy=s[1]+CH/2+1;
    diamond(ctx,cx,cy, cell.w?'#1b2130':'#141922', '#20252f');
  }
  validSet.forEach(function(i){
    var s=screenXY(i),cx=s[0]+off,cy=s[1]+CH/2+1;
    diamond(ctx,cx,cy,'rgba(63,185,80,.55)','#3fb950');
    ctx.fillStyle='#dff5e4';ctx.font='9px monospace';ctx.textAlign='center';
    ctx.fillText(i,cx,cy+3);
  });
  (state.fighters||[]).forEach(function(f){
    var s=screenXY(f.cell),cx=s[0]+off,cy=s[1]+CH/2+1;
    var col=f.me?'#4a9eff':(f.boss?'#e3b341':(f.enemy?'#e05561':'#7ee787'));
    ctx.beginPath();ctx.arc(cx,cy,f.boss?7:5,0,7);ctx.fillStyle=col;ctx.fill();
    if(f.boss){ctx.strokeStyle='#fff';ctx.lineWidth=1.5;ctx.stroke();}
  });
}

function renderSpells(){
  var sig=(state.spells||[]).map(function(s){return s.id;}).join(",");
  if(sig===spellSig) return;          // ne reconstruit que si la liste change
  spellSig=sig;
  var box=document.getElementById('spells');
  box.innerHTML='';
  if(!state.spells||!state.spells.length){
    box.innerHTML='<span class="sub">Aucun sort (hors combat).</span>';return;}
  state.spells.forEach(function(sp){
    var b=document.createElement('div');b.className='sp'+(selected===sp.id?' on':'');
    b.dataset.id=sp.id;
    b.innerHTML='<img src="/icon/sort/'+sp.id+'" onerror="this.style.display=\'none\'">'
      +'<span>'+sp.name+'</span><span class="pa">'+sp.pa+'&nbsp;PA</span>';
    b.onclick=function(){selected=(selected===sp.id?null:sp.id);markSel();refreshValid();};
    box.appendChild(b);
  });
}
function markSel(){
  document.querySelectorAll('#spells .sp').forEach(function(b){
    b.className='sp'+(String(selected)===b.dataset.id?' on':'');});
}

function spellById(id){return (state.spells||[]).find(function(s){return s.id===id;});}
function renderInfo(){
  var st=document.getElementById('status');
  document.getElementById('mypapm').textContent =
    (state.active?(state.my_pa+' PA · '+state.my_pm+' PM'):'— PA · — PM');
  document.getElementById('mycell').textContent =
    state.active&&state.my_cell!=null?('cellule '+state.my_cell):'';
  if(!state.active){
    st.textContent='en attente d\'un combat';st.style.color='#8b949e';
    document.getElementById('fighters').innerHTML='—';
    document.getElementById('hint').textContent='Lance un combat : la grille et tes sorts apparaîtront ici.';
    return;
  }
  st.textContent='combat en cours';st.style.color='#3fb950';
  // combattants
  var order=(state.fighters||[]).slice().sort(function(a,b){
    return (a.me?0:a.boss?1:a.enemy?2:3)-(b.me?0:b.boss?1:b.enemy?2:3);});
  document.getElementById('fighters').innerHTML=order.map(function(f){
    var col=f.me?'var(--me)':(f.boss?'var(--boss)':(f.enemy?'var(--enemy)':'var(--ally)'));
    var nm=f.me?'Toi':(f.boss?'BOSS':(f.enemy?'Ennemi':'Allié'));
    var pct=f.hpmax?Math.max(0,Math.min(100,Math.round(f.hp*100/f.hpmax))):0;
    return '<div class="row"><span class="dot" style="background:'+col+'"></span>'
      +'<span class="nm">'+nm+'</span>'
      +'<span class="hpwrap"><span class="hp" style="width:'+pct+'%;background:'+col+'"></span></span>'
      +'<span class="hpn">'+f.hp.toLocaleString('fr')+'</span></div>';
  }).join('');
  // détail du sort
  var det=document.getElementById('spelldet');
  if(selected===null){det.innerHTML='Aucun sort sélectionné.<br>Clique un sort en haut.';}
  else{
    var sp=spellById(selected)||{};
    var enough=state.my_pa>=sp.pa;
    var n=valid.cells?valid.cells.length:0;
    det.innerHTML='<b>'+(sp.name||'')+'</b><br>'
      +'Coût : <b class="'+(enough?'ok':'no')+'">'+sp.pa+' PA</b>'
      +' (tu as '+state.my_pa+')<br>'
      +'Portée : '+sp.rmin+'–'+sp.rmax+' · Ligne de vue : '+(sp.los?'oui':'non')+'<br>'
      +(n?('<b class="ok">'+n+' case'+(n>1?'s':'')+'</b> où viser (en vert)')
          :'<b class="no">aucune case</b> d\'ici : rapproche-toi ou change de sort');
  }
  document.getElementById('hint').textContent = state.enemies+' ennemi'+(state.enemies>1?'s':'')
    +' en jeu. Clique un sort pour voir où le lancer.';
}

function refreshValid(){
  if(selected===null){valid={cells:[],center:state?state.my_cell:null};validSet=new Set();draw();renderInfo();return;}
  fetch('/assist/valid?spell='+selected).then(function(r){return r.json();}).then(function(d){
    valid=d;validSet=new Set(d.cells||[]);draw();renderInfo();
  }).catch(function(){});
}

function sigOf(d){
  if(!d||!d.active) return 'off';
  return d.my_cell+'|'+d.my_pa+'|'+d.my_pm+'|'+(d.fighters||[]).map(function(f){
    return f.cell+':'+f.hp;}).join(',');
}
var lastSig="";
function poll(){
  fetch('/assist/state').then(function(r){return r.json();}).then(function(d){
    var prevCell=state?state.my_cell:null, wasActive=state&&state.active;
    state=d;
    if(d.active&&d.cells&&d.cells.length!==sizedN) sizeCanvas(d.cells.length);
    renderSpells();
    var sig=sigOf(d);
    if(sig!==lastSig){                 // ne bouge que si quelque chose a changé
      lastSig=sig;
      if(selected!==null && (d.my_cell!==prevCell || d.active!==wasActive)) refreshValid();
      else { draw(); renderInfo(); }
    }
  }).catch(function(){});
}
poll(); setInterval(poll, 1000);
</script></body></html>"""
