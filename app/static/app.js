let projectId = null;
let config = {};
let currentPlaces = [];
let mapState = { view:null, pointLayer:null, routeLayer:null, sketch:null, selectedGraphic:null };

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

async function api(url, options={}) {
  const res = await fetch(url, options);
  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; } catch { data = {detail:text}; }
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

function setStatus(el, msg, error=false) {
  el.textContent = msg || "";
  el.classList.toggle("error", error);
}

function esc(v) {
  return String(v ?? "").replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}

function markStep(activeStep) {
  $$('.progress-step').forEach(step => {
    const number = Number(step.dataset.step);
    step.classList.toggle('active', number === activeStep);
    step.classList.toggle('completed', number < activeStep);
  });
}

function setBusy(button, busy, busyLabel) {
  if (!button.dataset.label) button.dataset.label = button.textContent;
  button.disabled = busy;
  button.textContent = busy ? busyLabel : button.dataset.label;
}

async function loadConfig() {
  config = await api('/api/config');
  const provider = $('#providerStatus');
  provider.classList.toggle('ready', config.gemini_enabled);
  provider.classList.toggle('warning', !config.gemini_enabled);
  provider.querySelector('span').textContent = config.gemini_enabled
    ? `${config.gemini_model} ready`
    : 'Gemini key needed';
}

$('#file').addEventListener('change', event => {
  $('#fileName').textContent = event.target.files[0]?.name || '選擇檔案';
});

$('#uploadForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  setStatus($('#uploadStatus'), '正在上載並讀取文本…');
  $('#documentMetrics').classList.add('hidden');
  setBusy($('#uploadBtn'), true, '上載中…');
  const fd = new FormData();
  const f = $('#file').files[0];
  if (!f) {
    setBusy($('#uploadBtn'), false, '上載中…');
    setStatus($('#uploadStatus'), '請先選擇文本檔案。', true);
    return;
  }
  fd.append('file', f);
  if ($('#title').value.trim()) fd.append('title', $('#title').value.trim());
  if ($('#period').value.trim()) fd.append('historical_period', $('#period').value.trim());
  try {
    const p = await api('/api/projects', {method:'POST', body:fd});
    projectId = p.id;
    setStatus($('#uploadStatus'), `已加入「${p.filename}」。下一步可抽取地名。`);
    $('#wordCount').textContent = p.word_count.toLocaleString();
    $('#pageCount').textContent = p.page_count.toLocaleString();
    $('#pageCountNote').textContent = p.page_count_estimated
      ? `按每頁約 ${p.words_per_estimated_page.toLocaleString()} 字估算`
      : 'PDF 實際頁數';
    $('#documentMetrics').classList.remove('hidden');
    $('#extractBtn').disabled = false;
  } catch (err) { setStatus($('#uploadStatus'), err.message, true); }
  finally { setBusy($('#uploadBtn'), false, '上載中…'); }
});

$('#extractBtn').addEventListener('click', async () => {
  if (!projectId) return;
  setBusy($('#extractBtn'), true, 'Gemini 分析中…');
  setStatus($('#uploadStatus'), 'Gemini 正在辨識路線地名…');
  try {
    const result = await api(`/api/projects/${projectId}/extract`, {method:'POST'});
    currentPlaces = result.places;
    renderPlaces(currentPlaces);
    $('#step2').classList.remove('hidden');
    markStep(2);
    setStatus($('#uploadStatus'), `已抽取 ${result.count} 個地名。請在下方選擇需要加入路線的地名。`);
    $('#step2').scrollIntoView({behavior:'smooth'});
  } catch (err) {
    setStatus($('#uploadStatus'), err.message, true);
  } finally {
    setBusy($('#extractBtn'), false, 'Gemini 分析中…');
  }
});

function renderPlaces(places) {
  const tbody = $('#placesTable tbody');
  tbody.innerHTML = places.map(p => `
    <tr data-id="${p.id}">
      <td><span class="readonly-value order-value">${p.route_order}</span></td>
      <td><span class="readonly-value">${esc(p.date_text || '—')}</span></td>
      <td><span class="readonly-value place-value">${esc(p.original_name)}</span></td>
      <td><select data-f="route_role">
        <option value="passed" ${p.route_role==='passed'?'selected':''}>經過</option>
        <option value="mentioned_only" ${p.route_role==='mentioned_only'?'selected':''}>提及</option>
        <option value="passed_and_mentioned" ${p.route_role==='passed_and_mentioned'?'selected':''}>經過及提及</option>
      </select></td>
      <td><input data-f="historical_region" value="${esc(p.historical_region)}" placeholder="可修改"></td>
      <td><span class="readonly-value sentence-value">${esc(p.sentence || '—')}</span></td>
    </tr>`).join('');
}

async function saveAllPlaces() {
  const rows = $$('#placesTable tbody tr');
  for (const tr of rows) {
    const payload = {};
    tr.querySelectorAll('[data-f]').forEach(el => {
      let v = el.value;
      if (el.dataset.f === 'historical_region' && v === '') v = null;
      payload[el.dataset.f] = v;
    });
    await api(`/api/places/${tr.dataset.id}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  }
  currentPlaces = await api(`/api/projects/${projectId}/places`);
  currentPlaces.sort((a,b)=>a.route_order-b.route_order);
  renderPlaces(currentPlaces);
  setStatus($('#placesStatus'), `✓ 已儲存 ${currentPlaces.length} 個地名的分類及歷史區域。`);
}

$('#savePlacesBtn').addEventListener('click', async () => {
  try { await saveAllPlaces(); } catch (err) { setStatus($('#placesStatus'), err.message, true); }
});

$('#confirmPlacesBtn').addEventListener('click', async () => {
  try {
    await saveAllPlaces();
    const r = await api(`/api/projects/${projectId}/confirm-places`, {method:'POST'});
    setStatus($('#placesStatus'), `✓ 已選擇 ${r.selected_count} 個路線地名；另有 ${r.mentioned_count} 個提及地名保留作參考。`);
    $('#confirmPlacesBtn').classList.add('hidden');
    $('#unconfirmPlacesBtn').classList.remove('hidden');
    $('#step3').classList.remove('hidden');
    markStep(3);
    $('#step3').scrollIntoView({behavior:'smooth'});
  } catch (err) { setStatus($('#placesStatus'), err.message, true); }
});

$('#unconfirmPlacesBtn').addEventListener('click', async () => {
  try {
    await api(`/api/projects/${projectId}/unconfirm-places`, {method:'POST'});
    $('#confirmPlacesBtn').classList.remove('hidden');
    $('#unconfirmPlacesBtn').classList.add('hidden');
    $('#step3').classList.add('hidden');
    markStep(2);
    setStatus($('#placesStatus'), '已取消確認，可以再次修改分類及歷史區域。');
  } catch (err) { setStatus($('#placesStatus'), err.message, true); }
});

$('#geocodeBtn').addEventListener('click', async () => {
  setBusy($('#geocodeBtn'), true, '正在配對…');
  setStatus($('#geoStatus'), '正在比較歷史與現代地名來源；地點較多時可能需要數分鐘。');
  try {
    const r = await api(`/api/projects/${projectId}/geocode`, {method:'POST'});
    renderGeocodes(r.results);
    setStatus($('#geoStatus'), `✓ 經緯度配對完成：${r.count} 個地點。`);
    $('#step4').classList.remove('hidden');
    markStep(4);
    $('#downloadMap').href = `/api/projects/${projectId}/map.geojson?download=true`;
    $('#step4').scrollIntoView({behavior:'smooth'});
  } catch (err) {
    setStatus($('#geoStatus'), err.message, true);
  } finally { setBusy($('#geocodeBtn'), false, '正在配對…'); }
});

function classLabel(c) {
  return c === 'confirmed' ? '確認經緯度' : c === 'possible' ? '有可能' : '資料不足';
}

function renderGeocodes(results) {
  const tbody = $('#geoTable tbody');
  tbody.innerHTML = results.map(r => {
    const opts = (r.candidates||[]).map(c => `<option value="${c.id}" ${c.source===r.source && Math.abs(c.lon-r.lon)<1e-8 && Math.abs(c.lat-r.lat)<1e-8?'selected':''}>${esc(c.source)}｜${esc(c.candidate_name)}｜${Number(c.score).toFixed(2)}</option>`).join('');
    return `<tr data-place-id="${r.place_id}">
      <td>${r.route_order}</td><td>${esc(r.name)}</td>
      <td><span class="badge ${r.coord_class}">${classLabel(r.coord_class)}</span></td>
      <td>${Number(r.score||0).toFixed(2)}</td><td>${esc(r.source||'')}</td>
      <td>${r.lon ?? ''}</td><td>${r.lat ?? ''}</td>
      <td>${opts ? `<select class="candidate-select"><option value="">--選候選--</option>${opts}</select>` : '無候選'}</td>
    </tr>`;
  }).join('');

  $$('.candidate-select').forEach(sel => sel.addEventListener('change', async e => {
    if (!e.target.value) return;
    const tr = e.target.closest('tr');
    try {
      await api(`/api/places/${tr.dataset.placeId}/select-candidate/${e.target.value}`, {method:'POST'});
      const places = await api(`/api/projects/${projectId}/places?candidates=true`);
      const converted = places.map(p=>({place_id:p.id, route_order:p.route_order, name:p.normalized_name, coord_class:p.coord_class, score:p.coord_score, source:p.coord_source, lon:p.selected_lon, lat:p.selected_lat, candidates:p.candidates}));
      renderGeocodes(converted);
    } catch(err){ alert(err.message); }
  }));
}

async function waitForArcGIS() {
  for (let i=0;i<100;i++) {
    if (window.$arcgis?.import) return;
    await new Promise(r=>setTimeout(r,100));
  }
  throw new Error('ArcGIS SDK未能載入。');
}

async function initMap() {
  await waitForArcGIS();
  const [esriConfig, Map, MapView, GraphicsLayer, Graphic, Sketch] = await Promise.all([
    $arcgis.import('@arcgis/core/config.js'),
    $arcgis.import('@arcgis/core/Map.js'),
    $arcgis.import('@arcgis/core/views/MapView.js'),
    $arcgis.import('@arcgis/core/layers/GraphicsLayer.js'),
    $arcgis.import('@arcgis/core/Graphic.js'),
    $arcgis.import('@arcgis/core/widgets/Sketch.js')
  ]);
  if (config.arcgis_api_key) esriConfig.apiKey = config.arcgis_api_key;

  if (mapState.view) { mapState.view.destroy(); mapState = {view:null,pointLayer:null,routeLayer:null,sketch:null,selectedGraphic:null}; }
  const routeLayer = new GraphicsLayer({title:'文本次序路線'});
  const pointLayer = new GraphicsLayer({title:'行程地點'});
  const map = new Map({basemap: config.arcgis_api_key ? 'arcgis/topographic' : 'osm', layers:[routeLayer, pointLayer]});
  const view = new MapView({container:'mapView', map, center:[120.2,30.3], zoom:7});
  await view.when();
  const sketch = new Sketch({view, layer:pointLayer, creationMode:'single', availableCreateTools:[], visibleElements:{settingsMenu:false}});
  view.ui.add(sketch, 'top-right');
  mapState = {view, pointLayer, routeLayer, sketch, Graphic, selectedGraphic:null};

  sketch.on('update', async (event) => {
    if (event.state !== 'complete') return;
    for (const g of event.graphics) {
      const id = g.attributes?.place_id;
      const geom = g.geometry;
      if (!id || geom?.type !== 'point') continue;
      try {
        await api(`/api/places/${id}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({selected_lon:geom.longitude, selected_lat:geom.latitude})});
        g.attributes.coord_class = 'confirmed';
        g.attributes.coord_source = 'manual';
        applyPointSymbol(g);
      } catch(err) { setStatus($('#mapStatus'), err.message, true); }
    }
    rebuildRoute();
  });

  view.on('click', async (event) => {
    const hit = await view.hitTest(event, {include:[pointLayer]});
    const found = hit.results?.find(r => r.graphic);
    mapState.selectedGraphic = found?.graphic || null;
    $('#deleteMapPointBtn').disabled = !mapState.selectedGraphic;
    if (mapState.selectedGraphic) setStatus($('#mapStatus'), `已選：${mapState.selectedGraphic.attributes.route_order}｜${mapState.selectedGraphic.attributes.name}。可用Sketch移動，或按刪除。`);
  });
}

function pointSymbol(status) {
  if (status === 'confirmed') return {type:'simple-marker', style:'circle', color:'#16845b', size:10, outline:{color:'white',width:1}};
  if (status === 'possible') return {type:'simple-marker', style:'triangle', color:'#d97706', size:11, outline:{color:'white',width:1}};
  return {type:'simple-marker', style:'circle', color:'#6b7280', size:8, outline:{color:'white',width:1}};
}

function applyPointSymbol(g) { g.symbol = pointSymbol(g.attributes?.coord_class); }

function rebuildRoute() {
  if (!mapState.routeLayer || !mapState.pointLayer) return;
  mapState.routeLayer.removeAll();
  const pts = mapState.pointLayer.graphics.toArray().filter(g=>g.geometry?.type==='point').sort((a,b)=>a.attributes.route_order-b.attributes.route_order);
  if (pts.length < 2) return;
  const paths = [pts.map(g=>[g.geometry.longitude, g.geometry.latitude])];
  const g = new mapState.Graphic({
    geometry:{type:'polyline', paths, spatialReference:{wkid:4326}},
    symbol:{type:'simple-line', color:'#315f9e', width:2},
    attributes:{name:'文本次序暫定路線'}
  });
  mapState.routeLayer.add(g);
}

async function loadMapData() {
  if (!mapState.view) await initMap();
  const places = await api(`/api/projects/${projectId}/places`);
  mapState.pointLayer.removeAll();
  for (const p of places.filter(p=>p.route_role !== 'mentioned_only' && p.selected_lon != null && p.selected_lat != null)) {
    const g = new mapState.Graphic({
      geometry:{type:'point', longitude:p.selected_lon, latitude:p.selected_lat, spatialReference:{wkid:4326}},
      attributes:{place_id:p.id, route_order:p.route_order, name:p.normalized_name, coord_class:p.coord_class, coord_source:p.coord_source},
      symbol:pointSymbol(p.coord_class),
      popupTemplate:{title:`{route_order}｜{name}`, content:[{type:'fields', fieldInfos:[
        {fieldName:'coord_class',label:'分類'}, {fieldName:'coord_source',label:'坐標來源'}
      ]}]}
    });
    mapState.pointLayer.add(g);
  }
  rebuildRoute();
  if (mapState.pointLayer.graphics.length) {
    await mapState.view.goTo(mapState.pointLayer.graphics.toArray(), {padding:50}).catch(()=>{});
  }
  setStatus($('#mapStatus'), `已顯示 ${mapState.pointLayer.graphics.length} 個有坐標地點；路線按文本次序生成。`);
}

$('#showMapBtn').addEventListener('click', async () => {
  setBusy($('#showMapBtn'), true, '載入中…');
  setStatus($('#mapStatus'), '正在載入 ArcGIS 地圖…');
  try {
    $('#downloadMap').href = `/api/projects/${projectId}/map.geojson?download=true`;
    await loadMapData();
  } catch(err) { setStatus($('#mapStatus'), err.message, true); }
  finally { setBusy($('#showMapBtn'), false, '載入中…'); }
});

$('#deleteMapPointBtn').addEventListener('click', async () => {
  const g = mapState.selectedGraphic;
  if (!g) return;
  if (!confirm(`刪除 ${g.attributes.name}？刪除後前後地點會自動重連。`)) return;
  try {
    await api(`/api/places/${g.attributes.place_id}`, {method:'DELETE'});
    mapState.pointLayer.remove(g);
    mapState.selectedGraphic = null;
    $('#deleteMapPointBtn').disabled = true;
    rebuildRoute();
    setStatus($('#mapStatus'), '✓ 地點已刪除，路線已自動重連。');
  } catch(err) { setStatus($('#mapStatus'), err.message, true); }
});

loadConfig().catch(err => console.error(err));
