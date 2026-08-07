(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const canvas = $("mapCanvas");
  const wrap = $("canvasWrap");
  const tooltip = $("mapTooltip");
  const ctx = canvas.getContext("2d");
  const view = { centerX: 0, centerY: 0, cell: 18, fitted: false };
  let snapshot = null;
  let selectedUnitId = null;
  let dragging = false;
  let dragStart = null;
  let runtimeStatus = "connecting";
  let runtimeRefreshInFlight = false;
  const hitTargets = [];

  const colors = {
    ink: "#182522",
    graphite: "#263431",
    graphite2: "#41504b",
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
    enemy: "#b53832",
    enemyDark: "#5d211e",
    enemyLight: "#ff7c68",
    green: "#318754",
    red: "#b53832",
    beacon: "#3d91a1",
  };

  function setText(id, value) {
    $(id).textContent = value;
  }

  function formatDuration(totalSeconds) {
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;
    return hours
      ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
      : `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }

  function positionText(position) {
    return position ? `${position[0]}, ${position[1]}` : "--";
  }

  function positionKey(position) {
    return `${position[0]},${position[1]}`;
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>'"]/g, (character) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
    })[character]);
  }

  function actionText(action) {
    if (!action) return "等待";
    const labels = {
      MOVE: "移动", HARVEST: "采集", DEPOSIT: "入库", HEAL: "治疗",
      PICKUP_BEACON: "拾取信标", SWEEP: "横扫", SHOOT: "射击", WAIT: "等待",
    };
    const base = labels[action.type] || action.type;
    const detail = action.direction || action.unit_type || action.detail;
    return detail ? `${base} ${detail}` : base;
  }

  function statusText(status) {
    return {
      connecting: "等待策略连接", connected: "实时连接", error: "策略异常",
      stopped: "策略已停止", reconnecting: "正在重连",
    }[status] || status;
  }

  function applyRuntime(runtime, serverTime) {
    runtimeStatus = runtime.status || "connecting";
    $("connection").dataset.status = runtimeStatus;
    setText("connectionText", statusText(runtimeStatus));
    setText("lastUpdate", new Date(serverTime || Date.now()).toLocaleTimeString("zh-CN", { hour12: false }));
    setText("uptime", formatDuration(Number(runtime.uptimeSeconds || 0)));
    setText("accepted", runtime.acceptedSubmissions || 0);
    setText("failed", runtime.failedSubmissions || 0);
  }

  function unitTypeName(type) {
    return { WORKER: "工人", VANGUARD: "先锋", RANGER: "游侠", CORE: "Core" }[type] || type;
  }

  function unitModelName(type) {
    return { WORKER: "worker", VANGUARD: "vanguard", RANGER: "ranger", CORE: "core" }[type] || "raider";
  }

  function updateDashboard(data) {
    snapshot = data;
    const runtime = data.runtime || {};
    const game = data.game || {};
    const terrain = game.terrain || {};
    const counts = game.counts || {};
    const core = game.core;

    applyRuntime(runtime, Date.now());
    $("lastUpdate").title = `最近数据 ${new Date(data.generatedAt || Date.now()).toLocaleTimeString("zh-CN", { hour12: false })}`;
    setText("tick", game.tick ?? "--");
    setText("resources", `${game.resources || 0} / ${game.resourceCapacity || 0}`);
    setText("population", game.population || 0);
    setText("explored", (terrain.explored || []).length.toLocaleString("zh-CN"));
    setText("resourceNodes", (terrain.resources || []).length);
    setText("mapSummary", `已探索 ${(terrain.explored || []).length.toLocaleString("zh-CN")} 格 · 当前视野 ${(terrain.visible || []).length} 格 · 障碍 ${(terrain.obstacles || []).length}`);
    setText("playerStatus", game.playerStatus || "--");
    setText("corePosition", core ? positionText(core.position) : "重生中");
    setText("coreHp", core ? `${core.hp} / 5` : "--");
    setText("coreShield", core ? `${core.shield} / 5` : "--");
    setText("coreState", core ? core.state : "RESPAWNING");
    setText("workerCount", counts.workers || 0);
    setText("vanguardCount", counts.vanguards || 0);
    setText("rangerCount", counts.rangers || 0);
    setText("enemyCount", counts.enemies || 0);
    $("coreHpBar").style.setProperty("--value", `${core ? Math.max(0, Math.min(100, core.hp / 5 * 100)) : 0}%`);
    $("coreShieldBar").style.setProperty("--value", `${core ? Math.max(0, Math.min(100, core.shield / 5 * 100)) : 0}%`);
    $("resourceBar").style.setProperty("--value", `${game.resourceCapacity ? Math.max(0, Math.min(100, game.resources / game.resourceCapacity * 100)) : 0}%`);
    if (core) {
      setText("sectorCoordinate", `SECTOR ${core.position[0]} : ${core.position[1]}`);
      if (!selectedUnitId) updateSelectionLabel("CORE · C0", core.position);
    }

    renderUnits(game.units || []);
    renderResources(terrain.resources || [], terrain.visibleResources || [], game.workers || []);
    renderEvents(game.events || []);
    renderOperatorStats(data.stats || {});
    renderModelCanvases();
    const error = $("runtimeError");
    error.hidden = !runtime.lastError;
    error.textContent = runtime.lastError || "";

    if (!view.fitted && game.tick) fitMap();
    drawMap();
  }

  function renderUnits(units) {
    const roster = $("workerRows");
    const workers = units.filter((unit) => unit.type === "WORKER");
    setText("cargoTotal", `载荷 ${workers.reduce((sum, worker) => sum + (worker.cargo || 0), 0)}`);
    if (!units.length) {
      roster.innerHTML = '<p class="empty-state">暂无单位</p>';
      return;
    }
    const typeCounts = {};
    roster.innerHTML = units.map((unit) => {
      typeCounts[unit.type] = (typeCounts[unit.type] || 0) + 1;
      const prefix = { WORKER: "W", VANGUARD: "V", RANGER: "R" }[unit.type] || "U";
      const name = `${prefix}${typeCounts[unit.type]} · ${unitTypeName(unit.type)}`;
      const target = unit.resourceTarget || unit.scoutGoal;
      const detail = unit.type === "WORKER"
        ? `载荷 ${unit.cargo || 0}${target ? ` · 目标 ${positionText(target)}` : ""}`
        : `HP ${unit.hp}${target ? ` · 目标 ${positionText(target)}` : ""}`;
      const selected = unit.id === selectedUnitId ? " selected" : "";
      return `<button class="unit-card${selected}" type="button" data-unit-id="${escapeHtml(unit.id)}" data-unit-name="${escapeHtml(name)}" aria-pressed="${unit.id === selectedUnitId}">
        <canvas data-model="${unitModelName(unit.type)}" aria-hidden="true"></canvas>
        <span><b>${escapeHtml(name)}</b><small>${escapeHtml(detail)}</small></span>
        <i class="action-label">${escapeHtml(actionText(unit.action))}</i>
      </button>`;
    }).join("");
    roster.querySelectorAll("[data-unit-id]").forEach((button) => {
      button.addEventListener("click", () => selectUnit(button.dataset.unitId, button.dataset.unitName));
    });
  }

  function renderResources(resources, visibleResources, workers) {
    const list = $("resourceList");
    const visibleKeys = new Set(visibleResources.map(positionKey));
    const assignments = new Map();
    workers.forEach((worker, index) => {
      if (worker.resourceTarget) assignments.set(positionKey(worker.resourceTarget), `已分配 W${index + 1}`);
    });
    const ordered = [...resources].sort((first, second) => {
      const visibleDifference = Number(visibleKeys.has(positionKey(second))) - Number(visibleKeys.has(positionKey(first)));
      return visibleDifference || first[0] - second[0] || first[1] - second[1];
    });
    setText("visibleResourceCount", `可见 ${visibleResources.length} · 记忆 ${Math.max(0, resources.length - visibleResources.length)}`);
    if (!ordered.length) {
      list.innerHTML = '<p class="empty-state">暂无已知资源</p>';
      return;
    }
    list.innerHTML = ordered.slice(0, 12).map((position) => {
      const key = positionKey(position);
      const visible = visibleKeys.has(key);
      const detail = assignments.get(key) || (visible ? "待分配工人" : "雾区记忆");
      return `<button type="button" class="resource-item${visible ? " visible" : ""}" data-x="${position[0]}" data-y="${position[1]}">
        <strong>${positionText(position)}</strong><span>${escapeHtml(detail)}</span>
      </button>`;
    }).join("");
    list.querySelectorAll(".resource-item").forEach((button) => {
      button.addEventListener("click", () => {
        const position = [Number(button.dataset.x), Number(button.dataset.y)];
        centerMap(position[0], position[1], 20);
        updateSelectionLabel("资源晶体", position);
      });
    });
  }

  function renderEvents(events) {
    const list = $("eventList");
    if (!events.length) {
      list.innerHTML = '<li class="empty-event">暂无事件</li>';
      return;
    }
    list.innerHTML = [...events].reverse().slice(0, 2).map((event) => {
      const detail = event.reason ? ` · ${event.reason}` : event.position ? ` · ${positionText(event.position)}` : "";
      return `<li><b>${escapeHtml(event.type)}</b>${escapeHtml(detail)}</li>`;
    }).join("");
  }

  function renderOperatorStats(stats) {
    const values = stats.values || {};
    document.querySelectorAll("[data-stat]").forEach((element) => {
      element.textContent = Number(values[element.dataset.stat] || 0).toLocaleString("zh-CN");
    });
    const source = stats.source === "arena-hero" ? "完整生涯" : "本地累计";
    const trackedSince = stats.trackedSince ? new Date(stats.trackedSince).toLocaleString("zh-CN", { hour12: false }) : "--";
    setText("statsSource", source);
    $("statsSource").title = `${source} · ${stats.source === "arena-hero" ? "更新于" : "自"} ${trackedSince}`;
  }

  function gameData() {
    return snapshot && snapshot.game ? snapshot.game : null;
  }

  function mapPoints(game) {
    if (!game) return [];
    const terrain = game.terrain || {};
    const points = [...(terrain.explored || [])];
    if (game.core) points.push(game.core.position);
    (game.units || []).forEach((unit) => points.push(unit.position));
    (game.enemies || []).forEach((enemy) => points.push(enemy.position));
    return points;
  }

  function fitMap() {
    const game = gameData();
    const points = mapPoints(game);
    const width = wrap.clientWidth || 800;
    const height = wrap.clientHeight || 500;
    if (!points.length) {
      view.centerX = 0;
      view.centerY = 0;
      view.cell = 18;
    } else {
      const xs = points.map((point) => point[0]);
      const ys = points.map((point) => point[1]);
      const minX = Math.min(...xs), maxX = Math.max(...xs);
      const minY = Math.min(...ys), maxY = Math.max(...ys);
      view.centerX = (minX + maxX) / 2;
      view.centerY = (minY + maxY) / 2;
      view.cell = Math.max(3, Math.min(30, Math.min(width / (maxX - minX + 5), height / (maxY - minY + 5))));
    }
    view.fitted = true;
    drawMap();
  }

  function resizeCanvas() {
    const rect = wrap.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.floor(rect.width * dpr));
    canvas.height = Math.max(1, Math.floor(rect.height * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    drawMap();
    renderModelCanvases();
  }

  function toScreen(position) {
    return [
      wrap.clientWidth / 2 + (position[0] - view.centerX) * view.cell,
      wrap.clientHeight / 2 + (position[1] - view.centerY) * view.cell,
    ];
  }

  function toWorld(clientX, clientY) {
    const rect = canvas.getBoundingClientRect();
    return [
      Math.round(view.centerX + (clientX - rect.left - rect.width / 2) / view.cell),
      Math.round(view.centerY + (clientY - rect.top - rect.height / 2) / view.cell),
    ];
  }

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

  function modelPalette(hostile = false) {
    return hostile ? {
      ink: "#351613", graphite: "#251e1c", graphite2: "#49312c", ivory: "#c8b7a1",
      primary: colors.enemy, primaryDark: colors.enemyDark, accent: colors.enemyLight, secondary: "#7e3e2d",
    } : {
      ink: colors.ink, graphite: colors.graphite, graphite2: colors.graphite2, ivory: colors.ivory,
      primary: colors.teal, primaryDark: colors.tealDark, accent: colors.amberLight, secondary: colors.blue,
    };
  }

  function drawSelectionRing(target, selected, width, y) {
    if (!selected) return;
    target.strokeStyle = colors.amberLight;
    target.lineWidth = 2;
    target.setLineDash([4, 3]);
    target.beginPath();
    target.ellipse(0, y, width, Math.max(9, width * .34), 0, 0, Math.PI * 2);
    target.stroke();
    target.setLineDash([]);
  }

  function drawCoreModel(target, scale, selected = false, hostile = false) {
    const p = modelPalette(hostile);
    target.save(); target.scale(scale, scale); drawSelectionRing(target, selected, 45, 24);
    polygon(target, [[-43,18],[-30,4],[22,1],[44,15],[30,31],[-25,32]], p.graphite, p.ink, 2);
    polygon(target, [[-34,7],[-22,-9],[19,-11],[34,4],[21,16],[-23,18]], p.ivory, p.ink, 2);
    polygon(target, [[-23,-9],[-9,-24],[11,-25],[22,-10],[11,2],[-11,3]], p.primary, p.ink, 1.5);
    polygon(target, [[-10,-24],[-2,-34],[9,-33],[13,-24],[4,-17],[-5,-18]], p.graphite2, p.ink, 1.5);
    target.fillStyle = p.accent; target.beginPath(); target.arc(2,-24,7,0,Math.PI*2); target.fill();
    target.strokeStyle = p.ink; target.lineWidth = 2; target.stroke();
    target.fillStyle = p.primaryDark; target.fillRect(-28,18,13,8); target.fillRect(16,16,14,9);
    target.strokeStyle = p.ink; target.beginPath(); target.moveTo(17,-12); target.lineTo(28,-34); target.lineTo(29,-45); target.stroke();
    target.fillStyle = p.accent; target.fillRect(25,-47,8,5); target.restore();
  }

  function drawWorkerModel(target, scale, selected = false, hostile = false) {
    const p = modelPalette(hostile);
    target.save(); target.scale(scale, scale); drawSelectionRing(target, selected, 42, 19);
    [-25,-8,17].forEach((x) => {
      target.fillStyle = p.ink; target.beginPath(); target.arc(x,18,8,0,Math.PI*2); target.fill();
      target.fillStyle = p.graphite2; target.beginPath(); target.arc(x,18,3,0,Math.PI*2); target.fill();
    });
    polygon(target,[[-34,7],[-22,-10],[20,-11],[34,2],[27,15],[-27,15]],p.ivory,p.ink,2);
    polygon(target,[[-22,-10],[-12,-22],[10,-23],[21,-11],[13,1],[-15,2]],p.primary,p.ink,1.5);
    polygon(target,[[13,-21],[31,-18],[27,-2],[12,-4]],p.graphite2,p.ink,1.5);
    polygon(target,[[18,-18],[24,-28],[30,-16]],p.accent,p.ink,1);
    polygon(target,[[-33,2],[-46,8],[-42,14],[-29,10]],p.secondary,p.ink,1.5);
    target.strokeStyle=p.ink; target.lineWidth=3; target.beginPath(); target.moveTo(-44,10); target.lineTo(-51,20); target.moveTo(-44,10); target.lineTo(-54,7); target.stroke();
    target.restore();
  }

  function drawVanguardModel(target, scale, selected = false, hostile = false) {
    const p = modelPalette(hostile);
    target.save(); target.scale(scale, scale); drawSelectionRing(target, selected, 38, 29);
    polygon(target,[[-22,24],[-14,3],[-4,5],[-7,31],[-24,31]],p.graphite,p.ink,2);
    polygon(target,[[8,4],[19,4],[26,29],[8,30]],p.graphite,p.ink,2);
    polygon(target,[[-24,-17],[-14,-31],[14,-30],[27,-15],[20,7],[-18,8]],p.ivory,p.ink,2);
    polygon(target,[[-12,-30],[-4,-42],[11,-39],[16,-29],[8,-20],[-8,-21]],hostile?p.primary:p.secondary,p.ink,2);
    target.fillStyle=p.accent; target.fillRect(-1,-35,8,4);
    polygon(target,[[-27,-15],[-43,-26],[-50,-12],[-46,20],[-27,10]],p.primaryDark,p.ink,2);
    polygon(target,[[26,-10],[43,-4],[38,3],[23,1]],p.primary,p.ink,2);
    target.strokeStyle=p.accent; target.lineWidth=4; target.beginPath(); target.moveTo(39,0); target.lineTo(49,23); target.stroke(); target.restore();
  }

  function drawRangerModel(target, scale, selected = false, hostile = false) {
    const p = modelPalette(hostile);
    target.save(); target.scale(scale, scale); drawSelectionRing(target, selected, 34, 30);
    polygon(target,[[-14,7],[-4,6],[-8,32],[-23,32]],p.graphite,p.ink,2);
    polygon(target,[[6,5],[14,4],[23,31],[7,31]],p.graphite,p.ink,2);
    polygon(target,[[-17,-25],[-8,-38],[10,-35],[20,-21],[14,8],[-12,8]],p.ivory,p.ink,2);
    polygon(target,[[-7,-38],[0,-48],[11,-41],[10,-34],[2,-28],[-6,-30]],hostile?p.primary:"#75617f",p.ink,2);
    target.fillStyle=p.accent; target.beginPath(); target.arc(3,-39,4,0,Math.PI*2); target.fill();
    target.strokeStyle=p.ink; target.lineWidth=2; target.beginPath(); target.moveTo(-2,-47); target.lineTo(-8,-58); target.moveTo(9,-42); target.lineTo(14,-55); target.stroke();
    polygon(target,[[-12,-17],[-34,-8],[-31,-1],[-9,-6]],p.primaryDark,p.ink,2);
    polygon(target,[[-39,-10],[33,1],[46,8],[29,9],[-38,-3]],p.graphite2,p.ink,2); target.restore();
  }

  function drawRaiderModel(target, scale, selected = false) {
    target.save(); target.scale(scale, scale); drawSelectionRing(target, selected, 36, 23);
    polygon(target,[[-34,18],[-25,-14],[-8,-27],[13,-24],[35,-7],[29,22],[3,31]],colors.enemyDark,"#351613",2);
    polygon(target,[[-22,-11],[-5,-34],[15,-28],[25,-10],[8,3],[-10,1]],"#c8b7a1","#351613",2);
    target.fillStyle=colors.enemyLight; target.beginPath(); target.arc(2,-22,6,0,Math.PI*2); target.fill();
    target.strokeStyle="#351613"; target.lineWidth=4; target.beginPath(); target.moveTo(25,-3); target.lineTo(46,-22); target.stroke(); target.restore();
  }

  const modelRenderers = { core: drawCoreModel, worker: drawWorkerModel, vanguard: drawVanguardModel, ranger: drawRangerModel, raider: drawRaiderModel };

  function renderModelCanvas(modelCanvas) {
    const rect = modelCanvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const dpr = window.devicePixelRatio || 1;
    modelCanvas.width = Math.floor(rect.width * dpr);
    modelCanvas.height = Math.floor(rect.height * dpr);
    const modelCtx = modelCanvas.getContext("2d");
    modelCtx.setTransform(dpr,0,0,dpr,0,0);
    modelCtx.clearRect(0,0,rect.width,rect.height);
    modelCtx.translate(rect.width/2, rect.height/2 + (modelCanvas.classList.contains("model-canvas") ? 12 : 5));
    const scale = modelCanvas.classList.contains("model-canvas") ? Math.min(rect.width/125,rect.height/115) : Math.min(rect.width/105,rect.height/105);
    modelRenderers[modelCanvas.dataset.model](modelCtx, scale, false, false);
  }

  function renderModelCanvases() {
    document.querySelectorAll("canvas[data-model]").forEach(renderModelCanvas);
  }

  function drawCell(position, fill, inset = .5) {
    const [x,y] = toScreen(position);
    const size = Math.max(1,view.cell-inset*2);
    ctx.fillStyle=fill; ctx.fillRect(x-view.cell/2+inset,y-view.cell/2+inset,size,size);
  }

  function drawTerrainTexture(position) {
    if (view.cell < 8) return;
    const [x,y] = toScreen(position);
    const seed = position[0]*37+position[1]*19;
    ctx.fillStyle = seed%5===0 ? "rgba(74,75,67,.15)" : "rgba(255,253,247,.13)";
    ctx.fillRect(x-view.cell*.28,y-view.cell*.1,Math.max(1,view.cell*.22),1);
  }

  function drawMap() {
    const width=wrap.clientWidth, height=wrap.clientHeight;
    if(!width||!height)return;
    ctx.clearRect(0,0,width,height); ctx.fillStyle="#8e8a7f"; ctx.fillRect(0,0,width,height);
    const game=gameData();
    if(!game){ctx.fillStyle="#263431";ctx.font="700 13px system-ui";ctx.textAlign="center";ctx.fillText("等待地图数据",width/2,height/2);return;}
    const terrain=game.terrain||{};
    (terrain.explored||[]).forEach((cell)=>drawCell(cell,colors.sand));
    (terrain.explored||[]).forEach(drawTerrainTexture);
    (terrain.visible||[]).forEach((cell)=>drawCell(cell,colors.visible,.7));
    drawGrid(width,height);
    (terrain.obstacles||[]).forEach(drawObstacle);
    (game.workers||[]).forEach(drawTarget);
    const visibleKeys=new Set((terrain.visibleResources||[]).map(positionKey));
    (terrain.resources||[]).forEach((cell)=>drawResource(cell,visibleKeys.has(positionKey(cell))));
    if(game.beacon&&game.beacon.status&&game.beacon.status!=="None")drawBeacon(game.beacon);
    hitTargets.length=0;
    if(game.core)drawCore(game.core);
    (game.units||[]).forEach(drawUnit);
    (game.enemies||[]).forEach(drawEnemy);
  }

  function drawGrid(width,height) {
    if(view.cell<8)return;
    const left=Math.floor(view.centerX-width/2/view.cell)-1,right=Math.ceil(view.centerX+width/2/view.cell)+1;
    const bottom=Math.floor(view.centerY-height/2/view.cell)-1,top=Math.ceil(view.centerY+height/2/view.cell)+1;
    ctx.strokeStyle="rgba(24,37,34,.19)";ctx.lineWidth=1;ctx.beginPath();
    for(let x=left;x<=right;x+=1){const sx=toScreen([x-.5,0])[0];ctx.moveTo(sx,0);ctx.lineTo(sx,height);}
    for(let y=bottom;y<=top;y+=1){const sy=toScreen([0,y-.5])[1];ctx.moveTo(0,sy);ctx.lineTo(width,sy);}
    ctx.stroke();
  }

  function drawObstacle(position) {
    const [x,y]=toScreen(position),r=Math.max(1.25,view.cell*.32);
    polygon(ctx,[[x-r,y+r*.7],[x-r*.55,y-r*.6],[x,y-r],[x+r*.8,y-r*.25],[x+r,y+r*.6]],colors.graphite2,colors.ink,1);
  }

  function drawResource(position,visible) {
    const [x,y]=toScreen(position),r=Math.max(1.5,view.cell*.32);
    if(!visible){ctx.save();ctx.globalAlpha=.7;ctx.setLineDash([3,2]);ctx.strokeStyle=colors.amber;ctx.lineWidth=1.5;ctx.strokeRect(x-r,y-r,r*2,r*2);ctx.restore();return;}
    polygon(ctx,[[x,y-r*1.25],[x+r*.7,y-r*.25],[x+r*.25,y+r],[x-r*.45,y+r*.65],[x-r*.75,y-r*.3]],colors.amberLight,colors.rust,1.2);
    polygon(ctx,[[x,y-r*1.25],[x+r*.18,y-r*.15],[x-r*.45,y+r*.65],[x-r*.75,y-r*.3]],"#ffe39a");
  }

  function drawTarget(worker) {
    const target=worker.resourceTarget||worker.scoutGoal;
    if(!target)return;
    const [x1,y1]=toScreen(worker.position),[x2,y2]=toScreen(target);
    ctx.save();ctx.strokeStyle=worker.resourceTarget?colors.teal:colors.graphite2;ctx.lineWidth=worker.id===selectedUnitId?2:1;ctx.globalAlpha=worker.id===selectedUnitId?.95:.55;ctx.setLineDash([6,5]);ctx.beginPath();ctx.moveTo(x1,y1);ctx.lineTo(x2,y2);ctx.stroke();ctx.restore();
  }

  function drawCore(core) {
    const [x,y]=toScreen(core.position),scale=view.cell/112;
    ctx.save();ctx.translate(x,y+view.cell*.06);drawCoreModel(ctx,scale,selectedUnitId==="core",false);ctx.restore();
    hitTargets.push({id:"core",name:"CORE · C0",position:core.position,x,y,radius:Math.max(4,view.cell*.48)});
    drawHealthBar(x,y,view.cell*.8,core.hp,5,-view.cell*.48);
    if(view.cell>=18)drawMapLabel(x,y+view.cell*.39,"C0 · CORE",selectedUnitId==="core",false);
  }

  function drawUnit(unit) {
    const [x,y]=toScreen(unit.position),model=unitModelName(unit.type),scale=view.cell/122;
    ctx.save();ctx.translate(x,y+view.cell*.04);modelRenderers[model](ctx,scale,unit.id===selectedUnitId,false);ctx.restore();
    const name=unitTypeName(unit.type);
    hitTargets.push({id:unit.id,name,position:unit.position,x,y,radius:Math.max(4,view.cell*.46)});
    drawHealthBar(x,y,view.cell*.76,unit.hp,unit.type==="VANGUARD"?4:2,-view.cell*.46);
    if(view.cell>=18)drawMapLabel(x,y+view.cell*.37,name,unit.id===selectedUnitId,false);
  }

  function drawEnemy(enemy) {
    const [x,y]=toScreen(enemy.position),model=unitModelName(enemy.type),scale=view.cell/120;
    ctx.save();ctx.translate(x,y+view.cell*.04);modelRenderers[model](ctx,scale,false,true);ctx.restore();
    const marker=Math.max(1.5,view.cell*.1);
    polygon(ctx,[[x,y-view.cell*.44],[x+marker,y-view.cell*.31],[x-marker,y-view.cell*.31]],colors.enemy,"#fff0e5",Math.max(.5,view.cell*.015));
    drawHealthBar(x,y,view.cell*.78,enemy.hp,enemy.kind==="CORE"?5:enemy.type==="VANGUARD"?4:2,-view.cell*.47);
    if(view.cell>=18)drawMapLabel(x,y+view.cell*.38,`敌方 · ${unitTypeName(enemy.type)}`,false,true);
  }

  function drawMapLabel(x,y,label,selected,hostile) {
    const fontSize=Math.max(6,Math.min(11,view.cell*.17));
    const height=fontSize+4;
    ctx.font=`800 ${fontSize}px Inter,system-ui`;const width=ctx.measureText(label).width+fontSize*1.25;
    ctx.fillStyle=hostile?colors.enemy:selected?colors.amber:colors.ink;ctx.fillRect(x-width/2,y,width,height);
    ctx.fillStyle=hostile?"#fff4e8":selected?colors.ink:colors.ivory;ctx.textAlign="center";ctx.textBaseline="middle";ctx.fillText(label,x,y+height/2);
  }

  function drawHealthBar(x,y,width,hp,maxHp,offsetY) {
    if(hp>=maxHp||view.cell<10)return;
    const height=Math.max(1,view.cell*.12);
    ctx.fillStyle="#5b302c";ctx.fillRect(x-width/2,y+offsetY,width,height);
    ctx.fillStyle=hp/maxHp>.5?colors.green:colors.red;ctx.fillRect(x-width/2,y+offsetY,width*Math.max(0,hp/maxHp),height);
  }

  function drawBeacon(beacon) {
    const [x,y]=toScreen(beacon.position),r=Math.max(1.5,view.cell*.24);
    ctx.fillStyle=colors.beacon;ctx.beginPath();ctx.arc(x,y,r,0,Math.PI*2);ctx.fill();ctx.strokeStyle="#d8f4f5";ctx.lineWidth=1.5;ctx.stroke();
  }

  function updateSelectionLabel(name,position) {
    setText("selectedName",name);setText("selectedPosition",positionText(position));
  }

  function selectUnit(unitId,displayName) {
    selectedUnitId=selectedUnitId===unitId?null:unitId;
    const game=gameData();
    if(selectedUnitId==="core"&&game&&game.core){centerMap(game.core.position[0],game.core.position[1]);updateSelectionLabel("CORE · C0",game.core.position);}
    else {
      const unit=game&&(game.units||[]).find((item)=>item.id===selectedUnitId);
      if(unit){centerMap(unit.position[0],unit.position[1]);updateSelectionLabel(displayName||unitTypeName(unit.type),unit.position);}
      else if(game&&game.core)updateSelectionLabel("CORE · C0",game.core.position);
    }
    updateDashboard(snapshot);
  }

  function centerMap(x,y,minimumCell=16) {
    view.centerX=x;view.centerY=y;view.cell=Math.max(view.cell,minimumCell);drawMap();
  }

  function showTooltip(event) {
    const game=gameData();
    if(!game||dragging){tooltip.hidden=true;return;}
    const position=toWorld(event.clientX,event.clientY);setText("cursorCoordinate",positionText(position));
    const same=(candidate)=>candidate&&candidate[0]===position[0]&&candidate[1]===position[1];const items=[];
    if(game.core&&same(game.core.position))items.push(`Core · HP ${game.core.hp} · 护盾 ${game.core.shield}`);
    (game.units||[]).filter((unit)=>same(unit.position)).forEach((unit)=>items.push(`${unitTypeName(unit.type)} · HP ${unit.hp} · ${actionText(unit.action)}`));
    (game.enemies||[]).filter((enemy)=>same(enemy.position)).forEach((enemy)=>items.push(`敌方 ${unitTypeName(enemy.type)} · HP ${enemy.hp}`));
    const terrain=game.terrain||{};
    if((terrain.visibleResources||[]).some(same))items.push("资源晶体 · 可采集");else if((terrain.resources||[]).some(same))items.push("资源记忆 · 雾区");
    if((terrain.obstacles||[]).some(same))items.push("障碍");
    const explored=(terrain.explored||[]).some(same);
    tooltip.innerHTML=`<b>${position[0]}, ${position[1]}</b><br>${items.length?items.map(escapeHtml).join("<br>"):explored?"已探索":"未知区域"}`;
    const rect=wrap.getBoundingClientRect();tooltip.style.left=`${Math.max(8,Math.min(rect.width-230,event.clientX-rect.left+12))}px`;tooltip.style.top=`${Math.max(8,Math.min(rect.height-90,event.clientY-rect.top+12))}px`;tooltip.hidden=false;
  }

  wrap.addEventListener("pointerdown",(event)=>{
    const rect=canvas.getBoundingClientRect(),x=event.clientX-rect.left,y=event.clientY-rect.top;
    const hit=[...hitTargets].reverse().find((target)=>Math.hypot(target.x-x,target.y-y)<=target.radius);
    if(hit){selectUnit(hit.id,hit.name);return;}
    dragging=true;dragStart={x:event.clientX,y:event.clientY,centerX:view.centerX,centerY:view.centerY};wrap.classList.add("dragging");wrap.setPointerCapture(event.pointerId);tooltip.hidden=true;
  });
  wrap.addEventListener("pointermove",(event)=>{
    if(dragging&&dragStart){view.centerX=dragStart.centerX-(event.clientX-dragStart.x)/view.cell;view.centerY=dragStart.centerY-(event.clientY-dragStart.y)/view.cell;drawMap();}else showTooltip(event);
  });
  wrap.addEventListener("pointerup",()=>{dragging=false;dragStart=null;wrap.classList.remove("dragging");});
  wrap.addEventListener("pointercancel",()=>{dragging=false;dragStart=null;wrap.classList.remove("dragging");});
  wrap.addEventListener("pointerleave",()=>{tooltip.hidden=true;setText("cursorCoordinate","--, --");});
  wrap.addEventListener("wheel",(event)=>{
    event.preventDefault();const before=toWorld(event.clientX,event.clientY);view.cell=Math.max(3,Math.min(52,view.cell*(event.deltaY<0?1.15:.87)));const after=toWorld(event.clientX,event.clientY);view.centerX+=before[0]-after[0];view.centerY+=before[1]-after[1];drawMap();
  },{passive:false});
  $("zoomIn").addEventListener("click",()=>{view.cell=Math.min(52,view.cell*1.2);drawMap();});
  $("zoomOut").addEventListener("click",()=>{view.cell=Math.max(3,view.cell/1.2);drawMap();});
  $("resetView").addEventListener("click",fitMap);
  window.addEventListener("resize",resizeCanvas);

  async function loadInitialState() {
    try {
      const response=await fetch("/api/state",{cache:"no-store"});
      if(response.ok)updateDashboard(await response.json());
    } catch (_) {
      $("connection").dataset.status="error";setText("connectionText","面板服务不可用");
    }
  }

  async function refreshRuntimeClock() {
    if (runtimeRefreshInFlight) return;
    runtimeRefreshInFlight = true;
    try {
      const response = await fetch("/api/runtime", { cache: "no-store" });
      if (response.ok) {
        const data = await response.json();
        applyRuntime(data.runtime || {}, data.serverTime);
      }
    } catch (_) {
      // EventSource handles the visible connection error state.
    } finally {
      runtimeRefreshInFlight = false;
    }
  }

  const events=new EventSource("/api/events");
  events.onmessage=(event)=>updateDashboard(JSON.parse(event.data));
  events.onerror=()=>{$("connection").dataset.status="reconnecting";setText("connectionText","正在重连");};
  window.setInterval(refreshRuntimeClock,1000);
  resizeCanvas();renderModelCanvases();loadInitialState();refreshRuntimeClock();
})();
