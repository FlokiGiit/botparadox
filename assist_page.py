"""Page « Assistant de combat » servie par le tableau de bord (/assist).

Affiche en direct la grille isométrique du combat (combattants, boss) et, quand
on clique un de ses sorts, surligne les cases OÙ LE SORT PEUT ÊTRE LANCÉ
(portée + ligne de vue), calculées en local côté bot (aucune requête serveur).

Le joueur joue son combat à la main : la page ne fait qu'indiquer où viser.
"""

ASSIST_PAGE = r"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Assistant de combat</title>
<style>
  :root{--bg:#0d1117;--fg:#e6e8eb;--sub:#8b949e;--card:#161b22;--edge:#262a33;
        --ok:#3fb950;--enemy:#e05561;--me:#4a9eff;--boss:#e3b341}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
       font-family:Segoe UI,Roboto,system-ui,sans-serif}
  header{padding:10px 14px;border-bottom:1px solid var(--edge);
         display:flex;align-items:center;gap:12px;flex-wrap:wrap}
  h1{font-size:15px;margin:0;font-weight:700}
  .sub{color:var(--sub);font-size:12px}
  #spells{display:flex;gap:6px;flex-wrap:wrap;padding:10px 14px;
          border-bottom:1px solid var(--edge)}
  .sp{display:flex;align-items:center;gap:6px;padding:6px 9px;border-radius:8px;
      background:var(--card);border:1px solid var(--edge);cursor:pointer;
      font-size:12px;color:var(--fg)}
  .sp.on{border-color:var(--ok);background:#12291a}
  .sp img{width:22px;height:22px;border-radius:4px}
  .sp .pa{color:var(--boss);font-weight:700}
  #wrap{position:relative;overflow:auto;padding:12px}
  canvas{display:block;margin:0 auto;background:transparent}
  #hint{padding:6px 14px;color:var(--sub);font-size:12px}
  .legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--sub)}
  .dot{display:inline-block;width:10px;height:10px;border-radius:50%;
       vertical-align:middle;margin-right:4px}
</style></head><body>
<header>
  <h1>Assistant de combat</h1>
  <span class="legend">
    <span><span class="dot" style="background:var(--me)"></span>toi</span>
    <span><span class="dot" style="background:var(--enemy)"></span>ennemi</span>
    <span><span class="dot" style="background:var(--boss)"></span>boss</span>
    <span><span class="dot" style="background:var(--ok)"></span>cases valides</span>
  </span>
  <span id="status" class="sub" style="margin-left:auto"></span>
</header>
<div id="spells"></div>
<div id="hint">Clique un de tes sorts : les cases où tu peux le lancer (portée + ligne de vue) s'affichent en vert.</div>
<div id="wrap"><canvas id="c"></canvas></div>
<script>
var CW=30, CH=16;            // largeur/hauteur d'une tuile
var STRIDE=29, W=15;
var state=null, selected=null, validSet=new Set(), validCenter=null;

function rowcol(cell){
  var pair=Math.floor(cell/STRIDE), rem=cell%STRIDE;
  if(rem<W) return [pair*2, rem];
  return [pair*2+1, rem-W];
}
function screenXY(cell){
  var rc=rowcol(cell), hr=rc[0], col=rc[1];
  var x=col*CW + (hr%2?CW/2:0);
  var y=hr*(CH/2);
  return [x, y];
}
function diamond(ctx, cx, cy, fill, stroke){
  ctx.beginPath();
  ctx.moveTo(cx, cy-CH/2); ctx.lineTo(cx+CW/2, cy);
  ctx.lineTo(cx, cy+CH/2); ctx.lineTo(cx-CW/2, cy); ctx.closePath();
  if(fill){ctx.fillStyle=fill;ctx.fill();}
  if(stroke){ctx.strokeStyle=stroke;ctx.lineWidth=1;ctx.stroke();}
}
function draw(){
  var cv=document.getElementById('c'), ctx=cv.getContext('2d');
  if(!state||!state.active||!state.cells){ctx.clearRect(0,0,cv.width,cv.height);return;}
  // dimensions
  var maxX=0,maxY=0;
  for(var i=0;i<state.cells.length;i++){var s=screenXY(i);if(s[0]>maxX)maxX=s[0];if(s[1]>maxY)maxY=s[1];}
  cv.width=maxX+CW+2; cv.height=maxY+CH+2;
  ctx.clearRect(0,0,cv.width,cv.height);
  var off=CW/2+1;
  // fond : toutes les cases
  for(var i=0;i<state.cells.length;i++){
    var cell=state.cells[i]; if(!cell) continue;
    var s=screenXY(i), cx=s[0]+off, cy=s[1]+CH/2+1;
    var fill = cell.w ? '#1c2230' : '#12151b';
    diamond(ctx, cx, cy, fill, '#20252f');
  }
  // cases valides
  validSet.forEach(function(i){
    var s=screenXY(i), cx=s[0]+off, cy=s[1]+CH/2+1;
    diamond(ctx, cx, cy, 'rgba(63,185,80,.55)', '#3fb950');
    ctx.fillStyle='#0d1117'; ctx.font='9px monospace'; ctx.textAlign='center';
    ctx.fillText(i, cx, cy+3);
  });
  // combattants
  (state.fighters||[]).forEach(function(f){
    var s=screenXY(f.cell), cx=s[0]+off, cy=s[1]+CH/2+1;
    var col = f.me?'#4a9eff':(f.boss?'#e3b341':(f.enemy?'#e05561':'#7ee787'));
    ctx.beginPath(); ctx.arc(cx, cy, f.boss?7:5, 0, 7); ctx.fillStyle=col; ctx.fill();
    if(f.boss){ctx.strokeStyle='#fff';ctx.lineWidth=1.5;ctx.stroke();}
  });
}
function renderSpells(){
  var box=document.getElementById('spells');
  if(!state||!state.spells||!state.spells.length){box.innerHTML='<span class="sub">Aucun sort (hors combat).</span>';return;}
  box.innerHTML='';
  state.spells.forEach(function(sp){
    var b=document.createElement('div'); b.className='sp'+(selected===sp.id?' on':'');
    b.innerHTML='<img src="/icon/sort/'+sp.id+'" onerror="this.style.display=\'none\'">'
      +'<span>'+sp.name+'</span><span class="pa">'+sp.pa+' PA</span>';
    b.onclick=function(){ selected=(selected===sp.id?null:sp.id); refreshValid(); renderSpells(); };
    box.appendChild(b);
  });
}
function refreshValid(){
  if(selected===null){validSet=new Set();draw();return;}
  fetch('/assist/valid?spell='+selected).then(function(r){return r.json();}).then(function(d){
    validSet=new Set(d.cells||[]); validCenter=d.center;
    document.getElementById('hint').textContent =
      (d.cells&&d.cells.length? d.cells.length+' cases possibles (portée '+d.rmin+'-'+d.rmax+(d.los?', LdV':'')+')'
       : 'Aucune case valide pour ce sort d\'ici.');
    draw();
  }).catch(function(){});
}
function poll(){
  fetch('/assist/state').then(function(r){return r.json();}).then(function(d){
    var wasActive=state&&state.active, prevCell=state?state.my_cell:null;
    state=d;
    document.getElementById('status').textContent = d.active? 'combat en cours' : 'en attente d\'un combat';
    renderSpells();
    // si ma position a changé (ou nouveau combat), recalculer les cases
    if(selected!==null && (d.my_cell!==prevCell || d.active!==wasActive)) refreshValid();
    else draw();
  }).catch(function(){});
}
poll(); setInterval(poll, 800);
</script></body></html>"""
