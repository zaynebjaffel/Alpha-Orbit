const fallback = [36.7256, 9.1817];
const map = L.map("map", { center: fallback, zoom: 13 });
const street = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
});
const sentinelLayer = L.tileLayer(
  "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2025_3857/default/g/{z}/{y}/{x}.jpg",
  {
    minZoom: 0,
    maxZoom: 19,
    maxNativeZoom: 13,
    attribution:
      'Sentinel-2 cloudless by <a href="https://s2maps.eu" target="_blank" rel="noopener">EOX IT Services GmbH</a> (Contains modified Copernicus Sentinel data 2025)',
  },
);
street.addTo(map);
const layerControl = L.control.layers(
  { "Street · OpenStreetMap": street, "Satellite · Sentinel-2 Cloudless": sentinelLayer },
  null,
  { collapsed: false },
).addTo(map);
const drawnItems = new L.FeatureGroup().addTo(map);
map.addControl(new L.Control.Draw({ position: "topleft", draw: { polygon: { allowIntersection: false, showArea: true }, polyline: false, rectangle: false, circle: false, circlemarker: false, marker: false }, edit: { featureGroup: drawnItems } }));

const $ = (id) => document.getElementById(id);
let latestGeoJSON = null;
let latestSpatialData = null;
let environmentalOverlays = [];
let latestEnvironmentalData = null;
let latestForecastResult = null;
let climatePriority = "water";
let forecastChart = null;
const savedLandsKey = "fieldshift_saved_lands";
const demoLands = [
  { id: "demo-beja", name: "Beja Demo Farm", geojson: { type: "Polygon", coordinates: [[[9.18055,36.72445],[9.18285,36.72445],[9.18285,36.72675],[9.18055,36.72675],[9.18055,36.72445]]] } },
  { id: "demo-iowa", name: "Iowa Demo Farm", geojson: { type: "Polygon", coordinates: [[[-93.63,42.02],[-93.61,42.02],[-93.61,42.04],[-93.63,42.04],[-93.63,42.02]]] } },
  { id: "demo-santiago", name: "Santiago Dry Field", geojson: { type: "Polygon", coordinates: [[[-70.68,-33.46],[-70.66,-33.46],[-70.66,-33.44],[-70.68,-33.44],[-70.68,-33.46]]] } },
];
let savedLands = JSON.parse(localStorage.getItem(savedLandsKey) || "null") || demoLands.map((land) => {
  const feature = turf.feature(land.geojson);
  const centroid = turf.centroid(feature).geometry.coordinates;
  return { ...land, centroid: { lat: centroid[1], lon: centroid[0] }, bbox: turf.bbox(feature), area_ha: turf.area(feature) / 10000, slope_pct: null };
});
let activeLandId = null;
function persistLands() { localStorage.setItem(savedLandsKey, JSON.stringify(savedLands)); }
const userKey = "fieldshift_user";
function currentUser() {
  try { return JSON.parse(localStorage.getItem(userKey) || "null"); } catch { return null; }
}
function selectWeatherLand(id) {
  const land = savedLands.find((item) => item.id === id);
  if (!land) return;
  activeLandId = id;
  updateActiveContext(land);
  populateLandSelects();
  renderWeatherLandContext(land);
  fetchForecast(land);
}
function populateFocusCropSelects(rows) {
  [1, 2, 3].forEach((year) => {
    const select = $(`focus-year-${year}`);
    if (!select) return;
    select.replaceChildren(...rows.slice().sort((a, b) => a.name.localeCompare(b.name)).map((crop) => new Option(crop.name, crop.crop_id)));
  });
}
function renderFocusedPages(data = latestEnvironmentalData) {
  const stage4 = data?.stage4_agronomics || {};
  const stage5 = data?.stage5_climate || {};
  const rotations = (stage4.rotation_strategies || []).filter((rotation) => !rotation.sequence.includes("fallow"));
  const cropNames = Object.fromEntries((stage4.crop_rankings_worst_to_best || []).map((crop) => [crop.crop_id, crop.name]));
  const warming = Object.fromEntries((stage5.rotation_impacts || []).map((row) => [row.name, row]));
  $("rotation-cards").replaceChildren(...rotations.slice(0, 4).map((rotation) => {
    const card = document.createElement("article");
    card.className = "focus-card";
    const impact = warming[rotation.name];
    const sequenceLabels = rotation.sequence.map((id) => id === "fallow" ? "Fallow" : (cropNames[id] || id));
    card.innerHTML = `<h3>${rotation.name}</h3><p>${sequenceLabels.join(" → ")}</p><p><strong>${formatNumber(rotation.mean_suitability_score)}/100</strong> fit · ${formatNumber(rotation.water_use_m3_ha)} m³/ha</p><p>${impact ? `+${formatNumber(impact.additional_water_m3_ha)} m³/ha under +2°C` : "Use the plan that matches your water supply."}</p>`;
    const button = document.createElement("button"); button.className = "button secondary"; button.type = "button"; button.textContent = "Use this plan";
    button.addEventListener("click", () => { showPage("custom-page"); const ids = rotation.sequence; [1, 2, 3].forEach((year, index) => { $(`focus-year-${year}`).value = ids[index]; }); });
    card.append(button); return card;
  }));
  const rankings = stage4.crop_rankings_worst_to_best || [];
  populateFocusCropSelects(rankings);
  const land = savedLands.find((item) => item.id === activeLandId);
  populateWeatherLandPicker();
  renderWeatherLandContext(land);
  renderWeatherQuick(latestForecastResult);
  renderClimatePage(data);
}
function renderClimatePage(data = latestEnvironmentalData) {
  const empty = $("climate-focus-empty");
  const content = $("climate-focus-content");
  const stage5 = data?.stage5_climate;
  if (!empty || !content) return;
  if (!stage5?.baseline || !stage5?.scenario_result || !stage5?.deltas) {
    empty.hidden = false;
    content.hidden = true;
    return;
  }
  empty.hidden = true;
  content.hidden = false;
  const baseline = stage5.baseline;
  const scenario = stage5.scenario_result;
  const deltas = stage5.deltas;
  const best = stage5.crop_impacts?.slice().sort((a, b) => (a.additional_irrigation_m3_ha || 0) - (b.additional_irrigation_m3_ha || 0))[0];
  const recommendations = stage5.recommendations || {};
  $("focus-climate-heat").textContent = `${baseline.heat_stress_days} → ${scenario.heat_stress_days}`;
  $("focus-climate-water").textContent = best ? `+${formatNumber(best.additional_irrigation_m3_ha)} m³/ha` : "—";
  $("focus-climate-gdd").textContent = `${formatNumber(baseline.gdd)} → ${formatNumber(scenario.gdd)}`;
  $("focus-climate-headline").textContent = `Expect ${deltas.heat_stress_days >= 0 ? "+" : ""}${formatNumber(deltas.heat_stress_days)} heat-stress days and ${deltas.et0_mm >= 0 ? "+" : ""}${formatNumber(deltas.et0_mm)} mm ET₀ under +2°C.`;
  $("focus-climate-advice").textContent = best
    ? `${best.name} has the lowest modeled extra irrigation need among the scored crops. This is a planning comparison, not a weather forecast.`
    : "Use lower-water crops, preserve soil cover, and review irrigation timing before the hottest part of the season.";
  const actions = [
    ...(recommendations.crop_recommendations || []).slice(0, 3).map((item) => item.recommended_action),
    ...(recommendations.cycle_recommendations || []).slice(0, 1).map((item) => item.recommendation),
  ];
  $("focus-climate-actions").replaceChildren(...(actions.length ? actions : ["Favor heat-tolerant crops and keep a cover phase in the rotation."]).map((action) => {
    const item = document.createElement("li"); item.textContent = action; return item;
  }));
  renderClimateRotations(data, climatePriority);
}
function renderClimateRotations(data, priority = "water") {
  const container = $("climate-rotation-recommendations");
  if (!container) return;
  const stage4 = data?.stage4_agronomics || {};
  const stage5 = data?.stage5_climate || {};
  const rotations = (stage4.rotation_strategies || []).filter((rotation) => !rotation.sequence.includes("fallow"));
  const impacts = Object.fromEntries((stage5.rotation_impacts || []).map((row) => [row.name, row]));
  const rankings = stage4.crop_rankings_worst_to_best || [];
  const names = Object.fromEntries(rankings.map((crop) => [crop.crop_id, crop.name]));
  if (!rotations.length) {
    container.innerHTML = `<article class="focus-card"><h3>No rotation plan yet</h3><p>Complete a land analysis to receive three priority-based plans.</p></article>`;
    return;
  }
  const sorted = rotations.slice().sort((a, b) => {
    if (priority === "water") return (impacts[a.name]?.additional_water_m3_ha ?? Infinity) - (impacts[b.name]?.additional_water_m3_ha ?? Infinity);
    if (priority === "resilience") return (impacts[b.name]?.mean_yield_factor_change ?? -Infinity) - (impacts[a.name]?.mean_yield_factor_change ?? -Infinity);
    return (b.supplemental_income_usd_ha ?? -Infinity) - (a.supplemental_income_usd_ha ?? -Infinity);
  });
  const labels = { water: "Best for saving water", resilience: "Best for heat resilience", profit: "Best for farm income" };
  container.replaceChildren(...sorted.slice(0, 3).map((rotation, index) => {
    const impact = impacts[rotation.name] || {};
    const card = document.createElement("article");
    card.className = "focus-card climate-rotation-card";
    const sequence = rotation.sequence.map((id) => id === "fallow" ? "Fallow" : names[id] || id).join(" → ");
    const priorityMetric = priority === "water"
      ? `+${formatNumber(impact.additional_water_m3_ha)} m³/ha extra water under +2°C`
      : priority === "resilience"
      ? `${formatNumber((impact.mean_yield_factor_change || 0) * 100)}% modeled yield shift`
      : `$${formatNumber(rotation.supplemental_income_usd_ha)}/ha estimated return`;
    card.innerHTML = `<span class="focus-label">${index === 0 ? labels[priority] : `Alternative ${index}`}</span><h3>${rotation.name}</h3><p>${sequence}</p><p><strong>${priorityMetric}</strong></p><p>${formatNumber(rotation.mean_suitability_score)}/100 baseline fit · ${formatNumber(rotation.mean_plus2c_score)}/100 +2°C fit</p>`;
    const button = document.createElement("button");
    button.className = "button secondary"; button.type = "button"; button.textContent = "Test this rotation";
    button.addEventListener("click", () => {
      showPage("custom-page");
      rotation.sequence.forEach((cropId, index) => { const select = $(`focus-year-${index + 1}`); if (select && cropId !== "fallow") select.value = cropId; });
    });
    card.append(button);
    return card;
  }));
}
async function submitFocusRotation() {
  if (!latestEnvironmentalData) return;
  const crop_ids = [1, 2, 3].map((year) => $(`focus-year-${year}`).value).filter(Boolean);
  if (crop_ids.length !== 3) {
    $("focus-custom-result").textContent = "Load a land analysis before checking a rotation.";
    return;
  }
  const response = await fetch("/api/rotations/custom", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ crop_ids, weather: latestEnvironmentalData.weather_summary, stage3: latestEnvironmentalData.stage3_indicators, soil: latestEnvironmentalData.soil_profile, terrain: latestEnvironmentalData.terrain_slope }) });
  const result = await response.json();
  const names = Object.fromEntries((latestEnvironmentalData.stage4_agronomics?.crop_rankings_worst_to_best || []).map((crop) => [crop.crop_id, crop.name]));
  $("focus-custom-result").textContent = response.ok ? `${result.sequence.map((id) => names[id] || id).join(" → ")} · ${formatNumber(result.water_use_m3_ha)} m³/ha · $${formatNumber(result.supplemental_income_usd_ha)}/ha · nitrogen ${formatNumber(result.net_nitrogen_contribution_kg_ha)} kg/ha` : (result.detail || "This rotation could not be evaluated.");
}
function setUser(user) {
  localStorage.setItem(userKey, JSON.stringify(user));
  $("portal-gate").hidden = true;
  $("farmer-greeting").textContent = `Farmer: ${user.name}`;
}
function showPortalGate() {
  const user = currentUser();
  if (user) return setUser(user);
  $("portal-gate").hidden = false;
  $("farmer-greeting").textContent = "";
}
function renderSavedLandsHub() {
  const grid = $("saved-lands-grid");
  if (!grid) return;
  grid.replaceChildren(...savedLands.map((land) => {
    const card = document.createElement("article");
    card.className = "saved-land-card";
    const title = document.createElement("h3"); title.textContent = land.name;
    const details = document.createElement("p"); details.className = "hint";
    details.textContent = `${formatNumber(land.area_ha, 2)} ha · ${land.centroid.lat.toFixed(3)}°, ${land.centroid.lon.toFixed(3)}°`;
    const badge = document.createElement("span"); badge.className = "health-badge";
    badge.textContent = land.health_report ? "Analysis ready" : "Ready to analyze";
    const actions = document.createElement("div"); actions.className = "land-actions";
    const open = document.createElement("button"); open.className = "button primary"; open.type = "button"; open.textContent = "Open Dashboard";
    open.addEventListener("click", () => { renderActiveLand(land); showPage("health-page"); });
    const remove = document.createElement("button"); remove.className = "button small"; remove.type = "button"; remove.textContent = "Delete";
    remove.addEventListener("click", () => {
      savedLands = savedLands.filter((item) => item.id !== land.id); persistLands(); renderSavedLandsHub();
      if (activeLandId === land.id) { activeLandId = null; showPage("lands-page"); }
    });
    actions.append(open, remove); card.append(title, details, badge, actions); return card;
  }));
}
function showPage(pageId) {
  ["lands-page", "health-page", "climate-page", "rotations-page", "custom-page", "weather-page"].forEach((id) => { const element = $(id); if (element) element.hidden = id !== pageId; });
  document.querySelectorAll(".nav-tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.page === pageId));
  if (pageId === "lands-page") renderSavedLandsHub();
  if (pageId === "health-page") setTimeout(() => map.invalidateSize(), 50);
  if (pageId === "rotations-page" || pageId === "custom-page" || pageId === "weather-page" || pageId === "climate-page") renderFocusedPages();
  if (pageId === "weather-page") {
    const land = savedLands.find((item) => item.id === activeLandId);
    if (land) fetchForecast(land);
  }
}
function updateActiveContext(land) {
  $("active-context").hidden = !land;
  if (!land) return;
  $("context-land-name").textContent = land.name;
  $("context-land-details").textContent = `${formatNumber(land.area_ha, 2)} ha · ${land.centroid.lat.toFixed(3)}°, ${land.centroid.lon.toFixed(3)}°`;
}
function populateLandSelects() {
  ["active-land-select"].forEach((id) => {
    const select = $(id);
    select.replaceChildren(new Option("+ Draw New Land", "__new__"), ...savedLands.map((land) => new Option(land.name, land.id)));
    if (activeLandId) select.value = activeLandId;
  });
  populateWeatherLandPicker();
}
function populateWeatherLandPicker() {
  const select = $("weather-land-select");
  if (!select) return;
  select.replaceChildren(...savedLands.map((land) => new Option(land.name, land.id)));
  if (activeLandId) select.value = activeLandId;
  const pills = $("weather-land-pills");
  if (pills) {
    pills.replaceChildren(...savedLands.slice(0, 5).map((land) => {
      const pill = document.createElement("button");
      pill.type = "button"; pill.className = "land-pill"; pill.textContent = land.name;
      pill.classList.toggle("active", land.id === activeLandId);
      pill.addEventListener("click", () => selectWeatherLand(land.id));
      return pill;
    }));
  }
}
function renderWeatherLandContext(land) {
  const context = $("weather-land-context");
  if (!context) return;
  context.textContent = land
    ? `${land.name} · ${formatNumber(land.area_ha, 2)} ha · centroid ${land.centroid.lat.toFixed(5)}, ${land.centroid.lon.toFixed(5)} · top crop ${land.top_crop?.name || "not analyzed"}`
    : "Choose a parcel to view its area, centroid, and top crop.";
}
function setForecastBadge(count) {
  $("forecast-badge").textContent = count ? String(count) : "Clear";
  $("forecast-badge").classList.toggle("warning", Boolean(count));
}
function restoreSavedEvaluation(land) {
  if (!land) return;
  const report = land.health_report || {};
  const top = land.top_crop;
  if (report.stage3_indicators) updateHealthOverview(report);
  const weather = report.weather_summary || {};
  const soil = report.soil_profile || {};
  const terrain = report.terrain_slope || {};
  $("environmental-results").hidden = false;
  $("weather-temperature").textContent = weather.mean_temperature_c == null ? "—" : `${formatNumber(weather.mean_temperature_c)} °C`;
  $("weather-rainfall").textContent = weather.total_rainfall_mm == null ? "—" : `${formatNumber(weather.total_rainfall_mm)} mm`;
  $("weather-heat").textContent = weather.heat_stress_days_over_35c == null ? "—" : `${weather.heat_stress_days_over_35c} days`;
  $("weather-moisture").textContent = weather.mean_root_zone_moisture == null ? "—" : formatNumber(weather.mean_root_zone_moisture, 3);
  $("soil-summary").textContent = soil.ph == null ? "Saved soil data unavailable" : `pH ${formatNumber(soil.ph, 1)}`;
  $("terrain-summary").textContent = terrain.mean_elevation_m == null ? "Saved terrain unavailable" : `${formatNumber(terrain.mean_elevation_m)} m · ${formatNumber(terrain.mean_slope_percent)}%`;
  $("satellite-summary").textContent = report.optical_indices?.indices?.ndvi?.mean == null ? "Saved results" : `NDVI ${formatNumber(report.optical_indices.indices.ndvi.mean, 3)}`;
  $("stage3-summary").textContent = report.stage3_indicators ? `GDD ${formatNumber(report.stage3_indicators.growing_degree_days?.total)} · ET₀ ${formatNumber(report.stage3_indicators.reference_et0?.total_mm)} mm · moisture ${report.stage3_indicators.moisture_stress?.status || "—"}` : "Saved results";
  $("vulnerability-summary").textContent = report.stage3_indicators ? `Bare ground ${report.stage3_indicators.vulnerability?.bare_ground?.status || "—"} · heat days ${report.stage3_indicators.vulnerability?.heat_stress?.days ?? "—"}` : "Saved results";
  $("crop-summary").textContent = top ? `${top.name} · ${formatNumber(top.suitability_score)}/100` : "Unavailable";
  const bestRotation = report.stage4_agronomics?.recommendation_summary?.best_rotation || land.rotations?.[0];
  $("rotation-summary").textContent = bestRotation?.name || "Unavailable";
  $("recommendation-card").hidden = !top;
  if (top) $("recommendation-headline").textContent = `Saved recommendation: ${top.name}.`;
}
function renderActiveLand(land) {
  if (!land) return;
  activeLandId = land.id;
  updateActiveContext(land);
  latestEnvironmentalData = land.health_report || null;
  populateLandSelects();
  drawnItems.clearLayers();
  const layer = L.geoJSON(land.geojson, { style: { color: "#237a52", weight: 3, fillOpacity: 0.12 } }).getLayers()[0];
  drawnItems.addLayer(layer);
  latestGeoJSON = layer.toGeoJSON();
  latestSpatialData = {
    centroid: { latitude: land.centroid.lat, longitude: land.centroid.lon },
    bbox: land.bbox,
    area: { hectares: land.area_ha },
    geojson: land.geojson,
  };
  map.fitBounds(layer.getBounds(), { padding: [30, 30] });
  $("centroid").textContent = `${land.centroid.lat.toFixed(6)}°, ${land.centroid.lon.toFixed(6)}°`;
  $("bbox").textContent = `[${land.bbox.map((value) => value.toFixed(6)).join(", ")}]`;
  $("area").textContent = `${formatNumber(land.area_ha, 3)} ha`;
  $("fetch-data-button").disabled = false;
  $("save-land-button").disabled = false;
  restoreSavedEvaluation(land);
  fetchForecast(land);
}
function setStatus(message, error = false) { $("app-status").textContent = message; $("app-status").classList.toggle("error", error); }
function formatNumber(value, digits = 2) { return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits }); }
function updateHealthOverview(report) {
  const weather = report.weather_summary || {};
  const soil = report.soil_profile || {};
  const terrain = report.terrain_slope || {};
  const stage3 = report.stage3_indicators || {};
  const moisture = stage3.moisture_stress || {};
  const vulnerability = stage3.vulnerability || {};
  const ndvi = report.optical_indices?.indices?.ndvi?.mean;
  const heatDays = vulnerability.heat_stress?.days ?? weather.heat_stress_days_over_35c ?? 0;
  const moistureScore = moisture.status === "High" ? 25 : moisture.status === "Moderate" ? 60 : moisture.status === "Low" ? 90 : 65;
  const heatScore = Math.max(20, 100 - heatDays * 5);
  const soilScore = soil.ph == null ? 60 : soil.ph >= 5.5 && soil.ph <= 7.8 ? 85 : 55;
  const erosionScore = vulnerability.erosion?.status === "High" ? 30 : vulnerability.erosion?.status === "Moderate" ? 60 : 85;
  const score = Math.round((moistureScore + heatScore + soilScore + erosionScore) / 4);
  const status = score >= 75 ? "Thriving" : score >= 50 ? "Moderate moisture stress" : "High heat/drought risk";
  $("health-score").textContent = `${score}/100`;
  $("health-status").textContent = status;
  $("health-status").className = `health-badge ${score < 50 ? "risk" : score < 75 ? "watch" : ""}`;
  $("health-summary").textContent = score < 50
    ? "This field is under meaningful climate or water pressure. Favor heat-tolerant, water-saving crops and keep the soil covered."
    : score < 75
    ? "The field can produce, but moisture and heat should guide crop choice. Keep a legume or cover phase in the rotation."
    : "The field is currently in a favorable condition. Protect that advantage with balanced rotations and continuous soil cover.";
  const setPillar = (id, text, tone) => { const card = $(id); card.className = `pillar-card ${tone}`; card.querySelector("p").textContent = text; };
  setPillar("water-pillar", moisture.status === "High" ? "Dry root zone — water-saving crops strongly advised." : moisture.status === "Moderate" ? "Some moisture pressure — plan irrigation carefully." : "Root-zone moisture is supporting crop growth.", moisture.status === "High" ? "risk" : moisture.status === "Moderate" ? "watch" : "good");
  setPillar("heat-pillar", heatDays > 10 ? "High summer heat — choose heat-tolerant varieties." : "Heat conditions are manageable for the current plan.", heatDays > 10 ? "risk" : heatDays > 4 ? "watch" : "good");
  setPillar("soil-pillar", soil.ph == null ? "Soil chemistry unavailable — confirm with a field sample." : soil.ph < 5.5 || soil.ph > 7.8 ? `pH ${formatNumber(soil.ph, 1)} — consider soil amendment advice.` : `Balanced pH ${formatNumber(soil.ph, 1)} — legumes can support fertility.`, soil.ph == null || soil.ph < 5.5 || soil.ph > 7.8 ? "watch" : "good");
  setPillar("vigor-pillar", vulnerability.erosion?.status === "High" ? "Sloped ground — keep winter cover and residue." : ndvi == null ? "Canopy data unavailable — keep cover during bare-ground windows." : `Canopy signal ${formatNumber(ndvi, 2)} — maintain soil protection.`, vulnerability.erosion?.status === "High" ? "risk" : "good");
}
function showLiveMeasurement(layer) {
  const feature = layer.toGeoJSON();
  const area = turf.area(feature);
  const perimeter = turf.length(feature, { units: "kilometers" });
  $("area").textContent = `${formatNumber(area)} m² · ${formatNumber(area / 10000, 3)} ha · ${formatNumber(area / 4046.8564224, 3)} acres (live)`;
  $("perimeter").textContent = `${formatNumber(perimeter * 1000)} m · ${formatNumber(perimeter)} km (live)`;
}
async function processLayer(layer) {
  const feature = layer.toGeoJSON();
  latestGeoJSON = feature;
  showLiveMeasurement(layer);
  $("result-badge").textContent = "Processing";
  try {
    const response = await fetch("/api/spatial/process", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(feature) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "The polygon could not be processed.");
    $("centroid").textContent = `${data.centroid.latitude.toFixed(6)}°, ${data.centroid.longitude.toFixed(6)}°`;
    $("area").textContent = `${formatNumber(data.area.square_meters)} m² · ${formatNumber(data.area.hectares, 3)} ha · ${formatNumber(data.area.acres, 3)} acres`;
    $("perimeter").textContent = `${formatNumber(data.perimeter.meters)} m · ${formatNumber(data.perimeter.kilometers)} km`;
    $("bbox").textContent = `[${data.bbox.map((value) => value.toFixed(6)).join(", ")}]`;
    $("geojson-output").textContent = JSON.stringify({ type: "Feature", properties: {}, geometry: data.geojson }, null, 2);
    latestSpatialData = data;
    $("fetch-data-button").disabled = false;
    $("result-badge").textContent = "Ready";
    setStatus(`Processed with ${data.projection}. Centroid is ready for NASA POWER.`);
  } catch (error) {
    $("result-badge").textContent = "Error"; setStatus(error.message, true);
  }
}
map.on(L.Draw.Event.CREATED, (event) => { activeLandId = null; populateLandSelects(); drawnItems.clearLayers(); drawnItems.addLayer(event.layer); $("save-land-button").disabled = false; processLayer(event.layer); });
map.on(L.Draw.Event.EDITED, (event) => event.layers.eachLayer(processLayer));
function clearEnvironmentalOverlays() {
  environmentalOverlays.forEach(({ layer, name }) => {
    map.removeLayer(layer);
    layerControl.removeLayer(layer);
  });
  environmentalOverlays = [];
}
map.on(L.Draw.Event.DELETED, () => { clearEnvironmentalOverlays(); activeLandId = null; latestGeoJSON = null; latestSpatialData = null; $("fetch-data-button").disabled = true; ["centroid", "area", "perimeter", "bbox"].forEach((id) => $(id).textContent = "—"); $("geojson-output").textContent = "Draw a field to generate GeoJSON."; $("result-badge").textContent = "Waiting"; setStatus(""); });
function flyTo(lat, lon, zoom = 13) { map.flyTo([lat, lon], zoom, { duration: 1.2 }); }
function saveCurrentLand() {
  if (!latestGeoJSON || !latestSpatialData) return;
  const name = $("land-name-input").value.trim();
  if (!name) return setStatus("Enter a name before saving this land.", true);
  const feature = turf.feature(latestGeoJSON.geometry);
  const centroid = turf.centroid(feature).geometry.coordinates;
  const land = {
    id: `land-${Date.now()}`,
    name,
    geojson: latestGeoJSON.geometry,
    centroid: { lat: centroid[1], lon: centroid[0] },
    bbox: turf.bbox(feature),
    area_ha: turf.area(feature) / 10000,
    slope_pct: latestEnvironmentalData?.terrain_slope?.mean_slope_percent ?? null,
    health_report: latestEnvironmentalData,
    top_crop: latestEnvironmentalData?.stage4_agronomics?.recommendation_summary?.best_crop || null,
    rotations: latestEnvironmentalData?.stage4_agronomics?.rotation_strategies || [],
  };
  savedLands = savedLands.filter((item) => item.name !== name);
  savedLands.push(land);
  persistLands();
  $("land-name-input").value = "";
  renderActiveLand(land);
  setStatus(`${name} saved locally.`);
}
async function fetchForecast(land) {
  const content = $("weather-content");
  const loading = $("weather-loading");
  const unavailable = $("weather-unavailable");
  if (!land) {
    $("weather-status").textContent = "Choose a parcel to load its forecast.";
    content.hidden = true;
    unavailable.hidden = true;
    return;
  }
  $("weather-status").textContent = `Loading Meteomatics forecast for ${land.name}…`;
  loading.hidden = false;
  content.hidden = true;
  unavailable.hidden = true;
  try {
    const response = await fetch("/api/forecast/alerts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat: land.centroid.lat, lon: land.centroid.lon, land_name: land.name, slope_pct: land.slope_pct, top_crop: land.top_crop }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Forecast unavailable.");
    latestForecastResult = result;
    renderWeatherQuick(result);
    $("weather-status").textContent = result.available
      ? `Forecast ready for ${land.name}.`
      : `Forecast unavailable: ${result.reason || "try again later."}`;
    setForecastBadge(result.available ? (result.alerts || []).filter((alert) => alert.severity !== "optimal").length : 0);
    if (!result.available) {
      unavailable.hidden = false;
      unavailable.textContent = result.reason || "Live forecast credentials are inactive. The page will remain available for another attempt.";
      return;
    }
    content.hidden = false;
    const alerts = result.alerts || [];
    $("weather-alerts").replaceChildren(...alerts.map((alert) => {
      const card = document.createElement("article");
      card.className = `alert-card ${alert.severity}`;
      card.innerHTML = `<strong>${alert.title}</strong><span>${alert.date}</span><p>${alert.message}</p>`;
      return card;
    }));
    if (!alerts.length) {
      const clear = document.createElement("p");
      clear.className = "weather-clear";
      clear.textContent = "All clear for fieldwork. No active warnings in the Meteomatics outlook.";
      $("weather-alerts").replaceChildren(clear);
    }
    const daily = result.daily || [];
    const highs = daily.map((day) => Number(day["t_max_2m_24h:C"])).filter(Number.isFinite);
    const lows = daily.map((day) => Number(day["t_min_2m_24h:C"])).filter(Number.isFinite);
    const rainfall = daily.reduce((sum, day) => sum + (Number(day["precip_24h:mm"]) || 0), 0);
    const moistureValues = daily.map((day) => Number(day["soil_moisture_index_-15cm:idx"])).filter(Number.isFinite);
    const optimalDays = daily.filter((day) => Number(day["precip_24h:mm"]) < 2 && Number(day["soil_moisture_index_-15cm:idx"]) >= 0.25 && Number(day["soil_moisture_index_-15cm:idx"]) <= 0.75).length;
    renderWeatherSummary(alerts, rainfall, highs, lows, optimalDays);
    $("forecast-table").replaceChildren(...daily.map((day) => {
      const row = document.createElement("tr");
      const verdict = weatherVerdict(day);
      row.innerHTML = `<td>${day.date}</td><td>${day["t_max_2m_24h:C"] ?? "—"} / ${day["t_min_2m_24h:C"] ?? "—"}</td><td>${day["precip_24h:mm"] ?? "—"}</td><td>${day["evapotranspiration_24h:mm"] ?? "—"}</td><td>${day["soil_moisture_index_-15cm:idx"] == null ? "—" : `${(Number(day["soil_moisture_index_-15cm:idx"]) * 100).toFixed(0)}%`}</td><td><span class="verdict ${verdict.tone}">${verdict.label}</span></td>`;
      return row;
    }));
    if (forecastChart) forecastChart.destroy();
    if (!window.Chart) throw new Error("Forecast chart library could not be loaded.");
    forecastChart = new Chart($("forecast-chart"), {
      type: "line",
      data: {
        labels: daily.map((day) => day.date),
        datasets: [
          { label: "High °C", data: daily.map((day) => day["t_max_2m_24h:C"]), borderColor: "#b42318", yAxisID: "temperature" },
          { label: "Low °C", data: daily.map((day) => day["t_min_2m_24h:C"]), borderColor: "#237a52", yAxisID: "temperature" },
          { type: "bar", label: "Rain mm", data: daily.map((day) => day["precip_24h:mm"]), backgroundColor: "#8bb9d8", yAxisID: "rain" },
        ],
      },
      options: { responsive: true, scales: { temperature: { position: "left" }, rain: { position: "right" } } },
    });
  } catch (error) {
    unavailable.hidden = false;
    unavailable.textContent = error.message;
    $("weather-status").textContent = `Forecast unavailable: ${error.message}`;
    setForecastBadge(0);
  } finally {
    loading.hidden = true;
  }
}
function renderWeatherSummary(alerts, rainfall, highs, lows, optimalDays) {
  const active = alerts.filter((alert) => alert.severity !== "optimal").length;
  const cards = [
    ["Field alert status", active ? `${active} active warning${active === 1 ? "" : "s"}` : "All clear for fieldwork"],
    ["10-day rainfall", `${formatNumber(rainfall)} mm`],
    ["Temperature range", highs.length && lows.length ? `${formatNumber(Math.max(...highs))}° / ${formatNumber(Math.min(...lows))}°C` : "Unavailable"],
    ["Sowing / spraying days", `${optimalDays} suitable day${optimalDays === 1 ? "" : "s"}`],
  ];
  $("weather-quick").replaceChildren(...cards.map(([label, value], index) => {
    const card = document.createElement("article");
    card.className = `weather-summary-card ${index === 0 && active ? "warning" : index === 0 ? "clear" : ""}`;
    card.innerHTML = `<span>${label}</span><strong>${value}</strong>`;
    return card;
  }));
}
function weatherVerdict(day) {
  const high = Number(day["t_max_2m_24h:C"]);
  const low = Number(day["t_min_2m_24h:C"]);
  const rain = Number(day["precip_24h:mm"]) || 0;
  const moisture = Number(day["soil_moisture_index_-15cm:idx"]);
  if (low <= 2) return { label: "Frost risk", tone: "risk" };
  if (high >= 35) return { label: "Heat watch", tone: "risk" };
  if (rain >= 15 || moisture >= 0.8) return { label: "Too wet", tone: "caution" };
  if (rain < 2 && moisture >= 0.25 && moisture <= 0.75) return { label: "Ideal", tone: "good" };
  return { label: "Monitor", tone: "neutral" };
}
function renderWeatherQuick(result) {
  const container = $("weather-quick");
  if (!container) return;
  if (!result?.available) {
    container.innerHTML = `<article class="focus-card"><h3>Forecast unavailable</h3><p>${result?.reason || "Try again later or use local observations."}</p></article>`;
    return;
  }
  const alerts = (result.alerts || []).filter((alert) => alert.severity !== "optimal");
  const message = alerts[0]?.message || "No urgent weather action is flagged for the next 10 days.";
  container.innerHTML = `<article class="focus-card"><h3>${alerts.length ? `${alerts.length} field alert${alerts.length === 1 ? "" : "s"}` : "No urgent alerts"}</h3><p>${message}</p></article><article class="focus-card"><h3>Farmer action</h3><p>${alerts.length ? "Review the forecast details before irrigation or spraying." : "Keep the current plan and check again after the next weather update."}</p></article>`;
}
let cropExplorerRows = [];
let cropExplorerCategory = "All";
function renderCropExplorer(rows) {
  cropExplorerRows = rows;
  const categories = ["All", "Cereal", "Legume", "Oilseed", "Root/Cash", "Forage", "Cover Crop"];
  $("catalog-count").textContent = `${rows.length} crops`;
  $("crop-filters").replaceChildren(...categories.map((category) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = category === "All" ? "All (30+)" : `${category}s`;
    button.classList.toggle("active", cropExplorerCategory === category);
    button.addEventListener("click", () => { cropExplorerCategory = category; renderCropExplorer(cropExplorerRows); });
    return button;
  }));
  const query = ($("crop-search").value || "").toLowerCase();
  const visible = rows.filter((crop) => (cropExplorerCategory === "All" || crop.category === cropExplorerCategory) &&
    `${crop.name} ${crop.plant_family} ${crop.category}`.toLowerCase().includes(query));
  $("crop-leaderboard").replaceChildren(...visible.map((crop) => {
    const row = document.createElement("label"); row.className = "crop-row";
    const check = document.createElement("input"); check.type = "checkbox"; check.value = crop.crop_id; check.className = "crop-choice";
    const name = document.createElement("span"); name.textContent = `${crop.name} · ${crop.plant_family}`;
    const score = document.createElement("span"); score.textContent = `${formatNumber(crop.suitability_score)}/100`;
    const margin = document.createElement("span"); margin.textContent = `$${formatNumber(crop.net_margin_usd_ha)}/ha`;
    row.append(check, name, score, margin); return row;
  }));
}
function selectedCropIds() {
  return [...document.querySelectorAll(".crop-choice:checked")].map((input) => input.value).slice(0, 5);
}
function renderComparison(ids) {
  const rows = cropExplorerRows.filter((crop) => ids.includes(crop.crop_id));
  if (rows.length < 2) return setStatus("Select between 2 and 5 crops to compare.", true);
  const metrics = [["Plant family", "plant_family"], ["Root depth (m)", "max_root_depth_m"], ["Water demand (mm)", "seasonal_water_demand_mm"], ["Ky", "water_stress_sensitivity_ky"], ["Heat tolerance (°C)", "heat_tolerance_max_c"], ["+2°C score", "plus2c_score"], ["Score shift", "score_delta_plus2c"], ["N impact (kg/ha)", "n_contribution_kg_ha"], ["Yield loss (%)", "expected_yield_loss_pct"], ["Net margin ($/ha)", "net_margin_usd_ha"]];
  const table = document.createElement("table"); table.className = "comparison-table";
  table.innerHTML = `<thead><tr><th>Metric</th>${rows.map((row) => `<th>${row.name}</th>`).join("")}</tr></thead>`;
  const body = document.createElement("tbody");
  metrics.forEach(([label, key]) => {
    const tr = document.createElement("tr");
    const cells = rows.map((row) => {
      const value = row[key];
      return `<td>${value == null ? "—" : typeof value === "number" ? formatNumber(value) : value}</td>`;
    }).join("");
    tr.innerHTML = `<th>${label}</th>${cells}`;
    body.append(tr);
  });
  table.append(body); $("crop-comparison").replaceChildren(table); $("crop-comparison").hidden = false;
}
async function submitCustomRotation() {
  if (!latestEnvironmentalData) return;
  const crop_ids = [1, 2, 3].map((year) => $(`custom-year-${year}`).value);
  const response = await fetch("/api/rotations/custom", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ crop_ids, weather: latestEnvironmentalData.weather_summary, stage3: latestEnvironmentalData.stage3_indicators, soil: latestEnvironmentalData.soil_profile, terrain: latestEnvironmentalData.terrain_slope }) });
  const result = await response.json();
  $("custom-rotation-result").textContent = response.ok ? `${result.name}: ${formatNumber(result.water_use_m3_ha)} m³/ha · $${formatNumber(result.supplemental_income_usd_ha)}/ha · N ${formatNumber(result.net_nitrogen_contribution_kg_ha)} kg/ha` : (result.detail || "Rotation unavailable.");
}
$("crop-search").addEventListener("input", () => renderCropExplorer(cropExplorerRows));
$("compare-crops-button").addEventListener("click", () => renderComparison(selectedCropIds()));
$("compare-extremes-button").addEventListener("click", () => renderComparison([...cropExplorerRows.slice(-3), ...cropExplorerRows.slice(0, 2)].map((row) => row.crop_id)));
$("custom-rotation-button").addEventListener("click", submitCustomRotation);
$("focus-custom-button").addEventListener("click", submitFocusRotation);
document.querySelectorAll(".priority-button").forEach((button) => button.addEventListener("click", () => setClimatePriority(button.dataset.climatePriority)));
function setClimatePriority(priority) {
  climatePriority = priority;
  document.querySelectorAll(".priority-button").forEach((button) => button.classList.toggle("active", button.dataset.climatePriority === priority));
  renderClimateRotations(latestEnvironmentalData, priority);
}
function switchActiveLand(id) {
  if (id === "__new__") {
    activeLandId = null;
    drawnItems.clearLayers();
    $("fetch-data-button").disabled = true;
    map.flyTo(fallback, 13);
    return;
  }
  const land = savedLands.find((item) => item.id === id);
  if (land) renderActiveLand(land);
}
populateLandSelects();
$("farmer-form").addEventListener("submit", (event) => {
  event.preventDefault();
  setUser({ name: $("farmer-name").value.trim(), farm: $("farm-name").value.trim(), region: $("farmer-region").value.trim() });
});
$("demo-login-button").addEventListener("click", () => setUser({ name: "Demo Farmer", farm: "Field Shift Demo Farm", region: "Global demo portfolio" }));
$("logout-button").addEventListener("click", () => {
  localStorage.removeItem(userKey);
  showPortalGate();
});
document.querySelectorAll(".nav-tab").forEach((tab) => tab.addEventListener("click", () => showPage(tab.dataset.page)));
$("context-change-land").addEventListener("click", () => showPage("lands-page"));
$("open-draw-land").addEventListener("click", () => {
  showPage("health-page");
  map.invalidateSize();
  setStatus("Draw a new field polygon on the map, then save it as a land.");
});
document.querySelectorAll("[data-demo-land]").forEach((card) => card.addEventListener("click", () => {
  const demoId = card.dataset.demoLand;
  let land = savedLands.find((item) => item.id === demoId);
  if (!land) {
    const definition = demoLands.find((item) => item.id === demoId);
    if (!definition) return;
    const feature = turf.feature(definition.geojson);
    const centroid = turf.centroid(feature).geometry.coordinates;
    land = { ...definition, centroid: { lat: centroid[1], lon: centroid[0] }, bbox: turf.bbox(feature), area_ha: turf.area(feature) / 10000, slope_pct: null };
    savedLands.push(land); persistLands();
  }
  renderActiveLand(land); showPage("health-page");
}));
$("active-land-select").addEventListener("change", (event) => switchActiveLand(event.target.value));
$("weather-land-select").addEventListener("change", (event) => selectWeatherLand(event.target.value));
$("save-land-button").addEventListener("click", saveCurrentLand);
$("delete-land-button").addEventListener("click", () => {
  if (!activeLandId) return;
  savedLands = savedLands.filter((land) => land.id !== activeLandId);
  persistLands();
  activeLandId = null;
  populateLandSelects();
  clearEnvironmentalOverlays();
  setStatus("Active land deleted.");
});
renderSavedLandsHub();
showPortalGate();
if (savedLands.length) renderActiveLand(savedLands[0]);
showPage("lands-page");
function useLocation() {
  if (!navigator.geolocation) return setStatus("Browser geolocation is unavailable; using Beja, Tunisia.", true);
  setStatus("Requesting your location…");
  navigator.geolocation.getCurrentPosition((position) => { flyTo(position.coords.latitude, position.coords.longitude); setStatus("Map centered on your location."); }, () => { flyTo(...fallback); setStatus("Location permission was denied; showing Beja, Tunisia instead."); }, { enableHighAccuracy: true, timeout: 8000 });
}
async function searchLocation(query) {
  const coordinate = query.split(",").map((part) => Number(part.trim()));
  if (coordinate.length === 2 && coordinate.every(Number.isFinite) && coordinate[0] >= -90 && coordinate[0] <= 90 && coordinate[1] >= -180 && coordinate[1] <= 180) return flyTo(coordinate[0], coordinate[1]);
  $("search-status").textContent = "Searching…";
  try {
    const response = await fetch(`/api/geocode?query=${encodeURIComponent(query)}`); const places = await response.json();
    if (!response.ok || !places.length) throw new Error("No matching place found.");
    flyTo(Number(places[0].lat), Number(places[0].lon)); $("search-status").textContent = places[0].display_name;
  } catch (error) { $("search-status").textContent = error.message; }
}
$("search-form").addEventListener("submit", (event) => { event.preventDefault(); if ($("search-input").value.trim()) searchLocation($("search-input").value.trim()); });
$("locate-button").addEventListener("click", useLocation);
document.querySelectorAll(".preset").forEach((button) => button.addEventListener("click", () => flyTo(Number(button.dataset.lat), Number(button.dataset.lon))));
$("copy-button").addEventListener("click", async () => { if (!latestGeoJSON) return; await navigator.clipboard.writeText(JSON.stringify(latestGeoJSON, null, 2)); $("copy-button").textContent = "Copied"; setTimeout(() => $("copy-button").textContent = "Copy GeoJSON", 1500); });
$("fetch-data-button").addEventListener("click", async () => {
  if (!latestGeoJSON || !latestSpatialData) return;
  const button = $("fetch-data-button");
  button.disabled = true;
  $("data-badge").textContent = "Fetching";
  $("environmental-results").hidden = false;
  $("data-warning").textContent = "Fetching open data sources…";
  try {
    const response = await fetch("/api/data/fetch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        centroid: latestSpatialData.centroid,
        bbox: latestSpatialData.bbox,
        geojson_geometry: latestGeoJSON.geometry,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Environmental data could not be fetched.");
    latestEnvironmentalData = data;
    updateHealthOverview(data);
    const weather = data.weather_summary || {};
    const soil = data.soil_profile || {};
    const terrain = data.terrain_slope || {};
    const modis = data.modis || {};
    const indices = data.optical_indices || {};
    const stage3 = data.stage3_indicators || {};
    const scenes = data.satellite_scene_metadata?.scenes || [];
    clearEnvironmentalOverlays();
    $("weather-temperature").textContent = weather.mean_temperature_c == null ? "—" : `${formatNumber(weather.mean_temperature_c)} °C`;
    $("weather-rainfall").textContent = weather.total_rainfall_mm == null ? "—" : `${formatNumber(weather.total_rainfall_mm)} mm`;
    $("weather-heat").textContent = weather.heat_stress_days_over_35c == null ? "—" : `${weather.heat_stress_days_over_35c} days`;
    $("weather-moisture").textContent = weather.mean_root_zone_moisture == null ? "—" : formatNumber(weather.mean_root_zone_moisture, 3);
    const soilType = [soil.sand_percent == null ? null : `sand ${formatNumber(soil.sand_percent)}%`, soil.clay_percent == null ? null : `clay ${formatNumber(soil.clay_percent)}%`].filter(Boolean).join(", ");
    $("soil-summary").textContent = `${soilType || "Unavailable"} · pH ${soil.ph == null ? "—" : formatNumber(soil.ph, 1)}`;
    $("terrain-summary").textContent = terrain.mean_elevation_m == null ? "Unavailable" : `${formatNumber(terrain.mean_elevation_m)} m · ${formatNumber(terrain.mean_slope_percent)}%`;
    $("satellite-summary").textContent = indices.status === "computed"
      ? `Sentinel-2 · NDVI ${formatNumber(indices.indices.ndvi.mean, 3)}`
      : modis.status === "available"
      ? `MODIS: ${modis.products.join(", ")}`
      : scenes.length ? `${scenes[0].date} · ${scenes[0].sensor}` : "No satellite scene found";
    if (data.satellite_scene_metadata?.cached_array_files?.length && data.cache_key) {
      const bounds = [[latestSpatialData.bbox[1], latestSpatialData.bbox[0]], [latestSpatialData.bbox[3], latestSpatialData.bbox[2]]];
      ["ndvi", "evi", "ndmi", "bsi"].forEach((name) => {
        const layer = L.imageOverlay(`/api/data/layer/${data.cache_key}/${name}.png`, bounds, { opacity: 0.65 });
        layer.addTo(map);
        layerControl.addOverlay(layer, `Layer · ${name.toUpperCase()}`);
        environmentalOverlays.push({ layer, name });
      });
      setStatus("Environmental raster layers loaded. Use the map control to toggle NDVI, EVI, NDMI, and BSI.");
    }
    const gdd = stage3.growing_degree_days || {};
    const et0 = stage3.reference_et0 || {};
    const moisture = stage3.moisture_stress || {};
    const vulnerability = stage3.vulnerability || {};
    const stage4 = data.stage4_agronomics || {};
    const stage5 = data.stage5_climate || {};
    $("stage3-summary").textContent = `GDD ${gdd.total == null ? "—" : formatNumber(gdd.total)} · ET₀ ${et0.total_mm == null ? "—" : `${formatNumber(et0.total_mm)} mm`} · moisture ${moisture.status || "Unavailable"}`;
    $("vulnerability-summary").textContent = `Bare ground ${vulnerability.bare_ground?.status || "—"} · heat days ${vulnerability.heat_stress?.days ?? "—"} · erosion ${vulnerability.erosion?.status || "—"}`;
    const rankings = stage4.crop_rankings_worst_to_best || [];
    const rotations = stage4.rotation_strategies || [];
    const recommendations = stage4.recommendation_summary || {};
    const bestCrop = rankings[rankings.length - 1];
    const bestRotation = [...rotations].sort((a, b) => b.mean_suitability_score - a.mean_suitability_score)[0];
    renderFocusedPages(data);
    renderCropExplorer(rankings);
    [1, 2, 3].forEach((year) => {
      const select = $(`custom-year-${year}`);
      select.replaceChildren(...rankings.slice().sort((a, b) => a.name.localeCompare(b.name)).map((crop) => new Option(crop.name, crop.crop_id)));
    });
    $("crop-summary").textContent = bestCrop ? `${bestCrop.name} · ${formatNumber(bestCrop.suitability_score)} / 100` : "Unavailable";
    $("rotation-summary").textContent = bestRotation ? `${bestRotation.name} · ${formatNumber(bestRotation.mean_suitability_score)} / 100` : "Unavailable";
    $("crop-ranking").textContent = rankings.length
      ? `Crop ranking (worst → best): ${rankings.map((crop) => `${crop.name} (${formatNumber(crop.suitability_score)})`).join(" · ")}`
      : "Crop ranking unavailable.";
    $("rotation-comparison").textContent = rotations.length
      ? rotations.map((rotation) => `${rotation.name}: ${formatNumber(rotation.water_use_m3_ha)} m³/ha · $${formatNumber(rotation.supplemental_income_usd_ha)}/ha`).join(" | ")
      : "Rotation comparison unavailable.";
    const climate = stage5.deltas;
    const climateBase = stage5.baseline;
    const climateResult = stage5.scenario_result;
    const bestClimateCrop = stage5.crop_impacts?.find((crop) => crop.crop_id === recommendations.best_crop?.crop_id);
    const bestClimateRotation = stage5.rotation_impacts?.find((rotation) => rotation.name === recommendations.best_rotation?.name);
    if (climate && climateBase && climateResult) {
      $("climate-card").hidden = false;
      $("climate-headline").textContent = `Under +2°C, heat stress increases by ${climate.heat_stress_days} days and total ET₀ changes by ${formatNumber(climate.et0_mm)} mm.`;
      $("climate-heat").textContent = `${climateBase.heat_stress_days} → ${climateResult.heat_stress_days} days`;
      $("climate-et0").textContent = `${formatNumber(climateBase.total_et0_mm)} → ${formatNumber(climateResult.total_et0_mm)} mm`;
      $("climate-gdd").textContent = `${formatNumber(climateBase.gdd)} → ${formatNumber(climateResult.gdd)} (${climate.gdd >= 0 ? "+" : ""}${formatNumber(climate.gdd)})`;
      $("climate-crop-water").textContent = bestClimateCrop ? `+${formatNumber(bestClimateCrop.additional_irrigation_m3_ha)} m³/ha` : "Unavailable";
      $("climate-rotation-water").textContent = bestClimateRotation ? `+${formatNumber(bestClimateRotation.additional_water_m3_ha)} m³/ha` : "Unavailable";
      $("climate-warning").textContent = bestClimateCrop?.risk === "high"
        ? `${bestClimateCrop.name} has high modeled warming stress; consider a lower-water rotation or adjusted planting dates.`
        : "Scenario assumes precipitation and crop traits remain unchanged; use it for comparative planning, not a weather forecast.";
      const climateRecommendations = stage5.recommendations || {};
      $("climate-crop-recommendations").replaceChildren(
        ...(climateRecommendations.crop_recommendations || []).map((recommendation) => {
          const item = document.createElement("li");
          item.textContent = recommendation.recommended_action;
          return item;
        }),
      );
      $("climate-cycle-recommendations").replaceChildren(
        ...(climateRecommendations.cycle_recommendations || []).map((cycle) => {
          const article = document.createElement("article");
          article.className = "climate-cycle";
          const title = document.createElement("strong");
          title.textContent = `${cycle.name} · ${cycle.climate_fit} fit`;
          const summary = document.createElement("p");
          summary.textContent = cycle.recommendation;
          const schedule = document.createElement("p");
          schedule.textContent = cycle.schedule.map((phase) => `Year ${phase.year}: ${phase.crop} — ${phase.planting_timing}`).join(" | ");
          article.append(title, summary, schedule);
          return article;
        }),
      );
    }
    const recommendedCrop = recommendations.best_crop || bestCrop;
    const recommendedRotation = recommendations.best_rotation || bestRotation;
    if (recommendedCrop && recommendedRotation) {
      $("recommendation-card").hidden = false;
      $("recommendation-headline").textContent = `Start with ${recommendedCrop.name}, then use the ${recommendedRotation.name.toLowerCase()} sequence.`;
      $("recommended-crop").textContent = `${recommendedCrop.name} · ${formatNumber(recommendedCrop.suitability_score)}/100`;
      $("recommended-crop-water").textContent = recommendedCrop.irrigation_need_m3_ha == null ? "Unavailable" : `${formatNumber(recommendedCrop.irrigation_need_m3_ha)} m³/ha`;
      $("recommended-crop-revenue").textContent = recommendedCrop.estimated_revenue_usd_ha == null ? "Unavailable" : `$${formatNumber(recommendedCrop.estimated_revenue_usd_ha)}/ha`;
      $("recommended-rotation").textContent = recommendedRotation.name;
      $("recommended-rotation-water").textContent = `${formatNumber(recommendedRotation.water_use_m3_ha)} m³/ha`;
      $("recommended-rotation-savings").textContent = `$${formatNumber(recommendedRotation.fertilizer_savings_usd_ha)}/ha`;
      $("recommendation-actions").replaceChildren(
        ...(recommendations.actions || []).map((action) => {
          const item = document.createElement("li");
          item.textContent = action;
          return item;
        }),
      );
    }
    $("data-warning").textContent = data.warnings?.length ? data.warnings.join(" ") : "Data sources loaded successfully.";
    $("data-badge").textContent = "Ready";
    if (activeLandId) {
      const land = savedLands.find((item) => item.id === activeLandId);
      if (land) {
        land.health_report = data;
        land.top_crop = data.stage4_agronomics?.recommendation_summary?.best_crop || null;
        land.rotations = data.stage4_agronomics?.rotation_strategies || [];
        land.slope_pct = terrain.mean_slope_percent ?? land.slope_pct;
        persistLands();
      }
    }
  } catch (error) {
    $("data-badge").textContent = "Error";
    $("data-warning").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
// Location is opt-in so the portal opens without browser permission or network errors.
