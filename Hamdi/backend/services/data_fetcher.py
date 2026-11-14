from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from .crop_engine import calculate_stage4
from .climate_engine import calculate_stage5
from dotenv import load_dotenv
from pystac_client import Client
from pystac_client.exceptions import APIError, ParametersError
from shapely.geometry import shape

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

LOGGER = logging.getLogger("field_shift.data")
logging.basicConfig(level=logging.INFO, format="%(message)s")

POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
MODIS_URL = "https://modis.ornl.gov/rst/api/v1"
SOILGRIDS_URLS = (
    "https://rest.isric.org/soilgrids/v2.0/properties/query",
    "https://pixelgust.com/soilgrids/v2.0/properties/query",
)
EARTH_SEARCH_URL = "https://earth-search.aws.element84.com/v1"
LPCLOUD_URL = "https://cmr.earthdata.nasa.gov/stac/LPCLOUD"
POWER_PARAMETERS = (
    "T2M,T2M_MAX,T2M_MIN,PRECTOTCORR,ALLSKY_SFC_SW_DWN,"
    "RH2M,WS2M,GWETTOP,GWETROOT"
)
SOIL_PROPERTIES = ("clay", "sand", "silt", "phh2o", "soc", "nitrogen", "wv0033", "wv1500")
USER_AGENT = os.getenv(
    "FIELD_SHIFT_USER_AGENT",
    "FieldShift/1.0 (NASA Space Apps prototype; contact project maintainer)",
)
CACHE_DIR = Path(__file__).resolve().parents[2] / "cache"
STAC_FALLBACK_DAYS = 180
SOIL_CACHE_TTL_SECONDS = 30 * 24 * 3600
CACHE_SCHEMA_VERSION = "module5-climate-4"
MAX_PUBLIC_COG_CANDIDATES = 3
MAX_AUTHENTICATED_COG_CANDIDATES = 1
GDAL_HTTP_TIMEOUT_SECONDS = "15"
GDAL_HTTP_MAX_RETRY = "1"


@dataclass(frozen=True)
class FetchRequest:
    latitude: float
    longitude: float
    bbox: list[float]
    geometry: dict[str, Any]
    start: str
    end: str


def default_date_range() -> tuple[str, str]:
    end = date.today()
    return (end - timedelta(days=365)).isoformat(), end.isoformat()


def normalize_date_range(value: Any) -> tuple[str, str]:
    default_start, default_end = default_date_range()
    if value is None:
        return default_start, default_end
    if isinstance(value, dict):
        start, end = value.get("start", default_start), value.get("end", default_end)
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        start, end = value
    else:
        raise ValueError("date_range must be an object with start/end or a two-item list.")
    try:
        parsed_start, parsed_end = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    except ValueError as error:
        raise ValueError("date_range must use YYYY-MM-DD dates.") from error
    if parsed_start > parsed_end:
        raise ValueError("date_range.start must not be after date_range.end.")
    if (parsed_end - parsed_start).days > 3660:
        raise ValueError("date_range cannot exceed 10 years.")
    return parsed_start.isoformat(), parsed_end.isoformat()


def build_request(payload: dict[str, Any]) -> FetchRequest:
    centroid = payload.get("centroid")
    bbox = payload.get("bbox")
    geometry = payload.get("geojson_geometry")
    if not isinstance(centroid, dict) or not {"latitude", "longitude"} <= centroid.keys():
        raise ValueError("centroid must contain latitude and longitude.")
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise ValueError("bbox must be [min_lon, min_lat, max_lon, max_lat].")
    if not isinstance(geometry, dict):
        raise ValueError("geojson_geometry must be a GeoJSON geometry object.")
    try:
        latitude, longitude = float(centroid["latitude"]), float(centroid["longitude"])
        numeric_bbox = [float(value) for value in bbox]
    except (TypeError, ValueError) as error:
        raise ValueError("centroid and bbox must contain numeric values.") from error
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("centroid is outside WGS84 bounds.")
    if numeric_bbox[0] > numeric_bbox[2] or numeric_bbox[1] > numeric_bbox[3]:
        raise ValueError("bbox bounds are not ordered.")
    polygon = shape(geometry)
    if polygon.geom_type != "Polygon" or polygon.is_empty or not polygon.is_valid:
        raise ValueError("geojson_geometry must be a valid, non-empty Polygon.")
    start, end = normalize_date_range(payload.get("date_range"))
    return FetchRequest(latitude, longitude, numeric_bbox, geometry, start, end)


def cache_key(request: FetchRequest) -> str:
    content = json.dumps(
        {
            "schema": CACHE_SCHEMA_VERSION,
            "geometry": request.geometry,
            "bbox": request.bbox,
            "centroid": [request.latitude, request.longitude],
            "start": request.start,
            "end": request.end,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(content).hexdigest()


def _debug(message: str) -> None:
    LOGGER.info("[DATA DEBUG] %s", message)


def _soil_cache_key(request: FetchRequest) -> str:
    return hashlib.sha256(
        f"{request.latitude:.6f},{request.longitude:.6f}".encode()
    ).hexdigest()


def _modis_date(value: str) -> str:
    parsed = date.fromisoformat(value)
    return f"A{parsed.year}{parsed.timetuple().tm_yday:03d}"


def _modis_cache_key(request: FetchRequest) -> str:
    return hashlib.sha256(
        f"{request.latitude:.6f},{request.longitude:.6f},{request.start},{request.end}".encode()
    ).hexdigest()


def scale_modis_value(product: str, band: str, value: Any) -> float | None:
    """Normalize MODIS integer science values and discard provider fill values."""
    if not isinstance(value, (int, float)) or value in {-3000, -9999}:
        return None
    scale = 0.0001 if product == "MOD13Q1" or band.startswith("sur_refl_") else 1.0
    return float(value) * scale


async def _json_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | list[tuple[str, Any]],
    headers: dict[str, str] | None = None,
) -> Any:
    response = await client.get(url, params=params, headers=headers)
    response.raise_for_status()
    return response.json()


async def fetch_nasa_power(client: httpx.AsyncClient, request: FetchRequest) -> dict[str, Any]:
    params = {
        "parameters": POWER_PARAMETERS,
        "community": "AG",
        "longitude": request.longitude,
        "latitude": request.latitude,
        "start": request.start.replace("-", ""),
        "end": request.end.replace("-", ""),
        "format": "JSON",
    }
    _debug(
        f"NASA POWER point=({request.latitude},{request.longitude}) "
        f"bbox={request.bbox} datetime={params['start']}/{params['end']}"
    )
    payload = await _json_get(client, POWER_URL, params=params, headers={"User-Agent": USER_AGENT})
    properties = payload.get("properties", {})
    parameter_data = properties.get("parameter", {})
    dates = sorted(
        set().union(*(values.keys() for values in parameter_data.values() if isinstance(values, dict)))
    )
    daily: list[dict[str, Any]] = []
    for day in dates:
        record: dict[str, Any] = {"date": f"{day[:4]}-{day[4:6]}-{day[6:]}"}
        for name, values in parameter_data.items():
            value = values.get(day)
            if isinstance(value, (int, float)) and value != -999:
                record[name] = value
        if len(record) > 1:
            daily.append(record)

    def values(name: str) -> list[float]:
        return [float(item[name]) for item in daily if name in item]

    temperatures = values("T2M")
    maximums = values("T2M_MAX")
    rainfall = values("PRECTOTCORR")
    root_wetness = values("GWETROOT")
    return {
        "weather_summary": {
            "mean_temperature_c": np.mean(temperatures).item() if temperatures else None,
            "max_temperature_c": max(maximums) if maximums else None,
            "total_rainfall_mm": np.sum(rainfall).item() if rainfall else None,
            "heat_stress_days_over_35c": sum(value > 35 for value in maximums),
            "mean_root_zone_moisture": np.mean(root_wetness).item() if root_wetness else None,
            "observations": len(daily),
        },
        "daily": daily,
        "date_range": {"start": request.start, "end": request.end},
        "source": "NASA POWER",
    }


async def fetch_modis(client: httpx.AsyncClient, request: FetchRequest) -> dict[str, Any]:
    """Fetch small, pre-composited MODIS subsets; index math belongs to Stage 3."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"modis-{_modis_cache_key(request)}.json"
    if cache_file.exists() and time.time() - cache_file.stat().st_mtime < 7 * 24 * 3600:
        _debug(f"MODIS cache hit: {cache_file.name}")
        return json.loads(cache_file.read_text())

    products = {
        "MOD13Q1": ("250m_16_days_NDVI", "250m_16_days_EVI"),
        "MOD09A1": ("sur_refl_b02", "sur_refl_b06"),
    }
    subsets: dict[str, Any] = {}
    errors: dict[str, str] = {}
    common_params = {
        "latitude": request.latitude,
        "longitude": request.longitude,
        "startDate": _modis_date(request.start),
        "endDate": _modis_date(request.end),
        "kmAboveBelow": 1,
        "kmLeftRight": 1,
    }
    for product, bands in products.items():
        params = [*common_params.items(), *[("band", band) for band in bands]]
        for attempt in range(2):
            try:
                subsets[product] = await _json_get(
                    client,
                    f"{MODIS_URL}/{product}/subset",
                    params=params,
                    headers={"User-Agent": USER_AGENT},
                )
                _debug(f"MODIS product={product} status=ok")
                break
            except httpx.HTTPStatusError as error:
                if error.response.status_code >= 500 and attempt == 0:
                    await asyncio.sleep(0.5)
                    continue
                errors[product] = str(error)
                _debug(f"MODIS product={product} unavailable: {error}")
                break
            except (httpx.HTTPError, ValueError) as error:
                errors[product] = str(error)
                _debug(f"MODIS product={product} unavailable: {error}")
                break

    result = {
        "status": "available" if subsets else "unavailable",
        "products": list(subsets),
        "subsets": subsets,
        "date_range": {"start": request.start, "end": request.end},
        "source": "NASA ORNL DAAC MODIS REST",
        "scale_note": "MODIS reflectance and index values retain provider scale metadata; no index math performed.",
        "errors": errors,
    }
    cache_file.write_text(json.dumps(result))
    return result


def _soil_value(payload: dict[str, Any], property_name: str) -> float | None:
    for layer in payload.get("properties", {}).get("layers", []):
        if layer.get("name") != property_name:
            continue
        depths = layer.get("depths", [])
        if not depths:
            return None
        values = depths[0].get("values", {})
        value = values.get("mean")
        return float(value) if isinstance(value, (int, float)) else None
    return None


async def fetch_soilgrids(client: httpx.AsyncClient, request: FetchRequest) -> dict[str, Any]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"soil-{_soil_cache_key(request)}.json"
    if cache_file.exists() and time.time() - cache_file.stat().st_mtime < SOIL_CACHE_TTL_SECONDS:
        _debug(f"SoilGrids cache hit: {cache_file.name}")
        return json.loads(cache_file.read_text())

    property_params: list[tuple[str, Any]] = [
        ("lon", request.longitude),
        ("lat", request.latitude),
        *[("property", value) for value in SOIL_PROPERTIES],
        ("depth", "0-5cm"),
        ("value", "mean"),
    ]
    last_error: httpx.HTTPError | None = None
    used_url = SOILGRIDS_URLS[0]
    for url in SOILGRIDS_URLS:
        try:
            payload = await _json_get(client, url, params=property_params, headers={"User-Agent": USER_AGENT})
            used_url = url
            break
        except httpx.HTTPStatusError as error:
            last_error = error
            _debug(f"SoilGrids endpoint failed status={error.response.status_code} url={url}")
        except httpx.HTTPError as error:
            last_error = error
    else:
        _debug(f"SoilGrids unavailable after fallback: {last_error}")
        result = {
            "soil_profile": {
                "clay_percent": None,
                "sand_percent": None,
                "silt_percent": None,
                "ph": None,
                "organic_carbon_g_per_kg": None,
                "nitrogen_g_per_kg": None,
                "available_water_capacity": {"33_kpa": None, "1500_kpa": None},
            },
            "source": "SoilGrids unavailable; no default soil values applied",
            "warning": "SoilGrids services were unavailable.",
        }
        cache_file.write_text(json.dumps(result))
        return result

    raw = {name: _soil_value(payload, name) for name in SOIL_PROPERTIES}
    result = {
        "soil_profile": {
            "clay_percent": raw["clay"] / 10 if raw["clay"] is not None else None,
            "sand_percent": raw["sand"] / 10 if raw["sand"] is not None else None,
            "silt_percent": raw["silt"] / 10 if raw["silt"] is not None else None,
            "ph": raw["phh2o"] / 10 if raw["phh2o"] is not None else None,
            "organic_carbon_g_per_kg": raw["soc"] / 10 if raw["soc"] is not None else None,
            "nitrogen_g_per_kg": raw["nitrogen"] / 100 if raw["nitrogen"] is not None else None,
            "available_water_capacity": {
                "33_kpa": raw["wv0033"] / 10 if raw["wv0033"] is not None else None,
                "1500_kpa": raw["wv1500"] / 10 if raw["wv1500"] is not None else None,
            },
        },
        "source": used_url,
    }
    cache_file.write_text(json.dumps(result))
    return result


def _stac_search(
    url: str,
    collections: list[str],
    bbox: list[float],
    start: str | None,
    end: str | None,
    token: str | None,
    query: dict[str, Any] | None = None,
) -> list[Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    catalog = Client.open(url, headers=headers)
    search_kwargs: dict[str, Any] = {
        "collections": collections,
        "bbox": bbox,
    }
    if query:
        search_kwargs["query"] = query
    if start and end:
        search_kwargs["datetime"] = f"{start}/{end}"
    search = catalog.search(**search_kwargs)
    items = list(search.items())
    _debug(
        f"STAC search collections={collections} bbox={bbox} "
        f"datetime={search_kwargs.get('datetime', 'none')} items={len(items)}"
    )
    return items


def _asset_url(item: Any, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        asset = item.assets.get(key)
        if asset and asset.href:
            return asset.href
    return None


def _item_date(item: Any) -> str:
    value = item.datetime or item.properties.get("datetime", "")
    return value.isoformat()[:10] if hasattr(value, "isoformat") else str(value)[:10]


def _raster_metadata(items: list[Any], token: str | None) -> dict[str, Any]:
    candidate_limit = (
        MAX_AUTHENTICATED_COG_CANDIDATES if token else MAX_PUBLIC_COG_CANDIDATES
    )
    selected_items = sorted(items, key=_item_date, reverse=True)[:candidate_limit]
    _debug(f"raster_candidates={len(selected_items)} limit={candidate_limit}")
    if not selected_items:
        return {"scenes": [], "bands": [], "cached_array_files": []}
    latest = selected_items[0]
    band_aliases = {
        "blue": ("B02", "blue"),
        "green": ("B03", "green"),
        "red": ("B04", "red"),
        "nir": ("B05", "B08", "nir"),
        "swir1": ("B11", "B06", "swir16"),
        "cloud_mask": ("Fmask", "SCL", "scl"),
    }
    bands = {name: _asset_url(latest, keys) for name, keys in band_aliases.items()}
    return {
        "scenes": [
            {
                "id": item.id,
                "date": _item_date(item),
                "sensor": item.collection_id or "unknown",
            }
            for item in selected_items[:10]
        ],
        "bands": {name: url for name, url in bands.items() if url},
        "cached_array_files": [],
        "access": "NASA Earthdata token configured" if token else "Public Earth Search",
    }


def _clip_rasters_sync(
    items: list[Any], geometry: dict[str, Any], key: str, token: str | None
) -> tuple[list[str], dict[str, str]]:
    if not items:
        return [], {}


    import rioxarray
    import rasterio
    from rioxarray.exceptions import NoDataInBounds
    from shapely.geometry import mapping

    polygon = shape(geometry)
    last_error: Exception | None = None
    for item in items:
        bands = {
            name: _asset_url(item, aliases)
            for name, aliases in {
                "blue": ("B02", "blue"),
                "green": ("B03", "green"),
                "red": ("B04", "red"),
                "nir": ("B05", "B08", "nir"),
                "swir1": ("B11", "B06", "swir16"),
                "cloud_mask": ("Fmask", "SCL", "scl"),
            }.items()
        }
        bands = {name: url for name, url in bands.items() if url}
        _debug(f"selected COG URLs scene={item.id}: {bands}")
        arrays: dict[str, np.ndarray] = {}
        reference = None
        try:
            env_options = {
                "GDAL_HTTP_HEADERS": f"Authorization: Bearer {token}",
                "GDAL_HTTP_TIMEOUT": GDAL_HTTP_TIMEOUT_SECONDS,
                "GDAL_HTTP_MAX_RETRY": GDAL_HTTP_MAX_RETRY,
                "GDAL_HTTP_RETRY_DELAY": "1",
            } if token else {
                "AWS_NO_SIGN_REQUEST": "YES",
                "GDAL_HTTP_TIMEOUT": GDAL_HTTP_TIMEOUT_SECONDS,
                "GDAL_HTTP_MAX_RETRY": GDAL_HTTP_MAX_RETRY,
                "GDAL_HTTP_RETRY_DELAY": "1",
            }
            with rasterio.Env(**env_options):
                for name, url in bands.items():
                    raster = rioxarray.open_rasterio(url, masked=True, chunks=None)
                    clipped = raster.rio.clip(
                        [mapping(polygon)], crs="EPSG:4326", drop=True, from_disk=True
                    )
                    if reference is None:
                        reference = clipped
                    else:
                        clipped = clipped.rio.reproject_match(reference)
                    arrays[name] = np.asarray(clipped.squeeze().values, dtype=np.float32)
                    raster.close()
            output = CACHE_DIR / f"{key}-optical.npz"
            np.savez_compressed(output, **arrays)
            return [str(output)], bands
        except (NoDataInBounds, rasterio.errors.RasterioIOError, ValueError) as error:
            last_error = error
            _debug(f"scene={item.id} COG clip failed; trying next scene: {error}")
            if token and isinstance(error, rasterio.errors.RasterioIOError) and "404" in str(error):
                raise
    if last_error:
        raise last_error
    return [], {}


def _calculate_optical_indices(cached_files: list[str]) -> dict[str, Any]:
    if not cached_files:
        return {"status": "unavailable", "indices": {}, "error": "No optical raster was cached."}
    path = Path(cached_files[0])
    with np.load(path) as arrays:
        required = {"blue", "red", "nir", "swir1"} - set(arrays.files)
        if required:
            return {
                "status": "unavailable",
                "indices": {},
                "error": f"Missing optical bands: {', '.join(sorted(required))}.",
            }
        blue, red, nir, swir1 = (
            np.asarray(arrays[name], dtype=np.float32)
            for name in ("blue", "red", "nir", "swir1")
        )
    scale = 10000.0 if float(np.nanpercentile(nir, 99)) > 2 else 1.0
    blue, red, nir, swir1 = (band / scale for band in (blue, red, nir, swir1))
    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi = (nir - red) / (nir + red)
        evi = 2.5 * (nir - red) / (nir + 6 * red - 7.5 * blue + 1)
        ndmi = (nir - swir1) / (nir + swir1)
        bsi = ((swir1 + red) - (nir + blue)) / ((swir1 + red) + (nir + blue))
    for array in (ndvi, evi, ndmi, bsi):
        array[~np.isfinite(array)] = np.nan
    return {
        "status": "computed",
        "indices": {
            name: {
                "mean": float(np.nanmean(array)),
                "minimum": float(np.nanmin(array)),
                "maximum": float(np.nanmax(array)),
            }
            for name, array in (("ndvi", ndvi), ("evi", evi), ("ndmi", ndmi), ("bsi", bsi))
        },
        "bare_ground_fraction": float(np.mean(bsi > 0.2)),
        "cached_array_files": cached_files,
        "reflectance_scale": scale,
    }


def _daily_value(record: dict[str, Any], name: str) -> float | None:
    value = record.get(name)
    return float(value) if isinstance(value, (int, float)) and np.isfinite(value) else None


def _reference_et0(record: dict[str, Any], latitude: float, elevation_m: float = 0.0) -> float | None:
    tmean = _daily_value(record, "T2M")
    tmax = _daily_value(record, "T2M_MAX")
    tmin = _daily_value(record, "T2M_MIN")
    radiation = _daily_value(record, "ALLSKY_SFC_SW_DWN")
    humidity = _daily_value(record, "RH2M")
    wind = _daily_value(record, "WS2M")
    if None in (tmean, tmax, tmin, radiation, humidity, wind):
        return None
    day = date.fromisoformat(str(record["date"])).timetuple().tm_yday
    latitude_radians = np.deg2rad(latitude)
    inverse_distance = 1 + 0.033 * np.cos(2 * np.pi * day / 365)
    solar_declination = 0.409 * np.sin(2 * np.pi * day / 365 - 1.39)
    sunset_angle = np.arccos(
        np.clip(-np.tan(latitude_radians) * np.tan(solar_declination), -1, 1)
    )
    ra = (24 * 60 / np.pi) * 0.0820 * inverse_distance * (
        sunset_angle * np.sin(latitude_radians) * np.sin(solar_declination)
        + np.cos(latitude_radians) * np.cos(solar_declination) * np.sin(sunset_angle)
    )
    rso = (0.75 + 2e-5 * max(elevation_m, 0)) * ra
    es_max = 0.6108 * np.exp(17.27 * tmax / (tmax + 237.3))
    es_min = 0.6108 * np.exp(17.27 * tmin / (tmin + 237.3))
    es = (es_max + es_min) / 2
    ea = max(0.0, es * humidity / 100)
    delta = 4098 * (0.6108 * np.exp(17.27 * tmean / (tmean + 237.3))) / (tmean + 237.3) ** 2
    pressure = 101.3 * ((293 - 0.0065 * max(elevation_m, 0)) / 293) ** 5.26
    gamma = 0.000665 * pressure
    net_shortwave = (1 - 0.23) * radiation
    ratio = min(radiation / rso, 1.0) if rso > 0 else 0.0
    net_longwave = 4.903e-9 * (
        ((tmax + 273.16) ** 4 + (tmin + 273.16) ** 4) / 2
    ) * (0.34 - 0.14 * np.sqrt(ea)) * (1.35 * ratio - 0.35)
    net_radiation = max(0.0, net_shortwave - net_longwave)
    et0 = (
        0.408 * delta * net_radiation
        + gamma * (900 / (tmean + 273)) * wind * max(es - ea, 0)
    ) / (delta + gamma * (1 + 0.34 * wind))
    return float(max(et0, 0.0))


def _calculate_stage3(
    daily: list[dict[str, Any]],
    optical: dict[str, Any],
    latitude: float,
    elevation_m: float | None,
    slope_percent: float | None,
) -> dict[str, Any]:
    elevation = elevation_m or 0.0
    gdd_base = 10.0
    gdd_total = 0.0
    et0_values: list[float] = []
    heat_days: list[str] = []
    heavy_rain_days: list[str] = []
    moisture_values: list[float] = []
    daily_indicators: list[dict[str, Any]] = []
    for record in daily:
        tmean = _daily_value(record, "T2M")
        tmax = _daily_value(record, "T2M_MAX")
        tmin = _daily_value(record, "T2M_MIN")
        rain = _daily_value(record, "PRECTOTCORR")
        wetness = _daily_value(record, "GWETROOT")
        if tmax is not None and tmin is not None:
            gdd = max(0.0, min((tmax + tmin) / 2, 35.0) - gdd_base)
            gdd_total += gdd
        else:
            gdd = None
        et0 = _reference_et0(record, latitude, elevation)
        if et0 is not None:
            et0_values.append(et0)
        day = str(record["date"])
        if tmax is not None and tmax > 35:
            heat_days.append(day)
        if rain is not None and rain >= 20:
            heavy_rain_days.append(day)
        if wetness is not None:
            moisture_values.append(wetness)
        daily_indicators.append({"date": day, "gdd_base_10": gdd, "et0_mm": et0})
    ndmi_mean = optical.get("indices", {}).get("ndmi", {}).get("mean")
    wetness_mean = float(np.mean(moisture_values)) if moisture_values else None
    moisture_stress = (
        "High" if (wetness_mean is not None and wetness_mean <= 0.3 + 1e-9) or (ndmi_mean is not None and ndmi_mean < -0.2)
        else "Moderate" if (wetness_mean is not None and wetness_mean < 0.5) or (ndmi_mean is not None and ndmi_mean < 0)
        else "Low" if wetness_mean is not None or ndmi_mean is not None else "Unavailable"
    )
    bare_fraction = optical.get("bare_ground_fraction")
    vulnerability = {
        "bare_ground": {
            "status": "High" if bare_fraction is not None and bare_fraction >= 0.25 else "Low" if bare_fraction is not None else "Unavailable",
            "fraction": bare_fraction,
            "threshold": 0.2,
        },
        "heat_stress": {"days": len(heat_days), "window_dates": heat_days},
        "heavy_rain": {"days": len(heavy_rain_days), "window_dates": heavy_rain_days},
        "erosion": {
            "status": (
                "High" if slope_percent is not None and slope_percent >= 15 and heavy_rain_days
                else "Moderate" if slope_percent is not None and slope_percent >= 5 and heavy_rain_days
                else "Low" if slope_percent is not None
                else "Unavailable"
            ),
            "slope_percent": slope_percent,
            "window_dates": heavy_rain_days if slope_percent is not None and slope_percent >= 5 else [],
        },
    }
    return {
        "growing_degree_days": {"base_temperature_c": gdd_base, "total": round(gdd_total, 2)},
        "reference_et0": {
            "method": "FAO-56 Penman-Monteith",
            "total_mm": round(float(np.sum(et0_values)), 2) if et0_values else None,
            "mean_daily_mm": round(float(np.mean(et0_values)), 2) if et0_values else None,
            "observations": len(et0_values),
        },
        "moisture_stress": {
            "status": moisture_stress,
            "mean_root_zone_wetness": wetness_mean,
            "mean_ndmi": ndmi_mean,
        },
        "vulnerability": vulnerability,
        "daily": daily_indicators,
    }


def _clip_dem_sync(
    item: Any, geometry: dict[str, Any], key: str, token: str | None = None
) -> dict[str, Any]:
    import rioxarray
    import rasterio
    from shapely.geometry import mapping

    href = _asset_url(item, ("data", "dem", "elevation"))
    if not href:
        raise ValueError("Copernicus DEM scene has no readable data asset.")
    env_options = {
        "GDAL_HTTP_HEADERS": f"Authorization: Bearer {token}",
        "AWS_NO_SIGN_REQUEST": "YES",
    } if token else {"AWS_NO_SIGN_REQUEST": "YES"}
    with rasterio.Env(**env_options):
        raster = rioxarray.open_rasterio(href, masked=True, chunks=None)
        clipped = raster.rio.clip(
            [mapping(shape(geometry))], crs="EPSG:4326", drop=True, from_disk=True
        )
        centroid = shape(geometry).centroid
        utm_zone = int((centroid.x + 180) // 6) + 1
        utm_epsg = 32600 + utm_zone if centroid.y >= 0 else 32700 + utm_zone
        projected = clipped.rio.reproject(f"EPSG:{utm_epsg}")
    values = np.ma.asarray(projected.squeeze().values, dtype=np.float64).filled(np.nan)
    values[values == 0] = np.nan
    x_resolution, y_resolution = projected.rio.resolution()
    gradient_y, gradient_x = np.gradient(values, abs(y_resolution), abs(x_resolution))
    valid_elevation = values[np.isfinite(values)]
    slope = np.sqrt(gradient_x**2 + gradient_y**2) * 100
    valid_slope = slope[np.isfinite(slope)]
    output = CACHE_DIR / f"{key}-dem.npz"
    np.savez_compressed(output, elevation=values, slope_percent=slope)
    raster.close()
    mean_elevation = float(np.mean(valid_elevation)) if valid_elevation.size else None
    mean_slope = float(np.mean(valid_slope)) if valid_slope.size else None
    erosion_risk = (
        "Low" if mean_slope is not None and mean_slope < 5
        else "Moderate" if mean_slope is not None and mean_slope < 15
        else "High" if mean_slope is not None
        else None
    )
    return {
        "status": "computed",
        "mean_elevation_m": mean_elevation,
        "mean_slope_percent": mean_slope,
        "erosion_risk": erosion_risk,
        "cached_array_files": [str(output)],
        "scene_id": item.id,
    }


def _clip_mean_sync(
    item: Any, asset_keys: tuple[str, ...], geometry: dict[str, Any], token: str | None = None
) -> float | None:
    import rioxarray
    import rasterio
    from shapely.geometry import mapping

    href = _asset_url(item, asset_keys)
    if not href:
        raise ValueError(f"Scene {item.id} has no matching raster asset.")
    env_options = {"GDAL_HTTP_HEADERS": f"Authorization: Bearer {token}"} if token else {}
    with rasterio.Env(**env_options):
        raster = rioxarray.open_rasterio(href, masked=True, chunks=None)
        clipped = raster.rio.clip(
            [mapping(shape(geometry))], crs="EPSG:4326", drop=True, from_disk=True
        )
    values = np.asarray(clipped.squeeze().values, dtype=np.float64)
    raster.close()
    valid = values[np.isfinite(values)]
    return float(np.mean(valid)) if valid.size else None


async def fetch_satellite_rasters(request: FetchRequest, key: str) -> dict[str, Any]:
    token = None
    catalog_url, collections = EARTH_SEARCH_URL, ["sentinel-2-c1-l2a"]
    query, source = None, "Element 84 Earth Search"
    try:
        items = []
        for collection in collections:
            collection_items = await asyncio.to_thread(
                _stac_search, catalog_url, [collection], request.bbox, request.start, request.end, token, query
            )
            items.extend(collection_items)
        if not items and (date.fromisoformat(request.end) - date.fromisoformat(request.start)).days < STAC_FALLBACK_DAYS:
            widened_start = (date.fromisoformat(request.end) - timedelta(days=STAC_FALLBACK_DAYS)).isoformat()
            _debug(f"no STAC items in requested window; widening datetime={widened_start}/{request.end}")
            items = []
            for collection in collections:
                items.extend(
                    await asyncio.to_thread(
                        _stac_search, catalog_url, [collection], request.bbox,
                        widened_start, request.end, token, query
                    )
                )
        selected_items = sorted(items, key=_item_date, reverse=True)
        metadata = _raster_metadata(selected_items, token)
        if selected_items:
            cached_files, selected_bands = await asyncio.to_thread(
                _clip_rasters_sync, selected_items, request.geometry, key, token
            )
            metadata["cached_array_files"] = cached_files
            metadata["bands"] = selected_bands
            metadata["indices"] = _calculate_optical_indices(cached_files)
        metadata["search_source"] = source
        return metadata
    except (APIError, ParametersError, httpx.HTTPError, RuntimeError, ValueError, OSError, KeyError) as error:
        return {"scenes": [], "bands": [], "cached_array_files": [], "error": str(error), "search_source": source}


async def fetch_ecostress(request: FetchRequest) -> dict[str, Any]:
    token = os.getenv("EARTHDATA_TOKEN")
    if not token:
        return {"status": "not_configured", "reason": "Set EARTHDATA_TOKEN to query ECOSTRESS."}
    try:
        items = await asyncio.to_thread(
            _stac_search, LPCLOUD_URL, ["ECO_L4T_ESI_003"], request.bbox, request.start, request.end, token, None
        )
        return {
            "status": "metadata_available",
            "scenes": [{"id": item.id, "date": _item_date(item)} for item in items[:10]],
            "mean_esi": await asyncio.to_thread(
                _clip_mean_sync, items[0], ("ESI", "esi", "data"), request.geometry, token
            ),
        }
    except (RuntimeError, ValueError, OSError) as error:
        return {"status": "unavailable", "error": str(error)}


async def fetch_terrain(request: FetchRequest, key: str) -> dict[str, Any]:
    try:
        items = await asyncio.to_thread(
            _stac_search, EARTH_SEARCH_URL, ["cop-dem-glo-30"], request.bbox, None, None, None, {}
        )
        if not items:
            return {"status": "no_scene", "mean_elevation_m": None, "mean_slope_percent": None}
        return await asyncio.to_thread(_clip_dem_sync, items[0], request.geometry, key)
    except (RuntimeError, ValueError, OSError) as error:
        return {"status": "unavailable", "error": str(error)}


async def fetch_bundle(request: FetchRequest) -> dict[str, Any]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = cache_key(request)
    cache_file = CACHE_DIR / f"{key}.json"
    if cache_file.exists() and time.time() - cache_file.stat().st_mtime < 7 * 24 * 3600:
        return json.loads(cache_file.read_text())

    warnings: list[str] = []
    async with httpx.AsyncClient(timeout=30) as client:
        results = await asyncio.gather(
            fetch_nasa_power(client, request),
            fetch_soilgrids(client, request),
            fetch_modis(client, request),
            fetch_satellite_rasters(request, key),
            fetch_terrain(request, key),
            return_exceptions=True,
        )
    weather, soil, modis, satellite, terrain = results
    ecostress = {
        "status": "unavailable",
        "reason": "ECOSTRESS raster loading is disabled for the fast environmental summary.",
    }
    if isinstance(weather, Exception):
        warnings.append(f"NASA POWER unavailable: {weather}")
        weather = {"weather_summary": {}, "daily": [], "source": "NASA POWER"}
    if isinstance(soil, Exception):
        warnings.append(f"SoilGrids unavailable: {soil}")
        soil = {"soil_profile": {}, "source": "SoilGrids"}
    if isinstance(modis, Exception):
        warnings.append(f"MODIS unavailable: {modis}")
        modis = {
            "status": "unavailable",
            "products": [],
            "subsets": {},
            "source": "NASA ORNL DAAC MODIS REST",
            "errors": {"request": str(modis)},
        }
    elif modis.get("status") != "available":
        warnings.append("NASA ORNL DAAC MODIS returned no usable product subsets.")
    if isinstance(satellite, Exception):
        warnings.append(f"Sentinel-2 raster layers unavailable: {satellite}")
        satellite = {"status": "unavailable", "scenes": [], "bands": [], "cached_array_files": []}
    elif not satellite.get("cached_array_files"):
        warnings.append(
            f"Sentinel-2 raster layers unavailable: {satellite.get('error', 'no scene found')}."
        )
    for name, result in (("terrain", terrain),):
        if isinstance(result, Exception):
            warnings.append(f"{name} unavailable: {result}")
            terrain = {"status": "unavailable"}
    optical_indices = satellite.get("indices", {})
    stage3 = _calculate_stage3(
        weather.get("daily", []),
        optical_indices,
        request.latitude,
        terrain.get("mean_elevation_m"),
        terrain.get("mean_slope_percent"),
    )
    stage4 = calculate_stage4(
        weather=weather["weather_summary"],
        stage3=stage3,
        soil=soil["soil_profile"],
        terrain=terrain,
    )
    stage5 = calculate_stage5(
        weather["daily"],
        latitude=request.latitude,
        rainfall_mm=weather["weather_summary"].get("total_rainfall_mm"),
        stage4=stage4,
    )
    bundle = {
        "weather_summary": weather["weather_summary"],
        "daily_weather": weather["daily"],
        "soil_profile": soil["soil_profile"],
        "terrain_slope": terrain,
        "modis": modis,
        "optical_indices": optical_indices,
        "stage3_indicators": stage3,
        "stage4_agronomics": stage4,
        "stage5_climate": stage5,
        "satellite_scene_metadata": satellite,
        "ecostress": ecostress,
        "data_sources_used": [
            weather.get("source", "NASA POWER"),
            soil.get("source", "SoilGrids"),
            modis.get("source", "NASA ORNL DAAC MODIS REST"),
            satellite.get("search_source", "STAC"),
            "NASA POWER GWETROOT for daily root-zone wetness",
        ],
        "request": {"centroid": [request.latitude, request.longitude], "bbox": request.bbox, "date_range": [request.start, request.end]},
        "warnings": warnings,
        "cache_key": key,
    }
    cache_file.write_text(json.dumps(bundle))
    return bundle
