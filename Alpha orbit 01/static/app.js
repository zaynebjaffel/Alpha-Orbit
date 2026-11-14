const fallback = [36.7256, 9.1817];
const map = L.map("map", { center: fallback, zoom: 13 });
const street = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
});
const satellite = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
  maxZoom: 19, attribution: "Tiles &copy; Esri",
});
street.addTo(map);
L.control.layers({ "Street · OpenStreetMap": street, "Satellite · Esri": satellite }, null, { collapsed: false }).addTo(map);
const drawnItems = new L.FeatureGroup().addTo(map);
map.addControl(new L.Control.Draw({ position: "topleft", draw: { polygon: { allowIntersection: false, showArea: true }, polyline: false, rectangle: false, circle: false, circlemarker: false, marker: false }, edit: { featureGroup: drawnItems } }));

const $ = (id) => document.getElementById(id);
let latestGeoJSON = null;
window.fieldShift = {
  getLatestGeoJSON: () => latestGeoJSON,
  openGeoJSON: (feature) => {
    if (!feature || feature.type !== "Feature" || !feature.geometry) throw new Error("This farm has no valid saved boundary.");
    drawnItems.clearLayers();
    const savedLayer = L.geoJSON(feature);
    savedLayer.eachLayer((layer) => drawnItems.addLayer(layer));
    latestGeoJSON = feature;
    if (drawnItems.getLayers().length) {
      map.fitBounds(drawnItems.getBounds(), { padding: [24, 24] });
      processLayer(drawnItems.getLayers()[0]);
    }
  },
  invalidateMapSize: () => map.invalidateSize(),
};
function setStatus(message, error = false) { $("app-status").textContent = message; $("app-status").classList.toggle("error", error); }
function formatNumber(value, digits = 2) { return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits }); }
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
    $("result-badge").textContent = "Ready";
    setStatus(`Processed with ${data.projection}. Centroid is ready for NASA POWER.`);
  } catch (error) {
    $("result-badge").textContent = "Error"; setStatus(error.message, true);
  }
}
map.on(L.Draw.Event.CREATED, (event) => { drawnItems.clearLayers(); drawnItems.addLayer(event.layer); processLayer(event.layer); });
map.on(L.Draw.Event.EDITED, (event) => event.layers.eachLayer(processLayer));
map.on(L.Draw.Event.DELETED, () => { latestGeoJSON = null; ["centroid", "area", "perimeter", "bbox"].forEach((id) => $(id).textContent = "—"); $("geojson-output").textContent = "Draw a field to generate GeoJSON."; $("result-badge").textContent = "Waiting"; setStatus(""); });
function flyTo(lat, lon, zoom = 13) { map.flyTo([lat, lon], zoom, { duration: 1.2 }); }
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
useLocation();
