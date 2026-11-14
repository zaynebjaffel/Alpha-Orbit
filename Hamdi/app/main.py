from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pyproj import CRS, Transformer
from shapely.geometry import shape
from shapely.ops import transform

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
        isinstance(value, (int, float))
        and -180 <= value <= 180
        and -90 <= latitude <= 90
        for longitude, latitude in geometry.exterior.coords
        for value in (longitude,)
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
