/* Local, read-only atlas. No remote tiles, fonts, scripts, or runtime dependencies. */
"use strict";

const $ = (id) => document.getElementById(id);
const NS = "http://www.w3.org/2000/svg";
const R = 6378137;
// OSM representative campus point, EPSG:4326. Address and tile provenance:
// docs/evidence/shenzhen-20261021/campus-location.json (not a surveyed building point).
const testLocation = {tile:"49QHE",name:"深圳大学粤海校区",lon:113.9314749,lat:22.5359023};
const groupsZH = {optical:"光学波段", thermal:"热红外 / 温度", pan:"全色波段", ancillary:"辅助科学变量", qa:"质量标记 QA"};
const namesZH = {blue:"蓝光", green:"绿光", red:"红光", coastal:"海岸 / 气溶胶", nir08:"近红外 1", nir09:"近红外 2", swir16:"短波红外 1", swir22:"短波红外 2", cirrus:"卷云", pan:"全色", surface_temperature:"地表温度", temperature_uncertainty:"温度不确定性", thermal_radiance:"热辐射亮度", upwelling_radiance:"上行辐射亮度", downwelling_radiance:"下行辐射亮度", atmospheric_transmittance:"大气透过率", emissivity:"发射率", emissivity_stddev:"发射率标准差", cloud_distance:"距云距离", atmospheric_opacity:"大气不透明度", qa_pixel:"像元质量标记", qa_radsat:"辐射饱和标记", cloud_qa:"云质量标记", qa_aerosol:"气溶胶质量标记"};
const state = {catalog:null, tile:null, archive:null, track:null, time:0, group:"optical", band:null, mode:"natural", view:"map", revision:0, galleryRevision:0, compareRevision:0};
const cache = new Map();
const archiveRequests = new Map();
const queue = [];
let runningRequests = 0;
let toastTimer;

function element(tag, className, text) { const el = document.createElement(tag); if (className) el.className = className; if (text !== undefined) el.textContent = text; return el; }
function svgElement(tag, attributes = {}) { const el = document.createElementNS(NS, tag); Object.entries(attributes).forEach(([k,v]) => el.setAttribute(k, String(v))); return el; }
function prettySensor(a) { return String(a.sensor || a.product).replace(/oli[-_]?tirs/i,"OLI / TIRS").replace(/^oli$/i,"OLI / TIRS").replace(/^mss$/i,"MSS").replace(/^tm$/i,"TM").replace(/^etm\+?$/i,"ETM+"); }
function productLevel(a) { return String(a.product).toUpperCase(); }
function dateOnly(scene) { return String(scene?.time || "未知时间").slice(0,10); }
function platform(scene) { return String(scene?.platform || "未知卫星").replace(/landsat[-_ ]?/i,"Landsat "); }
function selectedArchive() { return state.catalog?.archives.find(a => a.id === state.archive); }
function selectedGroup(a = selectedArchive()) { return a?.groups.find(g => g.name === state.group); }
function sourceBand(band, index = state.time) { return band.source_bands?.[index] || band.source_band || band.name; }
function bandPresent(band, index) { return band.present?.[index] !== false; }
function formatNumber(value) { if (!Number.isFinite(value)) return "—"; if (Math.abs(value) >= 10000) return value.toExponential(2); return new Intl.NumberFormat("en", {maximumSignificantDigits:4}).format(value); }
function percent(value) { return Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "—"; }
function boundsUnion(archives) { return archives.reduce((out,a) => !out ? [...a.bounds] : [Math.min(out[0],a.bounds[0]),Math.min(out[1],a.bounds[1]),Math.max(out[2],a.bounds[2]),Math.max(out[3],a.bounds[3])], null); }
function trackKey(scene) { return scene.track || `unknown:${scene.index}`; }
function trackLabel(scene) { return scene.track || "轨道未提供 · 单景"; }
function sceneFrame(scene) { return scene.wrs_row == null ? "Row 未提供" : `Row ${String(scene.wrs_row).padStart(3,"0")}`; }
function tracks(a) { return [...new Set(a.scenes.map(trackKey))].sort(); }
function trackScenes(a, track) { return a.scenes.filter(scene => trackKey(scene) === track); }
function nearestScene(a, track) {
  if (a.id === state.archive && trackKey(a.scenes[state.time]) === track) return state.time;
  const candidates = trackScenes(a,track);
  if (!candidates.length) throw new Error("所选轨道没有已归档场景");
  const reference = new Date(selectedArchive()?.scenes[state.time]?.time).getTime();
  return candidates.reduce((best,scene) => Number.isFinite(reference) && Math.abs(new Date(scene.time).getTime()-reference) < Math.abs(new Date(best.time).getTime()-reference) ? scene : best).index;
}
function visibleArchives(scope) { return state.catalog.archives.filter(a => scope === "all" || a.tile_id === state.tile); }
function notice(message) { $("toast").textContent = message; $("toast").hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { $("toast").hidden = true; }, 4500); }
function option(value, label) { const el = element("option",null,label); el.value = value; return el; }
function fillSelect(id, entries, value) { const el = $(id); el.replaceChildren(...entries.map(([v,label]) => option(v,label))); el.value = String(value); el.disabled = !entries.length; }
function rgbPresets(group, index) { const available = new Set(group.bands.filter(b => bandPresent(b,index)).map(b => b.name)); return [{name:"natural",label:"自然彩色 · 红 / 绿 / 蓝",bands:["red","green","blue"]},{name:"false",label:"假彩色 · 近红外 / 红 / 绿",bands:["nir08","red","green"]}].filter(p => p.bands.every(b => available.has(b))); }
function defaultSelection(a) { state.archive = a.id; state.tile = a.tile_id; state.time = a.scenes[0].index; state.track = trackKey(a.scenes[0]); state.group = a.groups.some(g => g.name === "optical") ? "optical" : a.groups[0].name; const group = selectedGroup(a); state.band = group.bands[0].name; state.mode = rgbPresets(group,state.time)[0]?.name || "single"; }
function selection(group = selectedGroup(), index = state.time, mode = state.mode, band = state.band) { const preset = rgbPresets(group,index).find(p => p.name === mode); return preset ? {mode:"rgb",bands:preset.bands,label:preset.label.split(" · ")[0]} : {mode:mode === "valid" ? "valid" : "single",bands:[band],label:mode === "valid" ? "有效性掩膜" : namesZH[band] || band}; }

function refreshControls() {
  const a = selectedArchive(); if (!a) return;
  const tiles = [...new Set(state.catalog.archives.map(item => item.tile_id))];
  fillSelect("tile-select", tiles.map(t => [t, `${t} · ${state.catalog.archives.filter(item => item.tile_id === t).length} 个归档`]), state.tile);
  fillSelect("archive-select", visibleArchives("tile").map(item => [item.id, `${prettySensor(item)} · ${productLevel(item)}`]), a.id);
  fillSelect("track-select", tracks(a).map(track => { const scenes = trackScenes(a,track); return [track, scenes[0].track ? `${trackLabel(scenes[0])} · ${scenes.length} 景` : `${trackLabel(scenes[0])} · ${dateOnly(scenes[0])} · ${scenes[0].item_id || scenes[0].id}`]; }), state.track);
  fillSelect("scene-select", trackScenes(a,state.track).map(s => [s.index, `${s.time} · ${sceneFrame(s)} · ${s.item_id || s.id}`]), state.time);
  $("scene-select").title = a.scenes[state.time].id;
  $("selection-note").textContent = `${trackLabel(a.scenes[state.time])} / ${sceneFrame(a.scenes[state.time])}。仅显示所选一景，不补入其他轨道或日期。初次打开默认最早已归档景。`;
  fillSelect("group-select", a.groups.map(g => [g.name, `${groupsZH[g.name] || g.name} (${g.bands.length})`]), state.group);
  const group = selectedGroup(a);
  if (!group.bands.some(b => b.name === state.band)) state.band = group.bands[0].name;
  const modes = [...rgbPresets(group,state.time).map(p => [p.name,p.label]), ["single","单波段 · 原生变量"], ["valid","像元有效性掩膜"]];
  if (!modes.some(([m]) => m === state.mode)) state.mode = "single";
  fillSelect("mode-select", modes, state.mode);
  fillSelect("band-select", group.bands.map(b => [b.name, `${sourceBand(b)} · ${namesZH[b.name] || b.name}${bandPresent(b,state.time) ? "" : "（本景缺失）"}`]), state.band);
  const chosen = selection();
  $("band-control").hidden = chosen.mode === "rgb";
  $("rgb-info").hidden = chosen.mode !== "rgb";
  $("rgb-info").replaceChildren(...chosen.bands.map((b,i) => { const el = element("span","rgb-channel"); el.append(element("b",null,["R","G","B"][i]), document.createTextNode(sourceBand(group.bands.find(item => item.name === b)))); return el; }));
  $("workspace-hint").textContent = `${state.tile} / ${trackLabel(a.scenes[state.time])} / ${a.crs}`;
  $("gallery-count").textContent = state.catalog.archives.reduce((count,item) => count + tracks(item).length * item.groups.reduce((n,g) => n + g.bands.length,0),0);
}

function pump() { while (runningRequests < 2 && queue.length) { runningRequests++; const job = queue.shift(); job().finally(() => { runningRequests--; pump(); }); } }
function limitedRequest(job) { return new Promise((resolve,reject) => { queue.push(() => job().then(resolve,reject)); pump(); }); }
async function fetchPreview(a, time, group, bands, mode, size) {
  const params = new URLSearchParams({archive:a.id,time:String(time),group:group.name,bands:bands.join(","),mode,size:String(size)});
  const key = params.toString();
  if (cache.has(key)) { const existing = cache.get(key); cache.delete(key); cache.set(key,existing); return existing; }
  const previous = archiveRequests.get(a.id) || Promise.resolve();
  const promise = previous.catch(() => {}).then(() => limitedRequest(async () => {
    let response;
    for (let attempt = 0; attempt < 6; attempt++) {
      response = await fetch(`/api/preview?${key}`, {cache:"no-store"});
      if (response.status !== 503 || attempt === 5) break;
      await new Promise(resolve => setTimeout(resolve,250 * (attempt + 1)));
    }
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || payload.message || `请求失败 (${response.status})`);
    if (typeof payload.image !== "string" || !payload.image.startsWith("data:image/png;base64,")) throw new Error("服务未返回有效的 PNG 预览");
    return payload;
  }));
  archiveRequests.set(a.id,promise);
  cache.set(key,promise);
  try { const result = await promise; while (cache.size > 128) cache.delete(cache.keys().next().value); return result; }
  catch (error) { cache.delete(key); throw error; }
  finally { if (archiveRequests.get(a.id) === promise) archiveRequests.delete(a.id); }
}

function mercator(lon,lat) { return [R * lon * Math.PI / 180, R * Math.log(Math.tan(Math.PI / 4 + Math.max(-85.0511,Math.min(85.0511,lat)) * Math.PI / 360))]; }
function geographic(x,y) { return [x / R * 180 / Math.PI, (2 * Math.atan(Math.exp(y / R)) - Math.PI / 2) * 180 / Math.PI]; }
function niceStep(value) { const base = 10 ** Math.floor(Math.log10(value)); return [1,2,2.5,5,10].map(x => x * base).find(x => x >= value) || base * 10; }
function coordinateText(value, axis) { const compass = axis === "x" ? (value < 0 ? "W" : "E") : (value < 0 ? "S" : "N"); return `${Math.abs(value).toFixed(3)}° ${compass}`; }

function drawMap(container, a, preview, fitBounds = preview.bounds, interactive = true) {
  container._mapResizeObserver?.disconnect();
  container.replaceChildren();
  const width = 1000, height = 680, pad = interactive ? 56 : 42;
  const [west,south,east,north] = fitBounds;
  const scale = Math.min((width - pad * 2) / (east - west), (height - pad * 2) / (north - south));
  const offsetX = (width - (east - west) * scale) / 2, offsetY = (height - (north - south) * scale) / 2;
  const project = (x,y) => [offsetX + (x - west) * scale, offsetY + (north - y) * scale];
  const svg = svgElement("svg",{viewBox:`0 0 ${width} ${height}`,class:"map-svg",role:"img","aria-label":`${a.tile_id} ${prettySensor(a)} 空间影像，橙色虚线为 MGRS 边界`});
  const world = svgElement("g"); svg.append(world);
  const geoLow = geographic(west,south), geoHigh = geographic(east,north);
  const step = niceStep(Math.max(geoHigh[0] - geoLow[0],geoHigh[1] - geoLow[1]) / 5);
  for (let lon = Math.floor(geoLow[0] / step - 1) * step; lon <= geoHigh[0] + step; lon += step) {
    const x = project(mercator(lon,0)[0],0)[0]; world.append(svgElement("line",{x1:x,x2:x,y1:0,y2:height,class:"map-gridline"}));
    if (x > 32 && x < width - 42) { const label = svgElement("text",{x:x+5,y:height-18,class:"map-gridlabel"}); label.textContent = coordinateText(lon,"x"); world.append(label); }
  }
  for (let lat = Math.floor(geoLow[1] / step - 1) * step; lat <= geoHigh[1] + step && lat < 85; lat += step) {
    const y = project(0,mercator(0,lat)[1])[1]; world.append(svgElement("line",{x1:0,x2:width,y1:y,y2:y,class:"map-gridline"}));
    if (y > 32 && y < height - 38) { const label = svgElement("text",{x:12,y:y-6,class:"map-gridlabel"}); label.textContent = coordinateText(lat,"y"); world.append(label); }
  }
  const [imageX,imageY] = project(preview.bounds[0],preview.bounds[3]);
  const image = svgElement("image",{href:preview.image,x:imageX,y:imageY,width:(preview.bounds[2]-preview.bounds[0])*scale,height:(preview.bounds[3]-preview.bounds[1])*scale,preserveAspectRatio:"none"});
  world.append(image);
  const geometry = a.geometry?.type === "Feature" ? a.geometry.geometry : a.geometry;
  const polygons = geometry?.type === "Polygon" ? [geometry.coordinates] : geometry?.type === "MultiPolygon" ? geometry.coordinates : [];
  for (const polygon of polygons) { const path = polygon.map(ring => ring.map(([lon,lat],i) => { const p = project(...mercator(lon,lat)); return `${i ? "L" : "M"}${p[0]},${p[1]}`; }).join(" ") + " Z").join(" "); world.append(svgElement("path",{d:path,class:"map-boundary"})); }
  const rasterBoundary = svgElement("rect",{x:imageX,y:imageY,width:(preview.bounds[2]-preview.bounds[0])*scale,height:(preview.bounds[3]-preview.bounds[1])*scale,fill:"none",stroke:"#6d8978","stroke-width":.6,"stroke-opacity":.4,"vector-effect":"non-scaling-stroke"}); world.append(rasterBoundary);
  let campus = null;
  if (a.tile_id === testLocation.tile) {
    const point = project(...mercator(testLocation.lon,testLocation.lat));
    const marker = svgElement("g",{class:"campus-marker","aria-label":`${testLocation.name}，校园代表点（OSM）`});
    marker.append(svgElement("circle",{r:6,class:"campus-dot"}),svgElement("circle",{r:2,fill:"#ffffff"}));
    const label=svgElement("text",{x:11,y:4,class:"campus-label"});label.textContent=testLocation.name;
    const source=svgElement("tspan",{x:11,dy:17});source.textContent="校园代表点（OSM）";label.append(source);marker.append(label);
    const description=svgElement("title");description.textContent=`校园代表点（OSM） · ${testLocation.lon}, ${testLocation.lat} · WGS84`;marker.append(description);
    world.append(marker);campus={point,marker};
  }
  container.append(svg);
  const northIndicator = element("div","map-north","N"); const arrow = svgElement("svg",{width:15,height:22,viewBox:"0 0 15 22","aria-hidden":"true"}); arrow.append(svgElement("path",{d:"M7.5 0L15 21L7.5 16L0 21Z",fill:"#6b8066"}),svgElement("path",{d:"M7.5 0V16L15 21Z",fill:"#abb9a5"})); northIndicator.append(arrow); container.append(northIndicator);
  const coordinates = element("div","map-coordinate",`${a.tile_id} · ${a.crs}`); container.append(coordinates);
  const scaleEl = element("div","map-scale"); const scaleLabel = element("span"); const scaleLine = element("div","map-scale-line"); scaleEl.append(scaleLabel,scaleLine); container.append(scaleEl);
  const camera = {zoom:1,x:0,y:0};
  function update() {
    world.setAttribute("transform",`translate(${camera.x} ${camera.y}) scale(${camera.zoom})`);
    const pixelsPerSvg = Math.min(container.clientWidth / width, container.clientHeight / height);
    if (campus) campus.marker.setAttribute("transform",`translate(${campus.point[0]} ${campus.point[1]}) scale(${1/(camera.zoom*Math.max(.01,pixelsPerSvg))})`);
    const cameraCenterY = (height / 2 - camera.y) / camera.zoom;
    const latitude = geographic((west+east)/2,north - (cameraCenterY-offsetY)/scale)[1];
    const metersPerPixel = Math.cos(latitude * Math.PI / 180) / scale / camera.zoom / Math.max(.01,pixelsPerSvg);
    const targetMeters = niceStep(metersPerPixel * 60);
    scaleLabel.textContent = targetMeters >= 1000 ? `${formatNumber(targetMeters/1000)} km` : `${formatNumber(targetMeters)} m`;
    scaleLine.style.width = `${Math.max(20,targetMeters/metersPerPixel)}px`;
  }
  function zoom(factor, px=width/2, py=height/2) { const next = Math.max(.5,Math.min(20,camera.zoom*factor)); const ratio = next/camera.zoom; camera.x = px-(px-camera.x)*ratio; camera.y = py-(py-camera.y)*ratio; camera.zoom = next; update(); }
  function position(event) { const rect=svg.getBoundingClientRect(); const screenScale=Math.min(rect.width/width,rect.height/height); return [(event.clientX-rect.left-(rect.width-width*screenScale)/2)/screenScale,(event.clientY-rect.top-(rect.height-height*screenScale)/2)/screenScale]; }
  if (interactive) {
    const tools = element("div","map-tools"); [["+","放大地图",()=>zoom(1.4)],["−","缩小地图",()=>zoom(1/1.4)],["⌖","恢复格网范围",()=>{camera.zoom=1;camera.x=0;camera.y=0;update();}]].forEach(([text,label,handler])=>{const button=element("button",text==="⌖"?"fit-button":null,text);button.type="button";button.title=label;button.setAttribute("aria-label",label);button.addEventListener("click",handler);tools.append(button);});container.append(tools);
    if (campus) {
      const locate=element("button","campus-locate","定位校园");locate.type="button";locate.title="定位深圳大学粤海校区 · OSM 代表点";
      locate.setAttribute("aria-label","定位深圳大学粤海校区");
      locate.addEventListener("click",()=>{camera.zoom=12;camera.x=width/2-campus.point[0]*camera.zoom;camera.y=height/2-campus.point[1]*camera.zoom;update();notice("已定位粤海校区代表点（OSM）。仅放大现有预览，未提高影像分辨率。");});
      container.append(locate);
    }
    let drag=null;
    svg.addEventListener("pointerdown",event=>{if(event.button!==0)return;const p=position(event);drag={x:p[0],y:p[1],cx:camera.x,cy:camera.y};svg.setPointerCapture(event.pointerId);});
    svg.addEventListener("pointermove",event=>{const p=position(event);if(drag){camera.x=drag.cx+p[0]-drag.x;camera.y=drag.cy+p[1]-drag.y;update();}const worldX=(p[0]-camera.x)/camera.zoom,worldY=(p[1]-camera.y)/camera.zoom;const geo=geographic(west+(worldX-offsetX)/scale,north-(worldY-offsetY)/scale);coordinates.textContent=`${coordinateText(geo[0],"x")}  ${coordinateText(geo[1],"y")}`;});
    const stop=()=>{drag=null;};svg.addEventListener("pointerup",stop);svg.addEventListener("pointercancel",stop);svg.addEventListener("lostpointercapture",stop);
    svg.addEventListener("wheel",event=>{event.preventDefault();zoom(event.deltaY<0?1.15:1/1.15,...position(event));},{passive:false});
    svg.setAttribute("tabindex","0");svg.addEventListener("keydown",event=>{if(event.key==="+"||event.key==="="){zoom(1.4);event.preventDefault();}else if(event.key==="-"){zoom(1/1.4);event.preventDefault();}else if(event.key==="0"){camera.zoom=1;camera.x=0;camera.y=0;update();}});
  }
  update();
  container._mapResizeObserver=new ResizeObserver(update);
  container._mapResizeObserver.observe(container);
}

function detail(label, value, className) { $("observation-details").append(element("dt",null,label),element("dd",className,value)); }
function updateObservation(a, group, chosen, preview = null) {
  const scene=a.scenes[state.time];
  $("map-title").textContent=`${a.tile_id} · ${chosen.label}`;
  $("map-product").textContent=`${productLevel(a)} / ${group.resolution || "—"} m`;
  const maskLegend=document.querySelector(".map-footer span:nth-child(2)");maskLegend.replaceChildren(element("i","transparent-swatch"),document.createTextNode(chosen.mode==="valid"?"灰色为无效像元":"透明为无有效像元"));
  document.querySelector(".context-note p").textContent=a.extent_kind==="window"?"当前归档为格网内的窗口数据，地图按实际栅格范围显示；不代表该 MGRS 整格网已下载。地图完全由本地数据绘制，无在线底图请求。":chosen.mode==="valid"?"有效性掩膜以绿色表示源数据有效，灰色表示无效；景外与温度缺测会造成空缺。此掩膜不筛除云，也不等于有效科学观测。地图无在线底图请求。":"完整格网 ≠ 全格网有效覆盖。影像按固定 MGRS 范围归档；景外、无数据及部分温度缺测区域以透明显示。地图完全由本地数据绘制，无在线底图请求。";
  document.querySelector(".validity-block>div:first-child>span").textContent="整幅预览采样有效像元";
  document.querySelector(".validity-block>p").textContent="按预览采样统计；RGB 使用三个通道共同有效区域。有效像元不等于无云像元。";
  $("observation-title").textContent=prettySensor(a);
  $("observation-details").replaceChildren();
  detail("观测日期 / 卫星",`${dateOnly(scene)} · ${platform(scene)}`);
  detail("轨道 / 沿轨景框",`${trackLabel(scene)} · ${sceneFrame(scene)}`);
  detail("获取时间 UTC",scene.time);
  detail("原生格网 / 变量组",`${group.resolution || "—"} m · ${(group.shape || a.shape).join(" × ")} · ${group.name}${a.extent_kind==="window"?" · 窗口数据":" · 完整格网"}`);
  detail("源坐标系",a.crs);
  detail("产品标识",scene.id,"scene-id");
  detail("本地归档",a.relative_path,"scene-id");
  $("preview-legend").replaceChildren();
  $("legend-unit").textContent="";
  $("validity-value").textContent="—";$("validity-fill").style.width="0%";
  $("download-preview").classList.add("disabled");$("download-preview").setAttribute("aria-disabled","true");$("download-preview").removeAttribute("href");
  $("legend-title").textContent=chosen.mode==="rgb" ? "RGB 通道" : chosen.mode==="valid" ? "有效性掩膜" : "数值与颜色";
  $("legend-note").textContent="正在读取变量与有效性掩膜…";
  if (!preview) return;
  const stats=preview.stats || [];
  const legend=preview.legend || {};
  if(chosen.mode==="rgb") {
    $("legend-unit").textContent=stats[0]?.unit==="1"?"反射率 · 无量纲":stats[0]?.unit||"";
    const chips=element("div","legend-channels");chosen.bands.forEach((b,i)=>{const chip=element("span","legend-chip");chip.append(element("b",null,["R","G","B"][i]),document.createTextNode(stats[i]?.source_band || sourceBand(group.bands.find(item=>item.name===b))));chips.append(chip);});$("preview-legend").append(chips);
    $("legend-note").textContent="各通道分别按预览有效值 P2–P98 拉伸。显示颜色不用于跨传感器数值比较。";
  } else {
    const colors=legend.colors?.length ? legend.colors : ["#30205b","#267c8a","#6fc27d","#f8e797"];
    const qa=group.bands.find(b=>b.name===chosen.bands[0])?.kind==="qa";
    const discrete=qa||chosen.mode==="valid";
    const bar=element("div",discrete?"legend-discrete":"legend-gradient");if(discrete){colors.slice(0,32).forEach((color,i)=>{const swatch=element("i");swatch.style.backgroundColor=color;swatch.title=legend.labels?.[i]||"";bar.append(swatch);});}else bar.style.background=`linear-gradient(90deg, ${colors.slice(0,32).join(",")})`;
    const values=element("div","legend-values");const labels=legend.labels || [];
    values.append(element("span",null,labels[0] || formatNumber(stats[0]?.p02)),element("span",null,labels[labels.length-1] || formatNumber(stats[0]?.p98)));$("preview-legend").append(bar,values);
    $("legend-unit").textContent=stats[0]?.unit === "1" ? "无量纲" : stats[0]?.unit || "";
    $("legend-note").textContent=chosen.mode==="valid" ? "绿色为有效，灰色为无效。该掩膜仅表示源数据有效性，不筛除云。" : qa ? "QA 保留打包整数，以离散颜色区分不同值。颜色没有好坏顺序；QA 的 0 也可能有效。" : `${legend.note || "按预览有效值 P2–P98 拉伸。已应用该波段的 scale 与 offset；L1 仍为原始 DN。"}`;
    if(qa && legend.category_count) $("legend-note").textContent+=` 本预览有 ${legend.category_count} 种整数值。`;
    if(qa && chosen.mode!=="valid" && legend.categories?.length){const disclosure=element("details","qa-categories");disclosure.append(element("summary",null,`查看 QA 整数类别 (${legend.categories.length}${legend.category_count>legend.categories.length?" / "+legend.category_count:""})`));const list=element("div","qa-category-list");legend.categories.forEach(category=>{const item=element("span","qa-category");const swatch=element("i");swatch.style.backgroundColor=category.color;item.append(swatch,document.createTextNode(String(category.value)));list.append(item);});disclosure.append(list);$("preview-legend").append(disclosure);}
  }
  const fraction=Number.isFinite(preview.valid_fraction)?preview.valid_fraction:stats.length===1?stats[0]?.valid_fraction:null;
  $("validity-value").textContent=percent(fraction);$("validity-fill").style.width=`${Number.isFinite(fraction)?Math.max(0,Math.min(100,fraction*100)):0}%`;
  const link=$("download-preview");link.href=preview.image;link.download=`${a.tile_id}_${a.product}_${dateOnly(scene)}_${chosen.bands.join("-")}_${chosen.mode}.png`;link.classList.remove("disabled");link.setAttribute("aria-disabled","false");
}

function showError(container,error,retry) { container._mapResizeObserver?.disconnect();container.replaceChildren(); const box=element("div","error-state");box.append(element("p",null,error.message || String(error)));if(retry){const button=element("button","retry-button","重新读取");button.type="button";button.addEventListener("click",retry);box.append(button);}container.append(box); }
async function renderMain() {
  const revision=++state.revision;const a=selectedArchive(),group=selectedGroup(a),chosen=selection();if(!a||!group)return;
  const container=$("main-map");updateObservation(a,group,chosen);
  const busy=element("div","map-busy");busy.append(element("span","spinner"),document.createTextNode("读取本地影像…"));container.querySelector(".map-busy")?.remove();container.append(busy);
  if(chosen.bands.some(name=>!bandPresent(group.bands.find(b=>b.name===name),state.time))){showError(container,new Error("本景未提供所选变量；该变量仍保留在归档结构中。请选择其他变量或时间。"));$("legend-note").textContent="本景变量缺失（present = false），不生成占位影像。";return;}
  try {const preview=await fetchPreview(a,state.time,group,chosen.bands,chosen.mode,768);if(revision!==state.revision)return;drawMap(container,a,preview);updateObservation(a,group,chosen,preview);}catch(error){if(revision===state.revision){showError(container,error,renderMain);$("legend-note").textContent="预览读取失败，可重试；原始归档未被修改。";}}
}

function defaultRGB(a,index) { const group=a.groups.find(g=>g.name==="optical") || a.groups[0];const preset=rgbPresets(group,index)[0];return {group,bands:preset?.bands || [group.bands[0].name],mode:preset?"rgb":"single",label:preset?.label.split(" · ")[0] || "单波段"}; }
async function renderComparison() {
  const revision=++state.compareRevision;const grid=$("compare-grid");grid.querySelectorAll(".map-canvas").forEach(canvas=>canvas._mapResizeObserver?.disconnect());grid.replaceChildren();const archives=visibleArchives($("compare-scope").value);const tasks=[];
  for(const a of archives) for(const track of tracks(a)) {
    const index=nearestScene(a,track),scene=a.scenes[index],choice=defaultRGB(a,index);
    const card=element("article","compare-card");card.dataset.archive=a.id;card.dataset.track=track;card.dataset.scene=scene.id;
    const heading=element("div","compare-heading"),left=element("div");
    left.append(element("h3",null,`${prettySensor(a)} · ${a.tile_id}`),element("p",null,`${trackLabel(scene)} · ${sceneFrame(scene)}`),element("p",null,`${scene.time} / ${platform(scene)}${a.extent_kind==="window"?" · 窗口数据":""}`));
    heading.append(left,element("span","product-tag",`${productLevel(a)} · ${choice.group.resolution} m`));
    const source=element("p","comparison-source",scene.item_id || scene.id);source.title=scene.id;
    const canvas=element("div","map-canvas");canvas.append(element("div","loading-state","读取影像…"));
    const caption=element("div","compare-caption");caption.append(element("span",null,`${choice.label} · ${choice.bands.join(" / ")}`),element("span","compare-validity","—"));
    card.append(heading,source,canvas,caption);grid.append(card);
    const fit=boundsUnion(archives.filter(other=>other.tile_id===a.tile_id));
    tasks.push(async()=>{if(revision!==state.compareRevision)return;try{const preview=await fetchPreview(a,index,choice.group,choice.bands,choice.mode,512);if(revision!==state.compareRevision)return;drawMap(canvas,a,preview,fit,false);caption.lastChild.textContent=`预览有效 ${percent(preview.valid_fraction)}`;}catch(error){if(revision===state.compareRevision)showError(canvas,error,renderComparison);}});
  }
  await runWorkers(tasks,()=>revision===state.compareRevision);
}

async function runWorkers(tasks,isCurrent) { let next=0;async function worker(){while(next<tasks.length && isCurrent()){const task=tasks[next++];await task();}}await Promise.all([worker(),worker()]); }
function filterGallery() { const needle=$("gallery-filter").value.trim().toLowerCase();let shown=0;document.querySelectorAll(".band-card").forEach(card=>{card.hidden=Boolean(needle)&&!card.dataset.search.includes(needle);if(!card.hidden)shown++;});document.querySelectorAll(".gallery-section").forEach(section=>{section.hidden=![...section.querySelectorAll(".band-card")].some(card=>!card.hidden);});$("gallery-visible").textContent=`${shown} 个变量`; }
async function renderGallery() {
  const revision=++state.galleryRevision,grid=$("gallery-grid");grid.replaceChildren();const tasks=[];let completed=0,failed=0,missing=0,total=0;
  const progress=()=>{$("gallery-progress").textContent=`${completed} / ${total} 已显示${missing?` · ${missing} 本景缺失`:""}${failed?` · ${failed} 读取失败`:""}`;};
  for(const a of visibleArchives($("gallery-scope").value)) for(const track of tracks(a)) {const index=nearestScene(a,track),scene=a.scenes[index];for(const group of a.groups){const section=element("section","gallery-section");const heading=element("h3","gallery-heading",`${a.tile_id} / ${prettySensor(a)} · ${trackLabel(scene)} · ${groupsZH[group.name]||group.name}`);heading.append(element("span",null,`${dateOnly(scene)} · ${sceneFrame(scene)} · ${productLevel(a)} · ${group.resolution} m${a.extent_kind==="window"?" · 窗口数据":""}`));const source=element("p","gallery-source",scene.item_id || scene.id);source.title=scene.id;const cards=element("div","gallery-cards");section.append(heading,source,cards);grid.append(section);
    for(const band of group.bands) {total++;const card=element("button","band-card");card.type="button";card.dataset.archive=a.id;card.dataset.track=track;card.dataset.scene=scene.id;card.dataset.group=group.name;card.dataset.band=band.name;card.dataset.search=`${a.tile_id} ${a.product} ${a.sensor} ${trackLabel(scene)} ${sceneFrame(scene)} ${scene.id} ${group.name} ${groupsZH[group.name]} ${band.name} ${namesZH[band.name]||""} ${sourceBand(band,index)}`.toLowerCase();const thumbnail=element("div","band-thumbnail");thumbnail.append(element("span","spinner"),element("span","band-number",sourceBand(band,index)));const body=element("div","band-card-body");body.append(element("h4",null,namesZH[band.name]||band.name),element("p",null,band.name));const meta=element("div","band-card-meta");const info=element("span",null,band.kind==="qa"?"QA 打包整数":band.unit==="1"?"无量纲":band.unit||"DN"),valid=element("span",null,"等待读取");meta.append(info,valid);body.append(meta);card.append(thumbnail,body);cards.append(card);card.setAttribute("aria-label",`${a.tile_id} ${prettySensor(a)} ${trackLabel(scene)} ${band.name} ${sourceBand(band,index)} 详细地图`);card.addEventListener("click",()=>{state.tile=a.tile_id;state.archive=a.id;state.track=track;state.time=index;state.group=group.name;state.band=band.name;state.mode="single";refreshControls();switchView("map");window.scrollTo({top:0,behavior:"smooth"});});
      if(!bandPresent(band,index)){thumbnail.querySelector(".spinner").remove();thumbnail.append(element("span","missing-label","本景未提供"));valid.textContent="present = false";missing++;continue;}
      tasks.push(async()=>{if(revision!==state.galleryRevision)return;try{const preview=await fetchPreview(a,index,group,[band.name],"single",256);if(revision!==state.galleryRevision)return;thumbnail.querySelector(".spinner")?.remove();const img=element("img");img.src=preview.image;img.alt=`${band.name} 单波段空间预览`;thumbnail.prepend(img);const fraction=preview.valid_fraction ?? preview.stats?.[0]?.valid_fraction;valid.textContent=`有效 ${percent(fraction)}`;card.dataset.loaded="true";completed++;}catch(error){if(revision!==state.galleryRevision)return;thumbnail.querySelector(".spinner")?.remove();thumbnail.append(element("span","missing-label","读取失败 · 点击重试"));card.title=error.message;valid.textContent="打开地图可重试";failed++;}if(revision===state.galleryRevision)progress();});
    }
  }}
  progress();filterGallery();await runWorkers(tasks,()=>revision===state.galleryRevision);
}

function switchView(view) {
  state.view=view;state.revision++;state.galleryRevision++;state.compareRevision++;
  document.querySelectorAll(".tab").forEach(tab=>{const active=tab.dataset.view===view;tab.classList.toggle("active",active);tab.setAttribute("aria-selected",String(active));tab.tabIndex=active?0:-1;});
  ["map","compare","gallery"].forEach(name=>{$(`view-${name}`).hidden=name!==view;});
  if(!state.catalog?.archives.length)return;
  if(view==="map")renderMain();else if(view==="compare")renderComparison();else renderGallery();
}
function changeSelection() {refreshControls();switchView(state.view);}

$("tile-select").addEventListener("change",event=>{defaultSelection(state.catalog.archives.find(a=>a.tile_id===event.target.value));changeSelection();});
$("archive-select").addEventListener("change",event=>{defaultSelection(state.catalog.archives.find(a=>a.id===event.target.value));changeSelection();});
$("track-select").addEventListener("change",event=>{const a=selectedArchive();state.track=event.target.value;state.time=nearestScene(a,state.track);changeSelection();notice("已切换轨道；显示该轨道内与原选择时间最近的一景。");});
$("scene-select").addEventListener("change",event=>{state.time=Number(event.target.value);changeSelection();});
$("group-select").addEventListener("change",event=>{state.group=event.target.value;state.band=selectedGroup().bands[0].name;state.mode=rgbPresets(selectedGroup(),state.time)[0]?.name||"single";changeSelection();});
$("mode-select").addEventListener("change",event=>{state.mode=event.target.value;changeSelection();});
$("band-select").addEventListener("change",event=>{state.band=event.target.value;changeSelection();});
document.querySelectorAll(".tab").forEach(tab=>{tab.addEventListener("click",()=>switchView(tab.dataset.view));tab.addEventListener("keydown",event=>{if(event.key!=="ArrowLeft"&&event.key!=="ArrowRight")return;const tabs=[...document.querySelectorAll(".tab")];const next=tabs[(tabs.indexOf(tab)+(event.key==="ArrowRight"?1:tabs.length-1))%tabs.length];next.focus();switchView(next.dataset.view);event.preventDefault();});});
$("gallery-filter").addEventListener("input",filterGallery);
$("gallery-scope").addEventListener("change",renderGallery);
$("compare-scope").addEventListener("change",renderComparison);

async function initialize() {
  try {const response=await fetch("/api/catalog",{cache:"no-store"});const catalog=await response.json();if(!response.ok)throw new Error(catalog.error||`目录读取失败 (${response.status})`);if(!Array.isArray(catalog.archives))throw new Error("目录服务未返回归档列表");state.catalog=catalog;
    ["tiles","archives","scenes","variables"].forEach(name=>{$(`count-${name}`).textContent=catalog.counts?.[name]??"—";});$("root-label").textContent=catalog.root_label || "本地归档";$("root-label").title=catalog.root_label || "";
    if(catalog.errors?.length){$("catalog-error").hidden=false;$("catalog-error").textContent=`部分归档无法读取：\n${catalog.errors.map(error=>typeof error==="string"?error:JSON.stringify(error)).join("\n")}`;}
    if(!catalog.archives.length){showError($("main-map"),new Error("当前目录没有可读取的已提交 Zarr 归档。请使用 landsat-grits gui <目录> 指向已有数据目录。"));$("map-title").textContent="未发现可读归档";return;}
    defaultSelection(catalog.archives.find(a=>a.tile_id==="49QHE"&&a.product==="oli-l2") || catalog.archives.find(a=>a.tile_id==="49QHE") || catalog.archives[0]);refreshControls();switchView("map");
  }catch(error){$("catalog-error").hidden=false;$("catalog-error").textContent=`无法连接本地归档服务：${error.message}`;showError($("main-map"),error,()=>location.reload());$("map-title").textContent="归档连接失败";}
}
initialize();
