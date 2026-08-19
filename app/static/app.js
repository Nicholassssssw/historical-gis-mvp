let projectId = null;
let config = {};
let currentPlaces = [];
let selectedPlaceIds = new Set();
let currentGeocodeResults = [];
let selectedCoordinatePlaceIds = new Set();
let currentExtractionPlan = null;
let mapState = { view:null, pointLayer:null, routeLayer:null, sketch:null, selectedGraphic:null, fullscreenCleanup:null, usingFallbackBasemap:false };

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

async function streamApi(url, onEvent) {
  const res = await fetch(url, {method:'POST'});
  if (!res.ok) {
    const text = await res.text();
    let data;
    try { data = text ? JSON.parse(text) : {}; } catch { data = {detail:text}; }
    throw new Error(data.detail || `HTTP ${res.status}`);
  }
  if (!res.body) throw new Error('瀏覽器未能接收串流回應。');

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let result = null;

  const handleLine = line => {
    if (!line.trim()) return;
    let event;
    try { event = JSON.parse(line); }
    catch { throw new Error('收到無法解析的處理進度。'); }
    onEvent?.(event);
    if (event.event === 'error') throw new Error(event.detail || '處理失敗。');
    if (event.event === 'complete') result = event.result;
  };

  while (true) {
    const {value, done} = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), {stream:!done});
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';
    for (const line of lines) handleLine(line);
    if (done) break;
  }
  if (buffer.trim()) handleLine(buffer);
  if (!result) throw new Error('連線已結束，但未收到完成結果。');
  return result;
}

function setStatus(el, msg, error=false) {
  el.textContent = msg || "";
  el.classList.toggle("error", error);
}

function esc(v) {
  return String(v ?? "").replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}

function routeRoleLabel(routeRole) {
  if (routeRole === 'passed_and_mentioned') return '經過及提及';
  if (routeRole === 'mentioned_only') return '提及';
  return '經過';
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

function setGeocodeButtonMode(completed) {
  const button = $('#geocodeBtn');
  const label = completed ? '重新配對座標' : '開始配對座標';
  button.dataset.label = label;
  button.textContent = label;
  button.classList.toggle('button-accent', !completed);
  button.classList.toggle('button-quiet', completed);
}

async function loadConfig() {
  config = await api('/api/config');
  const provider = $('#providerStatus');
  provider.classList.toggle('ready', config.extraction_enabled);
  provider.classList.toggle('warning', !config.extraction_enabled);
  provider.querySelector('span').textContent = config.extraction_enabled
    ? `${config.extraction_model} ready`
    : config.extraction_setup_message;
  $('#extractBtn').textContent = `用 ${config.extraction_provider_label} 抽取地名`;
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
  if ($('#dynasty').value.trim()) fd.append('historical_dynasty', $('#dynasty').value.trim());
  if ($('#historicalYear').value.trim()) fd.append('historical_year_text', $('#historicalYear').value.trim());
  try {
    const p = await api('/api/projects', {method:'POST', body:fd});
    projectId = p.id;
    currentExtractionPlan = p.extraction_plan;
    $('#reviewTextTitle').textContent = $('#title').value.trim()
      ? '等待 DeepSeek 核對'
      : '等待 DeepSeek 搜尋';
    $('#reviewDynasty').textContent = '等待核對';
    $('#reviewYear').textContent = '等待核對';
    setStatus($('#uploadStatus'), `已加入「${p.filename}」。下一步可抽取地名。`);
    $('#wordCount').textContent = p.word_count.toLocaleString();
    $('#pageCount').textContent = p.page_count.toLocaleString();
    $('#pageCountNote').textContent = p.page_count_estimated
      ? `按每頁約 ${p.words_per_estimated_page.toLocaleString()} 字估算`
      : 'PDF 實際頁數';
    $('#readCount').textContent = p.extraction_plan.read_count.toLocaleString();
    $('#wordsPerRead').textContent = p.extraction_plan.words_per_read.toLocaleString();
    $('#estimatedInputTokens').textContent = p.extraction_plan.estimated_input_tokens.toLocaleString();
    $('#documentMetrics').classList.remove('hidden');
    $('#extractBtn').disabled = false;
  } catch (err) { setStatus($('#uploadStatus'), err.message, true); }
  finally { setBusy($('#uploadBtn'), false, '上載中…'); }
});

$('#extractBtn').addEventListener('click', async () => {
  if (!projectId) return;
  const providerLabel = config.extraction_provider_label || 'AI';
  setBusy($('#extractBtn'), true, `${providerLabel} 分析中…`);
  const batchNote = currentExtractionPlan?.read_count > 1
    ? `，正按 ${currentExtractionPlan.read_count} 次依次閱讀；長書可能需要較長時間，請勿重複按掣`
    : '';
  setStatus($('#uploadStatus'), `${providerLabel} 正在辨識路線地名${batchNote}…`);
  try {
    const result = await streamApi(`/api/projects/${projectId}/extract/stream`, event => {
      const readSuffix = event.total_reads > 1
        ? `（第 ${event.current_read}/${event.total_reads} 次閱讀）`
        : '';
      if (event.event === 'stream_started') {
        setStatus(
          $('#uploadStatus'),
          `Vertex AI 已開始回應${readSuffix}\n已接收 0 字符\n模型仍在生成……`,
        );
      } else if (event.event === 'stream_progress') {
        setStatus(
          $('#uploadStatus'),
          `Vertex AI 已開始回應${readSuffix}\n已接收 ${Number(event.received_chars || 0).toLocaleString()} 字符\n模型仍在生成……`,
        );
      } else if (event.event === 'retrying') {
        setStatus(
          $('#uploadStatus'),
          `Vertex AI 串流暫時中斷${readSuffix}\n正在自動重試 ${event.attempt}/${event.max_retries}……`,
        );
      } else if (event.event === 'read_complete' && event.total_reads > 1) {
        setStatus(
          $('#uploadStatus'),
          `已完成第 ${event.current_read}/${event.total_reads} 次閱讀，暫時找到 ${event.places_found} 個地名。\n正在準備下一次閱讀……`,
        );
      }
    });
    if (result.document_context) {
      const context = result.document_context;
      $('#reviewTextTitle').textContent = context.title || '文本內未找到名稱';
      $('#reviewDynasty').textContent = context.historical_dynasty || '—';
      $('#reviewYear').textContent = context.historical_year_text || (context.historical_year ?? '—');
    }
    currentPlaces = result.places;
    selectedPlaceIds.clear();
    renderPlaces(currentPlaces);
    $('#step2').classList.remove('hidden');
    markStep(2);
    setStatus($('#uploadStatus'), `已抽取 ${result.count} 個地名。可逐項勾選，亦可按「經過／提及」整批選擇；第三部分只會配對已勾選資料。`);
    $('#step2').scrollIntoView({behavior:'smooth'});
  } catch (err) {
    setStatus($('#uploadStatus'), err.message, true);
  } finally {
    setBusy($('#extractBtn'), false, `${providerLabel} 分析中…`);
  }
});

function renderPlaces(places) {
  const tbody = $('#placesTable tbody');
  const liveIds = new Set(places.map(place => place.id));
  selectedPlaceIds = new Set([...selectedPlaceIds].filter(id => liveIds.has(id)));
  tbody.innerHTML = places.map(p => `
    <tr data-id="${p.id}" data-route-role="${esc(p.route_role)}">
      <td><span class="readonly-value order-value">${p.route_order}</span></td>
      <td><span class="readonly-value">${esc(p.date_text || '—')}</span></td>
      <td><span class="readonly-value place-value">${esc(p.original_name)}</span></td>
      <td class="role-check-cell"><label class="place-row-selector"><input class="place-row-check" type="checkbox" ${selectedPlaceIds.has(p.id)?'checked':''} aria-label="選擇 ${esc(p.original_name)}（${routeRoleLabel(p.route_role)}）"><span>${routeRoleLabel(p.route_role)}</span></label></td>
      <td><span class="readonly-value sentence-value">${esc(p.sentence || '—')}</span></td>
      <td class="delete-cell"><button type="button" class="row-delete-button" data-action="delete-place" aria-label="刪除 ${esc(p.original_name)}">刪除</button></td>
    </tr>`).join('');
  updatePlaceSelectionState();
}

function placeRows() {
  return $$('#placesTable tbody tr');
}

function rowMatchesSelectedRole(row, selectedRole) {
  const rowRole = row.dataset.routeRole;
  if (rowRole === 'passed_and_mentioned') return true;
  return rowRole === selectedRole;
}

function updatePlaceSelectionState() {
  $$('.role-filter-check').forEach(filterCheck => {
    const matchingRows = placeRows().filter(row => rowMatchesSelectedRole(row, filterCheck.dataset.roleFilter));
    const checkedCount = matchingRows.filter(row => row.querySelector('.place-row-check').checked).length;
    filterCheck.disabled = matchingRows.length === 0;
    filterCheck.checked = matchingRows.length > 0 && checkedCount === matchingRows.length;
    filterCheck.indeterminate = checkedCount > 0 && checkedCount < matchingRows.length;
  });
}

$$('.role-filter-check').forEach(filterCheck => filterCheck.addEventListener('change', event => {
  const selectedRole = event.target.dataset.roleFilter;
  const otherRole = selectedRole === 'passed' ? 'mentioned_only' : 'passed';
  const otherRoleChecked = $(`.role-filter-check[data-role-filter="${otherRole}"]`).checked;
  placeRows().filter(row => rowMatchesSelectedRole(row, selectedRole)).forEach(row => {
    const check = row.querySelector('.place-row-check');
    check.checked = event.target.checked || (otherRoleChecked && rowMatchesSelectedRole(row, otherRole));
    const placeId = Number(row.dataset.id);
    if (check.checked) selectedPlaceIds.add(placeId);
    else selectedPlaceIds.delete(placeId);
  });
  updatePlaceSelectionState();
}));

$('#placesTable tbody').addEventListener('change', event => {
  if (event.target.classList.contains('place-row-check')) {
    const placeId = Number(event.target.closest('tr').dataset.id);
    if (event.target.checked) selectedPlaceIds.add(placeId);
    else selectedPlaceIds.delete(placeId);
    updatePlaceSelectionState();
  }
});

$('#placesTable tbody').addEventListener('click', async event => {
  const button = event.target.closest('[data-action="delete-place"]');
  if (!button) return;
  const row = button.closest('tr');
  const place = currentPlaces.find(item => item.id === Number(row.dataset.id));
  if (!window.confirm(`確定刪除「${place?.original_name || '這個地名'}」？`)) return;
  button.disabled = true;
  try {
    await api(`/api/places/${row.dataset.id}`, {method:'DELETE'});
    selectedPlaceIds.delete(Number(row.dataset.id));
    currentPlaces = currentPlaces.filter(item => item.id !== Number(row.dataset.id));
    renderPlaces(currentPlaces);
    setStatus($('#placesStatus'), `✓ 已刪除「${place?.original_name || '地名'}」。`);
  } catch (err) {
    button.disabled = false;
    setStatus($('#placesStatus'), err.message, true);
  }
});

$('#confirmPlacesBtn').addEventListener('click', async () => {
  if (selectedPlaceIds.size === 0) {
    setStatus($('#placesStatus'), '請至少勾選一個地名。', true);
    return;
  }
  setBusy($('#confirmPlacesBtn'), true, '確認中…');
  try {
    const r = await api(`/api/projects/${projectId}/confirm-places`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({place_ids:[...selectedPlaceIds]}),
    });
    setStatus($('#placesStatus'), `✓ 已選擇 ${r.selected_count} 個地名；第三部分只會配對這些已勾選資料。`);
    $('#confirmPlacesBtn').classList.add('hidden');
    $('#unconfirmPlacesBtn').classList.remove('hidden');
    $('#step3').classList.remove('hidden');
    markStep(3);
    $('#step3').scrollIntoView({behavior:'smooth'});
  } catch (err) {
    setStatus($('#placesStatus'), err.message, true);
  } finally {
    setBusy($('#confirmPlacesBtn'), false, '確認中…');
  }
});

$('#unconfirmPlacesBtn').addEventListener('click', async () => {
  try {
    await api(`/api/projects/${projectId}/unconfirm-places`, {method:'POST'});
    $('#confirmPlacesBtn').classList.remove('hidden');
    $('#unconfirmPlacesBtn').classList.add('hidden');
    $('#step3').classList.add('hidden');
    $('#step4').classList.add('hidden');
    $('#confirmCoordinatesBtn').classList.add('hidden');
    currentGeocodeResults = [];
    selectedCoordinatePlaceIds.clear();
    $('#geoTable tbody').innerHTML = '';
    setGeocodeButtonMode(false);
    markStep(2);
    setStatus($('#placesStatus'), '已取消確認，可以再次選擇地名。');
  } catch (err) { setStatus($('#placesStatus'), err.message, true); }
});

$('#geocodeBtn').addEventListener('click', async () => {
  setBusy($('#geocodeBtn'), true, '正在配對…');
  $('#step4').classList.add('hidden');
  $('#confirmCoordinatesBtn').classList.add('hidden');
  currentGeocodeResults = [];
  selectedCoordinatePlaceIds.clear();
  $('#geoTable tbody').innerHTML = '';
  const progress = {placeName:'', currentPlace:0, totalPlaces:0, pendingDatabases:[]};
  setStatus($('#geoStatus'), '正在準備歷史與現代地名資料庫…');
  try {
    const r = await streamApi(`/api/projects/${projectId}/geocode/stream`, event => {
      if (event.event === 'place_started') {
        progress.placeName = event.place_name || '';
        progress.currentPlace = Number(event.current_place || 0);
        progress.totalPlaces = Number(event.total_places || 0);
        setStatus(
          $('#geoStatus'),
          `正在配對 ${progress.currentPlace}/${progress.totalPlaces}：${progress.placeName}\n正在準備資料庫…`,
        );
      } else if (event.event === 'databases_started') {
        progress.pendingDatabases = [...(event.databases || [])];
        setStatus(
          $('#geoStatus'),
          `正在配對 ${progress.currentPlace}/${progress.totalPlaces}：${progress.placeName}\n目前配對資料庫：${progress.pendingDatabases.join('、')}`,
        );
      } else if (event.event === 'database_complete') {
        progress.pendingDatabases = progress.pendingDatabases.filter(name => name !== event.database);
        const remaining = progress.pendingDatabases.length
          ? progress.pendingDatabases.join('、')
          : '正在整理結果';
        setStatus(
          $('#geoStatus'),
          `正在配對 ${progress.currentPlace}/${progress.totalPlaces}：${progress.placeName}\n剛完成：${event.database}（${Number(event.candidate_count || 0)} 個候選）\n目前配對資料庫：${remaining}`,
        );
      } else if (event.event === 'place_complete' && event.result) {
        currentGeocodeResults.push(event.result);
        renderGeocodes(currentGeocodeResults, {resetSelection:true});
      }
    });
    renderGeocodes(r.results, {resetSelection:true});
    setStatus($('#geoStatus'), `✓ 經緯度配對完成：已顯示全部 ${r.count} 個地點。請勾選並確認要加入地圖的座標。`);
    $('#confirmCoordinatesBtn').classList.remove('hidden');
    updateCoordinateConfirmButton();
    setGeocodeButtonMode(true);
    markStep(3);
  } catch (err) {
    setStatus($('#geoStatus'), err.message, true);
  } finally { setBusy($('#geocodeBtn'), false, '正在配對…'); }
});

function classLabel(c) {
  return c === 'confirmed' ? '確認經緯度' : c === 'possible' ? '有可能' : '資料不足';
}

function updateCoordinateConfirmButton() {
  const button = $('#confirmCoordinatesBtn');
  button.disabled = selectedCoordinatePlaceIds.size === 0;
  button.textContent = `確認座標（${selectedCoordinatePlaceIds.size}）`;
}

function coordinateRows() {
  return $$('#geoTable tbody tr');
}

function updateCoordinateClassSelectionState() {
  $$('.coordinate-class-filter-check').forEach(filterCheck => {
    const matchingRows = coordinateRows().filter(row => row.dataset.coordClass === filterCheck.dataset.coordinateClassFilter);
    const availableRows = matchingRows.filter(row => !row.querySelector('.coordinate-row-check').disabled);
    const checkedCount = availableRows.filter(row => row.querySelector('.coordinate-row-check').checked).length;
    filterCheck.disabled = availableRows.length === 0;
    filterCheck.checked = availableRows.length > 0 && checkedCount === availableRows.length;
    filterCheck.indeterminate = checkedCount > 0 && checkedCount < availableRows.length;
  });
}

$$('.coordinate-class-filter-check').forEach(filterCheck => filterCheck.addEventListener('change', event => {
  const selectedClass = event.target.dataset.coordinateClassFilter;
  coordinateRows().filter(row => row.dataset.coordClass === selectedClass).forEach(row => {
    const check = row.querySelector('.coordinate-row-check');
    if (check.disabled) return;
    check.checked = event.target.checked;
    const placeId = Number(row.dataset.placeId);
    if (check.checked) selectedCoordinatePlaceIds.add(placeId);
    else selectedCoordinatePlaceIds.delete(placeId);
  });
  updateCoordinateClassSelectionState();
  invalidateCoordinateConfirmation();
  updateCoordinateConfirmButton();
}));

function invalidateCoordinateConfirmation() {
  $('#step4').classList.add('hidden');
  markStep(3);
}

function renderGeocodes(results, {resetSelection=false, preserveSelection=false, forceSelectedPlaceId=null}={}) {
  currentGeocodeResults = [...results];
  const liveIds = new Set(results.map(result => Number(result.place_id)));
  if (resetSelection) {
    selectedCoordinatePlaceIds = new Set(
      results
        .filter(result => result.coord_class === 'confirmed' && result.lon != null && result.lat != null)
        .map(result => Number(result.place_id)),
    );
  } else if (preserveSelection) {
    selectedCoordinatePlaceIds = new Set(
      [...selectedCoordinatePlaceIds].filter(placeId => liveIds.has(placeId)),
    );
  }
  if (forceSelectedPlaceId != null) selectedCoordinatePlaceIds.add(Number(forceSelectedPlaceId));

  const tbody = $('#geoTable tbody');
  tbody.innerHTML = results.map(r => {
    const opts = (r.candidates||[]).map(c => `<option value="${c.id}" ${c.source===r.source && Math.abs(c.lon-r.lon)<1e-8 && Math.abs(c.lat-r.lat)<1e-8?'selected':''}>${esc(c.source)}｜${esc(c.candidate_name)}｜${Number(c.score).toFixed(2)}</option>`).join('');
    const hasCoordinates = r.lon != null && r.lat != null;
    const isChecked = selectedCoordinatePlaceIds.has(Number(r.place_id));
    return `<tr data-place-id="${r.place_id}" data-coord-class="${esc(r.coord_class)}">
      <td>${r.route_order}</td><td>${esc(r.name)}</td>
      <td class="role-check-cell coordinate-check-cell"><label class="place-row-selector"><input class="coordinate-row-check" type="checkbox" ${isChecked?'checked':''} ${hasCoordinates?'':'disabled'} aria-label="確認 ${esc(r.name)} 的座標（${classLabel(r.coord_class)}）"><span class="badge ${r.coord_class}">${classLabel(r.coord_class)}</span></label></td>
      <td>${Number(r.score||0).toFixed(2)}</td><td>${esc(r.source||'')}</td>
      <td>${r.lon ?? ''}</td><td>${r.lat ?? ''}</td>
      <td>${opts ? `<select class="candidate-select"><option value="">--選候選--</option>${opts}</select>` : '無候選'}</td>
    </tr>`;
  }).join('');

  $$('.coordinate-row-check').forEach(check => check.addEventListener('change', event => {
    const placeId = Number(event.target.closest('tr').dataset.placeId);
    if (event.target.checked) selectedCoordinatePlaceIds.add(placeId);
    else selectedCoordinatePlaceIds.delete(placeId);
    updateCoordinateClassSelectionState();
    invalidateCoordinateConfirmation();
    updateCoordinateConfirmButton();
  }));

  $$('.candidate-select').forEach(sel => sel.addEventListener('change', async e => {
    if (!e.target.value) return;
    const tr = e.target.closest('tr');
    try {
      await api(`/api/places/${tr.dataset.placeId}/select-candidate/${e.target.value}`, {method:'POST'});
      const places = await api(`/api/projects/${projectId}/places?candidates=true`);
      const converted = places.filter(p=>p.user_selected).map(p=>({place_id:p.id, route_order:p.route_order, name:p.normalized_name, route_role:p.route_role, coord_class:p.coord_class, score:p.coord_score, source:p.coord_source, lon:p.selected_lon, lat:p.selected_lat, coordinate_selected:p.coordinate_selected, candidates:p.candidates}));
      renderGeocodes(converted, {preserveSelection:true, forceSelectedPlaceId:Number(tr.dataset.placeId)});
      invalidateCoordinateConfirmation();
      updateCoordinateConfirmButton();
    } catch(err){ alert(err.message); }
  }));
  updateCoordinateClassSelectionState();
  updateCoordinateConfirmButton();
}

$('#confirmCoordinatesBtn').addEventListener('click', async () => {
  if (!selectedCoordinatePlaceIds.size) {
    setStatus($('#geoStatus'), '請至少勾選一個有經緯度的地名。', true);
    return;
  }
  setBusy($('#confirmCoordinatesBtn'), true, '確認中…');
  try {
    const result = await api(`/api/projects/${projectId}/confirm-coordinates`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({place_ids:[...selectedCoordinatePlaceIds]}),
    });
    setStatus($('#geoStatus'), `✓ 已確認 ${result.selected_count} 個地點座標，第四部分只會使用呢啲地點。`);
    $('#downloadMap').href = `/api/projects/${projectId}/map.geojson?download=true`;
    $('#step4').classList.remove('hidden');
    markStep(4);
    $('#step4').scrollIntoView({behavior:'smooth'});
    await showCurrentMap();
  } catch (err) {
    setStatus($('#geoStatus'), err.message, true);
  } finally {
    setBusy($('#confirmCoordinatesBtn'), false, '確認中…');
    updateCoordinateConfirmButton();
  }
});

async function waitForArcGIS() {
  for (let i=0;i<100;i++) {
    if (window.$arcgis?.import) return;
    await new Promise(r=>setTimeout(r,100));
  }
  throw new Error('ArcGIS SDK未能載入。');
}

function addMapFullscreenControl(view) {
  const mapElement = $('#mapView');
  const requestFullscreen = mapElement.requestFullscreen || mapElement.webkitRequestFullscreen;
  const exitFullscreen = document.exitFullscreen || document.webkitExitFullscreen;
  if (!requestFullscreen || !exitFullscreen) return null;

  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'esri-widget--button esri-widget esri-interactive map-fullscreen-button';
  button.innerHTML = '<span aria-hidden="true">⛶</span>';

  const currentFullscreenElement = () => document.fullscreenElement || document.webkitFullscreenElement;
  const updateButton = () => {
    const isFullscreen = currentFullscreenElement() === mapElement;
    const label = isFullscreen ? '退出全螢幕地圖' : '全螢幕顯示地圖';
    button.setAttribute('aria-label', label);
    button.title = label;
    button.setAttribute('aria-pressed', String(isFullscreen));
  };

  const toggleFullscreen = async () => {
    try {
      if (currentFullscreenElement() === mapElement) {
        await exitFullscreen.call(document);
      } else {
        await requestFullscreen.call(mapElement);
      }
    } catch (error) {
      setStatus($('#mapStatus'), `未能進入全螢幕：${error.message}`, true);
    }
  };

  button.addEventListener('click', toggleFullscreen);
  document.addEventListener('fullscreenchange', updateButton);
  document.addEventListener('webkitfullscreenchange', updateButton);
  updateButton();
  view.ui.add(button, 'top-right');

  return () => {
    button.removeEventListener('click', toggleFullscreen);
    document.removeEventListener('fullscreenchange', updateButton);
    document.removeEventListener('webkitfullscreenchange', updateButton);
  };
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
  const arcgisApiKey = String(config.arcgis_api_key || '').trim();
  esriConfig.apiKey = arcgisApiKey || null;

  if (mapState.view) {
    mapState.fullscreenCleanup?.();
    mapState.view.destroy();
    mapState = {view:null,pointLayer:null,routeLayer:null,sketch:null,selectedGraphic:null,fullscreenCleanup:null,usingFallbackBasemap:false};
  }
  const routeLayer = new GraphicsLayer({title:'文本次序路線'});
  const pointLayer = new GraphicsLayer({title:'行程地點'});
  const map = new Map({basemap: arcgisApiKey ? 'arcgis/topographic' : 'osm', layers:[routeLayer, pointLayer]});
  let usingFallbackBasemap = false;
  try {
    await map.basemap.load();
  } catch (error) {
    console.warn('ArcGIS basemap failed; switching to OpenStreetMap.', error);
    map.basemap = 'osm';
    await map.basemap.load();
    usingFallbackBasemap = true;
  }
  $('#mapView').replaceChildren();
  const view = new MapView({container:'mapView', map, center:[120.2,30.3], zoom:7});
  await view.when();
  view.popupEnabled = false;
  const sketch = new Sketch({view, layer:pointLayer, creationMode:'single', availableCreateTools:[], visibleElements:{settingsMenu:false}});
  view.ui.add(sketch, 'top-right');
  const fullscreenCleanup = addMapFullscreenControl(view);
  mapState = {view, pointLayer, routeLayer, sketch, Graphic, selectedGraphic:null, fullscreenCleanup, usingFallbackBasemap};

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
        g.attributes.longitude = Number(geom.longitude).toFixed(6);
        g.attributes.latitude = Number(geom.latitude).toFixed(6);
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
    if (mapState.selectedGraphic) {
      await view.openPopup({
        features:[mapState.selectedGraphic],
        location:mapState.selectedGraphic.geometry,
        updateLocationEnabled:true,
      }).catch(()=>{});
      setStatus($('#mapStatus'), `已選：${mapState.selectedGraphic.attributes.route_order}｜${mapState.selectedGraphic.attributes.name}。可在彈出視窗查看資料、用 Sketch 移動，或按刪除。`);
    } else {
      await view.closePopup().catch(()=>{});
    }
  });
}

function pointSymbol(status) {
  if (status === 'confirmed') return {type:'simple-marker', style:'circle', color:'#9c3220', size:10, outline:{color:'#fff6df',width:1}};
  if (status === 'possible') return {type:'simple-marker', style:'triangle', color:'#9a7838', size:11, outline:{color:'#fff6df',width:1}};
  return {type:'simple-marker', style:'circle', color:'#766b5c', size:8, outline:{color:'#fff6df',width:1}};
}

function applyPointSymbol(g) { g.symbol = pointSymbol(g.attributes?.coord_class); }

function routeSymbol() {
  const routeColor = [71, 41, 26, 255];
  return {
    type:'cim',
    data:{
      type:'CIMSymbolReference',
      symbol:{
        type:'CIMLineSymbol',
        symbolLayers:[
          {type:'CIMSolidStroke', enable:true, width:1.25, color:routeColor},
          {
            type:'CIMVectorMarker',
            enable:true,
            size:5.5,
            markerPlacement:{
              type:'CIMMarkerPlacementAlongLineSameSize',
              endings:'WithMarkers',
              placementTemplate:[34],
              angleToLine:true
            },
            frame:{xmin:-8, ymin:-5.6, xmax:2, ymax:5.6},
            markerGraphics:[{
              type:'CIMMarkerGraphic',
              geometry:{rings:[[[-8,-5.47],[-8,5.6],[1.96,-0.03],[-8,-5.47]]]},
              symbol:{
                type:'CIMPolygonSymbol',
                symbolLayers:[{type:'CIMSolidFill', enable:true, color:routeColor}]
              }
            }]
          }
        ]
      }
    }
  };
}

function rebuildRoute() {
  if (!mapState.routeLayer || !mapState.pointLayer) return;
  mapState.routeLayer.removeAll();
  const pts = mapState.pointLayer.graphics.toArray().filter(g=>g.geometry?.type==='point').sort((a,b)=>a.attributes.route_order-b.attributes.route_order);
  if (pts.length < 2) return;
  const paths = [pts.map(g=>[g.geometry.longitude, g.geometry.latitude])];
  const g = new mapState.Graphic({
    geometry:{type:'polyline', paths, spatialReference:{wkid:4326}},
    symbol:routeSymbol(),
    attributes:{name:'文本次序暫定路線'}
  });
  mapState.routeLayer.add(g);
}

async function loadMapData() {
  if (!mapState.view) await initMap();
  const places = await api(`/api/projects/${projectId}/places`);
  mapState.pointLayer.removeAll();
  for (const p of places.filter(p=>p.coordinate_selected && p.selected_lon != null && p.selected_lat != null)) {
    const g = new mapState.Graphic({
      geometry:{type:'point', longitude:p.selected_lon, latitude:p.selected_lat, spatialReference:{wkid:4326}},
      attributes:{
        place_id:p.id,
        route_order:p.route_order,
        name:p.normalized_name,
        longitude:Number(p.selected_lon).toFixed(6),
        latitude:Number(p.selected_lat).toFixed(6),
        coord_class:p.coord_class,
        coord_source:p.coord_source || '未提供',
        source_sentence:p.sentence || '—',
      },
      symbol:pointSymbol(p.coord_class),
      popupTemplate:{title:`{route_order}｜{name}`, content:[{type:'fields', fieldInfos:[
        {fieldName:'name',label:'地名'},
        {fieldName:'longitude',label:'經度'},
        {fieldName:'latitude',label:'緯度'},
        {fieldName:'coord_source',label:'經緯度資料來源'},
        {fieldName:'source_sentence',label:'原句'}
      ]}]}
    });
    mapState.pointLayer.add(g);
  }
  rebuildRoute();
  if (mapState.pointLayer.graphics.length) {
    await mapState.view.goTo(mapState.pointLayer.graphics.toArray(), {padding:50}).catch(()=>{});
  }
  const basemapNote = mapState.usingFallbackBasemap ? '；ArcGIS 底圖暫時不可用，已自動改用 OpenStreetMap' : '';
  setStatus($('#mapStatus'), `已顯示 ${mapState.pointLayer.graphics.length} 個有坐標地點；路線按文本次序生成${basemapNote}。`);
}

async function showCurrentMap() {
  setStatus($('#mapStatus'), '正在載入 ArcGIS 地圖…');
  if (!mapState.view) $('#mapView').innerHTML = '<div class="map-placeholder">正在載入 ArcGIS 地圖…</div>';
  try {
    $('#downloadMap').href = `/api/projects/${projectId}/map.geojson?download=true`;
    await loadMapData();
  } catch(err) {
    if (!mapState.view) $('#mapView').innerHTML = '<div class="map-placeholder">地圖未能載入，請返回步驟三重新確認座標再試</div>';
    setStatus($('#mapStatus'), err.message, true);
  }
}

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
