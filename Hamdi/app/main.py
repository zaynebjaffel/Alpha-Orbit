from __future__ import annotations

import os
import struct
import zlib
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pyproj import CRS, Transformer
from shapely.geometry import shape
from shapely.ops import transform

from backend.services.data_fetcher import CACHE_DIR, build_request, fetch_bundle
from backend.services.meteomatics_service import fetch_meteomatics_forecast
from backend.services.crop_scorer import custom_rotation
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = os.getenv(
    "NOMINATIM_USER_AGENT",
    "FieldShift/1.0 (NASA Space Apps prototype; contact project maintainer)",
)

app = FastAPI(title="Field Shift Spatial Intake API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


def _utm_transformers(longitude: float, latitude: float) -> tuple[Transformer, Transformer, int]:
    zone = int((longitude + 180) // 6) + 1
    epsg = 32600 + zone if latitude >= 0 else 32700 + zone
    source = CRS.from_epsg(4326)
    target = CRS.from_epsg(epsg)
    return (
        Transformer.from_crs(source, target, always_xy=True),
        Transformer.from_crs(target, source, always_xy=True),
        epsg,
    )


@app.post("/api/spatial/process")
def process_polygon(geojson: dict[str, Any]) -> dict[str, Any]:
    if geojson.get("type") != "Feature" or not isinstance(geojson.get("geometry"), dict):
        raise HTTPException(422, "Request must be a GeoJSON Feature with a geometry.")

    geometry = shape(geojson["geometry"])
    if geometry.geom_type != "Polygon":
        raise HTTPException(422, "Only Polygon geometries are supported.")
    if geometry.is_empty or not geometry.is_valid or geometry.area == 0:
        raise HTTPException(422, "The polygon must be non-empty, valid, and have area.")
    if not all(
        isinstance(longitude, (int, float))
        and isinstance(latitude, (int, float))
        and -180 <= longitude <= 180
        and -90 <= latitude <= 90
        for longitude, latitude in geometry.exterior.coords
    ):
        raise HTTPException(422, "Coordinates must be WGS84 longitude/latitude values.")

    centroid = geometry.centroid
    to_utm, from_utm, epsg = _utm_transformers(centroid.x, centroid.y)
    projected = transform(to_utm.transform, geometry)
    perimeter_m = projected.length
    area_m2 = projected.area
    bbox = [*geometry.bounds]

    return {
        "centroid": {"latitude": centroid.y, "longitude": centroid.x},
        "bbox": bbox,
        "area": {
            "square_meters": area_m2,
            "hectares": area_m2 / 10_000,
            "acres": area_m2 / 4_046.8564224,
        },
        "perimeter": {"meters": perimeter_m, "kilometers": perimeter_m / 1_000},
        "projection": f"EPSG:{epsg}",
        "geojson": geometry.__geo_interface__,
        "geometry_valid": True,
    }


@app.get("/api/geocode")
async def geocode(query: str = Query(min_length=2, max_length=200)) -> list[dict[str, Any]]:
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en"}
    params = {"q": query, "format": "jsonv2", "limit": 5, "addressdetails": 1}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(NOMINATIM_URL, params=params, headers=headers)
            response.raise_for_status()
    except httpx.HTTPError as error:
        raise HTTPException(502, "Location search is temporarily unavailable.") from error
    return response.json()


@app.post("/api/data/fetch")
async def fetch_environmental_data(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        request = build_request(payload)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return await fetch_bundle(request)


@app.post("/api/rotations/custom")
async def custom_rotation_endpoint(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        crop_ids = payload["crop_ids"]
        if not isinstance(crop_ids, list):
            raise ValueError("crop_ids must be a list.")
        return custom_rotation(
            [str(crop_id) for crop_id in crop_ids],
            weather=payload.get("weather", {}),
            stage3=payload.get("stage3", {}),
            soil=payload.get("soil", {}),
            terrain=payload.get("terrain", {}),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(422, str(error)) from error


@app.post("/api/forecast/alerts")
async def forecast_alerts(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        lat = float(payload["lat"])
        lon = float(payload["lon"])
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(422, "lat and lon are required numeric values.") from error
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise HTTPException(422, "Centroid is outside WGS84 bounds.")
    slope = payload.get("slope_pct")
    try:
        slope = float(slope) if slope is not None else None
    except (TypeError, ValueError) as error:
        raise HTTPException(422, "slope_pct must be numeric.") from error
    return await fetch_meteomatics_forecast(
        lat,
        lon,
        land_name=payload.get("land_name"),
        slope_pct=slope,
        top_crop=payload.get("top_crop"),
    )


def _png_rgba(array: np.ndarray) -> bytes:
    finite = np.isfinite(array)
    if not finite.any():
        raise ValueError("The requested layer has no valid pixels.")
    values = np.nan_to_num(array, nan=0.0)
    minimum, maximum = np.nanpercentile(array, (2, 98))
    if maximum <= minimum:
        maximum = minimum + 1
    normalized = np.clip((values - minimum) / (maximum - minimum), 0, 1)
    alpha = np.where(finite, 210, 0).astype(np.uint8)
    red = (255 * normalized).astype(np.uint8)
    blue = (255 * (1 - normalized)).astype(np.uint8)
    green = (180 * (1 - np.abs(normalized - 0.5) * 2)).astype(np.uint8)
    rgba = np.dstack((red, green, blue, alpha))
    height, width, _ = rgba.shape
    rows = b"".join(b"\x00" + rgba[row].tobytes() for row in range(height))
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


@app.get("/api/data/layer/{cache_key}/{layer}.png")
def environmental_layer(cache_key: str, layer: str) -> Response:
    if len(cache_key) != 64 or any(character not in "0123456789abcdef" for character in cache_key):
        raise HTTPException(422, "Invalid environmental layer cache key.")
    if layer not in {"ndvi", "evi", "ndmi", "bsi"}:
        raise HTTPException(404, "Unknown environmental layer.")
    optical_file = CACHE_DIR / f"{cache_key}-optical.npz"
    if not optical_file.is_file():
        raise HTTPException(404, "Environmental raster cache is unavailable.")
    with np.load(optical_file) as arrays:
        required = {"blue", "red", "nir", "swir1"} - set(arrays.files)
        if required:
            raise HTTPException(422, "Cached raster is missing required optical bands.")
        blue, red, nir, swir1 = (arrays[name].astype(np.float32) for name in ("blue", "red", "nir", "swir1"))
    scale = 10000.0 if float(np.nanpercentile(nir, 99)) > 2 else 1.0
    blue, red, nir, swir1 = (band / scale for band in (blue, red, nir, swir1))
    with np.errstate(divide="ignore", invalid="ignore"):
        values = {
            "ndvi": (nir - red) / (nir + red),
            "evi": 2.5 * (nir - red) / (nir + 6 * red - 7.5 * blue + 1),
            "ndmi": (nir - swir1) / (nir + swir1),
            "bsi": ((swir1 + red) - (nir + blue)) / ((swir1 + red) + (nir + blue)),
        }[layer]
    values[~np.isfinite(values)] = np.nan
    return Response(content=_png_rgba(values), media_type="image/png")
