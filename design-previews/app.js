(() => {
  "use strict";

  const mapCanvas = document.getElementById("terrainMap");
  const mapStage = mapCanvas.parentElement;
  const ctx = mapCanvas.getContext("2d");
  const view = { x: 153, y: 273, cell: 21 };
  const home = { x: 153, y: 273, cell: 21 };
  let dragging = false;
  let dragOrigin = null;
  let selectedId = "C0";
  const hitTargets = [];

  const colors = {
    ink: "#182522",
    graphite: "#263431",
    graphite2: "#41504b",
    paper: "#e9e6dc",
    ivory: "#f5f0e4",
    teal: "#176f6b",
    tealDark: "#0e4d4b",
    tealSoft: "#75aaa1",
    amber: "#d78d22",
    amberLight: "#ffc657",
    rust: "#a34d34",
    blue: "#476f88",
    purple: "#75617f",
    sand: "#b9ad98",
    visible: "#c9d8bd",
  };

  const explored = [];
  const obstacles = [];
  for (let y = 247; y <= 299; y += 1) {
    for (let x = 122; x <= 185; x += 1) {
      const dx = (x - 153) / 1.12;
      const dy = y - 273;
      const noise = ((x * 17 + y * 11) % 13) - 5;
      const inside = dx * dx + dy * dy < (31 + noise) ** 2;
      if (inside && ((x * 13 + y * 7) % 19) > 1) explored.push([x, y]);
      if (inside && ((x * 19 + y * 23) % 71) === 0) obstacles.push([x, y]);
    }
  }

  const visible = explored.filter(([x, y]) => Math.abs(x - 153) + Math.abs(y - 273) <= 7);
  const resources = [
    { position: [148, 268], visible: true, assigned: true },
    { position: [161, 279], visible: true },
    { position: [143, 281], visible: true },
    { position: [170, 264], visible: false },
    { position: [136, 258], visible: false },
  ];
  const units = [
    { id: "C0", type: "core", label: "CORE", position: [153, 273] },
    { id: "W1", type: "worker", label: "工人", position: [148, 269] },
    { id: "W2", type: "worker", label: "工人", position: [159, 279] },
    { id: "W3", type: "worker", label: "工人", position: [144, 280] },
    { id: "V1", type: "vanguard", label: "先锋", position: [154, 273] },
    { id: "R1", type: "ranger", label: "游侠", position: [156, 274] },
  ];

  function polygon(target, points, fill, stroke = null, lineWidth = 1) {
    target.beginPath();
    points.forEach(([x, y], index) => index ? target.lineTo(x, y) : target.moveTo(x, y));
    target.closePath();
    target.fillStyle = fill;
    target.fill();
    if (stroke) {
      target.strokeStyle = stroke;
      target.lineWidth = lineWidth;
      target.stroke();
    }
  }

  function roundedRect(target, x, y, width, height, radius, fill, stroke = null, lineWidth = 1) {
    target.beginPath();
    target.roundRect(x, y, width, height, radius);
    target.fillStyle = fill;
    target.fill();
    if (stroke) {
      target.strokeStyle = stroke;
      target.lineWidth = lineWidth;
      target.stroke();
    }
  }

  function drawCoreModel(target, scale, selected = false) {
    target.save();
    target.scale(scale, scale);
    if (selected) {
      target.strokeStyle = colors.amberLight;
      target.lineWidth = 2;
      target.setLineDash([4, 3]);
      target.beginPath();
      target.ellipse(0, 24, 45, 16, 0, 0, Math.PI * 2);
      target.stroke();
      target.setLineDash([]);
    }
    polygon(target, [[-43,18],[-30,4],[22,1],[44,15],[30,31],[-25,32]], colors.graphite, colors.ink, 2);
    polygon(target, [[-34,7],[-22,-9],[19,-11],[34,4],[21,16],[-23,18]], colors.ivory, colors.ink, 2);
    polygon(target, [[-23,-9],[-9,-24],[11,-25],[22,-10],[11,2],[-11,3]], colors.teal, colors.ink, 1.5);
    polygon(target, [[-10,-24],[-2,-34],[9,-33],[13,-24],[4,-17],[-5,-18]], colors.graphite2, colors.ink, 1.5);
    target.fillStyle = colors.amberLight;
    target.beginPath(); target.arc(2,-24,7,0,Math.PI*2); target.fill();
    target.strokeStyle = colors.rust; target.lineWidth = 2; target.stroke();
    roundedRect(target,-28,18,13,8,1,colors.tealDark,colors.ink,1);
    roundedRect(target,16,16,14,9,1,colors.tealDark,colors.ink,1);
    target.strokeStyle = colors.ink; target.lineWidth = 2;
    target.beginPath(); target.moveTo(17,-12); target.lineTo(28,-34); target.lineTo(29,-45); target.stroke();
    target.fillStyle = colors.amber; target.fillRect(25,-47,8,5);
    target.restore();
  }

  function drawWorkerModel(target, scale, selected = false) {
    target.save();
    target.scale(scale, scale);
    if (selected) {
      target.strokeStyle = colors.amberLight; target.lineWidth = 2; target.setLineDash([4,3]);
      target.beginPath(); target.ellipse(0,19,42,13,0,0,Math.PI*2); target.stroke(); target.setLineDash([]);
    }
    [-25,-8,17].forEach((x) => {
      target.fillStyle = colors.ink;
      target.beginPath(); target.arc(x,18,8,0,Math.PI*2); target.fill();
      target.fillStyle = colors.graphite2;
      target.beginPath(); target.arc(x,18,3,0,Math.PI*2); target.fill();
    });
    polygon(target,[[-34,7],[-22,-10],[20,-11],[34,2],[27,15],[-27,15]],colors.ivory,colors.ink,2);
    polygon(target,[[-22,-10],[-12,-22],[10,-23],[21,-11],[13,1],[-15,2]],colors.teal,colors.ink,1.5);
    polygon(target,[[13,-21],[31,-18],[27,-2],[12,-4]],colors.graphite2,colors.ink,1.5);
    polygon(target,[[18,-18],[24,-28],[30,-16]],colors.amberLight,colors.rust,1);
    polygon(target,[[-33,2],[-46,8],[-42,14],[-29,10]],colors.amber,colors.ink,1.5);
    target.strokeStyle=colors.ink; target.lineWidth=3;
    target.beginPath(); target.moveTo(-44,10); target.lineTo(-51,20); target.moveTo(-44,10); target.lineTo(-54,7); target.stroke();
    target.restore();
  }

  function drawVanguardModel(target, scale, selected = false) {
    target.save();
    target.scale(scale, scale);
    if (selected) {
      target.strokeStyle=colors.amberLight; target.lineWidth=2; target.setLineDash([4,3]);
      target.beginPath(); target.ellipse(0,29,38,12,0,0,Math.PI*2); target.stroke(); target.setLineDash([]);
    }
    polygon(target,[[-22,24],[-14,3],[-4,5],[-7,31],[-24,31]],colors.graphite,colors.ink,2);
    polygon(target,[[8,4],[19,4],[26,29],[8,30]],colors.graphite,colors.ink,2);
    polygon(target,[[-24,-17],[-14,-31],[14,-30],[27,-15],[20,7],[-18,8]],colors.ivory,colors.ink,2);
    polygon(target,[[-12,-30],[-4,-42],[11,-39],[16,-29],[8,-20],[-8,-21]],colors.blue,colors.ink,2);
    target.fillStyle=colors.amberLight; target.fillRect(-1,-35,8,4);
    polygon(target,[[-27,-15],[-43,-26],[-50,-12],[-46,20],[-27,10]],colors.tealDark,colors.ink,2);
    polygon(target,[[26,-10],[43,-4],[38,3],[23,1]],colors.rust,colors.ink,2);
    target.strokeStyle=colors.amberLight; target.lineWidth=4;
    target.beginPath(); target.moveTo(39,0); target.lineTo(49,23); target.stroke();
    target.restore();
  }

  function drawRangerModel(target, scale, selected = false) {
    target.save();
    target.scale(scale, scale);
    if (selected) {
      target.strokeStyle=colors.amberLight; target.lineWidth=2; target.setLineDash([4,3]);
      target.beginPath(); target.ellipse(0,30,34,10,0,0,Math.PI*2); target.stroke(); target.setLineDash([]);
    }
    polygon(target,[[-14,7],[-4,6],[-8,32],[-23,32]],colors.graphite,colors.ink,2);
    polygon(target,[[6,5],[14,4],[23,31],[7,31]],colors.graphite,colors.ink,2);
    polygon(target,[[-17,-25],[-8,-38],[10,-35],[20,-21],[14,8],[-12,8]],colors.ivory,colors.ink,2);
    polygon(target,[[-7,-38],[0,-48],[11,-41],[10,-34],[2,-28],[-6,-30]],colors.purple,colors.ink,2);
    target.fillStyle=colors.amberLight; target.beginPath(); target.arc(3,-39,4,0,Math.PI*2); target.fill();
    target.strokeStyle=colors.ink; target.lineWidth=2;
    target.beginPath(); target.moveTo(-2,-47); target.lineTo(-8,-58); target.moveTo(9,-42); target.lineTo(14,-55); target.stroke();
    polygon(target,[[-12,-17],[-34,-8],[-31,-1],[-9,-6]],colors.tealDark,colors.ink,2);
    polygon(target,[[-39,-10],[33,1],[46,8],[29,9],[-38,-3]],colors.graphite2,colors.ink,2);
    target.fillStyle=colors.amber; target.fillRect(35,3,9,3);
    target.restore();
  }

  const modelRenderers = { core: drawCoreModel, worker: drawWorkerModel, vanguard: drawVanguardModel, ranger: drawRangerModel };

  function renderModelCanvas(canvas) {
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.floor(rect.width * dpr);
    canvas.height = Math.floor(rect.height * dpr);
    const modelCtx = canvas.getContext("2d");
    modelCtx.setTransform(dpr,0,0,dpr,0,0);
    modelCtx.clearRect(0,0,rect.width,rect.height);
    modelCtx.translate(rect.width/2, rect.height/2 + (canvas.classList.contains("model-canvas") ? 12 : 5));
    const scale = canvas.classList.contains("model-canvas") ? Math.min(rect.width/125,rect.height/115) : Math.min(rect.width/105,rect.height/105);
    modelRenderers[canvas.dataset.model](modelCtx, scale, false);
  }

  function resizeModels() {
    document.querySelectorAll("canvas[data-model]").forEach(renderModelCanvas);
  }

  function screen(position) {
    const rect = mapCanvas.getBoundingClientRect();
    return [rect.width/2 + (position[0]-view.x)*view.cell, rect.height/2 + (position[1]-view.y)*view.cell];
  }

  function drawCell(position, fill, alpha = 1) {
    const [x,y] = screen(position);
    const size = view.cell - 1;
    ctx.save(); ctx.globalAlpha = alpha; ctx.fillStyle = fill;
    ctx.fillRect(x-size/2,y-size/2,size,size); ctx.restore();
  }

  function drawTerrainTexture(position) {
    const [x,y] = screen(position);
    const seed = position[0]*37 + position[1]*19;
    ctx.fillStyle = seed%5===0 ? "rgba(74,75,67,.15)" : "rgba(255,253,247,.13)";
    ctx.fillRect(x-view.cell*.28,y-view.cell*.1,Math.max(1,view.cell*.22),1);
  }

  function drawGrid(width,height) {
    if (view.cell < 9) return;
    ctx.save(); ctx.strokeStyle="rgba(24,37,34,.19)"; ctx.lineWidth=1; ctx.beginPath();
    const left=Math.floor(view.x-width/2/view.cell)-1, right=Math.ceil(view.x+width/2/view.cell)+1;
    const top=Math.floor(view.y-height/2/view.cell)-1, bottom=Math.ceil(view.y+height/2/view.cell)+1;
    for(let x=left;x<=right;x+=1){const sx=screen([x-.5,0])[0];ctx.moveTo(sx,0);ctx.lineTo(sx,height);}
    for(let y=top;y<=bottom;y+=1){const sy=screen([0,y-.5])[1];ctx.moveTo(0,sy);ctx.lineTo(width,sy);}
    ctx.stroke(); ctx.restore();
  }

  function drawObstacle(position) {
    const [x,y]=screen(position); const r=Math.max(4,view.cell*.34);
    polygon(ctx,[[x-r,y+r*.7],[x-r*.55,y-r*.6],[x,y-r],[x+r*.8,y-r*.25],[x+r,y+r*.6]],colors.graphite2,colors.ink,1);
    ctx.strokeStyle="rgba(255,255,255,.24)";ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(x-r*.35,y-r*.5);ctx.lineTo(x+r*.35,y+r*.35);ctx.stroke();
  }

  function drawResource(resource) {
    const [x,y]=screen(resource.position); const r=Math.max(5,view.cell*.32);
    ctx.save();
    if(!resource.visible){ctx.globalAlpha=.62;ctx.setLineDash([3,2]);ctx.strokeStyle=colors.amber;ctx.lineWidth=2;ctx.strokeRect(x-r,y-r,r*2,r*2);ctx.setLineDash([]);ctx.restore();return;}
    polygon(ctx,[[x,y-r*1.25],[x+r*.7,y-r*.25],[x+r*.25,y+r],[x-r*.45,y+r*.65],[x-r*.75,y-r*.3]],colors.amberLight,colors.rust,1.3);
    polygon(ctx,[[x,y-r*1.25],[x+r*.18,y-r*.15],[x-r*.45,y+r*.65],[x-r*.75,y-r*.3]],"#ffe39a");
    if(resource.assigned){ctx.strokeStyle=colors.teal;ctx.lineWidth=1.5;ctx.beginPath();ctx.arc(x,y,r*1.65,0,Math.PI*2);ctx.stroke();}
    ctx.restore();
  }

  function drawRoute(from,to) {
    const a=screen(from),b=screen(to);
    ctx.save();ctx.strokeStyle=colors.teal;ctx.lineWidth=2;ctx.setLineDash([6,5]);ctx.beginPath();ctx.moveTo(a[0],a[1]);ctx.lineTo(b[0],b[1]);ctx.stroke();ctx.setLineDash([]);
    ctx.fillStyle=colors.teal;ctx.beginPath();ctx.arc(b[0],b[1],3,0,Math.PI*2);ctx.fill();ctx.restore();
  }

  function drawMapUnit(unit) {
    const [x,y]=screen(unit.position);
    const scale=Math.max(.34,Math.min(.58,view.cell/38));
    const selected=unit.id===selectedId;
    ctx.save();ctx.translate(x,y-4);modelRenderers[unit.type](ctx,scale,selected);ctx.restore();
    const radius=unit.type==="core"?30:23;
    hitTargets.push({unit,x,y,radius});
    const labelY=y+(unit.type==="core"?25:22);
    ctx.font="800 8px Inter, sans-serif";
    const label=`${unit.id} · ${unit.label}`;
    const width=ctx.measureText(label).width+10;
    ctx.fillStyle=selected?colors.amber:colors.ink;ctx.fillRect(x-width/2,labelY,width,15);
    ctx.fillStyle=selected?colors.ink:colors.ivory;ctx.textAlign="center";ctx.textBaseline="middle";ctx.fillText(label,x,labelY+7.5);
  }

  function drawMap() {
    const rect=mapCanvas.getBoundingClientRect(); if(!rect.width||!rect.height)return;
    ctx.clearRect(0,0,rect.width,rect.height);
    ctx.fillStyle="#8e8a7f";ctx.fillRect(0,0,rect.width,rect.height);
    const vignette=ctx.createRadialGradient(rect.width*.48,rect.height*.48,rect.width*.08,rect.width*.48,rect.height*.48,rect.width*.7);
    vignette.addColorStop(0,"rgba(255,255,255,.05)");vignette.addColorStop(1,"rgba(24,37,34,.36)");ctx.fillStyle=vignette;ctx.fillRect(0,0,rect.width,rect.height);
    explored.forEach(cell=>drawCell(cell,colors.sand));
    explored.forEach(drawTerrainTexture);
    visible.forEach(cell=>drawCell(cell,colors.visible,.82));
    drawGrid(rect.width,rect.height);
    obstacles.forEach(drawObstacle);
    drawRoute([148,269],[148,268]);
    drawRoute([159,279],[153,273]);
    resources.forEach(drawResource);
    hitTargets.length=0;
    units.forEach(drawMapUnit);
  }

  function resizeMap() {
    const rect=mapCanvas.getBoundingClientRect(); const dpr=window.devicePixelRatio||1;
    mapCanvas.width=Math.max(1,Math.floor(rect.width*dpr));mapCanvas.height=Math.max(1,Math.floor(rect.height*dpr));
    ctx.setTransform(dpr,0,0,dpr,0,0);drawMap();
  }

  function selectUnit(unit) {
    selectedId=unit.id;
    document.querySelectorAll(".unit-card").forEach((button)=>{
      const active=button.dataset.id===unit.id;
      button.classList.toggle("active",active);
      button.setAttribute("aria-pressed",String(active));
    });
    const tag=document.getElementById("selectionTag");
    tag.querySelector("strong").textContent=`${unit.label.toUpperCase()} · ${unit.id}`;
    tag.querySelector("b").textContent=unit.position.join(", ");
    drawMap();
  }

  function centerOn(position) { view.x=position[0];view.y=position[1];drawMap(); }

  document.querySelectorAll(".unit-card").forEach((button)=>{
    button.addEventListener("click",()=>{
      const unit=units.find(candidate=>candidate.id===button.dataset.id);
      if(unit){selectUnit(unit);centerOn(unit.position);}
    });
  });

  document.querySelectorAll("[data-resource]").forEach((button)=>{
    button.addEventListener("click",()=>{
      const position=button.dataset.resource.split(",").map(Number);
      centerOn(position);
      document.getElementById("cursorCoordinate").textContent=button.dataset.resource.replace(",",", ");
    });
  });

  document.getElementById("zoomOut").addEventListener("click",()=>{view.cell=Math.max(9,view.cell/1.2);drawMap();});
  document.getElementById("zoomIn").addEventListener("click",()=>{view.cell=Math.min(42,view.cell*1.2);drawMap();});
  document.getElementById("resetView").addEventListener("click",()=>{Object.assign(view,home);drawMap();});

  mapCanvas.addEventListener("pointerdown",(event)=>{
    const rect=mapCanvas.getBoundingClientRect();const x=event.clientX-rect.left,y=event.clientY-rect.top;
    const hit=[...hitTargets].reverse().find(target=>Math.hypot(target.x-x,target.y-y)<=target.radius);
    if(hit){selectUnit(hit.unit);return;}
    dragging=true;dragOrigin={pointerX:event.clientX,pointerY:event.clientY,x:view.x,y:view.y};mapCanvas.setPointerCapture(event.pointerId);
  });
  mapCanvas.addEventListener("pointermove",(event)=>{
    const rect=mapCanvas.getBoundingClientRect();
    const worldX=Math.round(view.x+(event.clientX-rect.left-rect.width/2)/view.cell);
    const worldY=Math.round(view.y+(event.clientY-rect.top-rect.height/2)/view.cell);
    document.getElementById("cursorCoordinate").textContent=`${worldX}, ${worldY}`;
    if(!dragging||!dragOrigin)return;
    view.x=dragOrigin.x-(event.clientX-dragOrigin.pointerX)/view.cell;view.y=dragOrigin.y-(event.clientY-dragOrigin.pointerY)/view.cell;drawMap();
  });
  mapCanvas.addEventListener("pointerup",()=>{dragging=false;dragOrigin=null;});
  mapCanvas.addEventListener("pointercancel",()=>{dragging=false;dragOrigin=null;});
  mapCanvas.addEventListener("wheel",(event)=>{event.preventDefault();view.cell=Math.max(9,Math.min(42,view.cell*(event.deltaY<0?1.12:.89)));drawMap();},{passive:false});

  let tick=4281;
  window.setInterval(()=>{
    tick+=1;document.getElementById("tickValue").textContent=tick.toLocaleString("en-US");
    document.getElementById("clock").textContent=new Date().toLocaleTimeString("zh-CN",{hour12:false});
  },1000);

  const resizeObserver=new ResizeObserver(()=>{resizeMap();resizeModels();});
  resizeObserver.observe(mapStage);
  document.querySelectorAll("canvas[data-model]").forEach(canvas=>resizeObserver.observe(canvas));
  window.addEventListener("resize",()=>{resizeMap();resizeModels();});
  resizeMap();resizeModels();
})();
