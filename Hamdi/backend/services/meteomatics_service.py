from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

METEOMATICS_URL = "https://api.meteomatics.com"
CACHE_DIR = Path(__file__).resolve().parents[2] / "backend" / "cache"
CACHE_TTL_SECONDS = 6 * 60 * 60
ALL_PARAMETERS = (
    "t_max_2m_24h:C,t_min_2m_24h:C,precip_24h:mm,evapotranspiration_24h:mm,"
    "soil_moisture_index_-15cm:idx,frost_warning_24h:idx,heavy_rain_warning_24h:idx,"
    "tstorm_warning_24h:idx,wind_warning_24h:idx"
)
CORE_PARAMETERS = (
    "t_max_2m_24h:C,t_min_2m_24h:C,precip_24h:mm,evapotranspiration_24h:mm,"
    "soil_moisture_index_-15cm:idx"
)


def _cache_key(lat: float, lon: float) -> str:
    return f"{lat:.5f}_{lon:.5f}".replace("-", "m").replace(".", "p")


def _date(value: str) -> str:
    return value[:10]


def _normalize(payload: dict[str, Any], parameters: str) -> list[dict[str, Any]]:
    days: dict[str, dict[str, Any]] = {}
    for series in payload.get("data", []):
        parameter = series.get("parameter")
        for coordinate in series.get("coordinates", []):
            for point in coordinate.get("dates", []):
                day = _date(str(point.get("date", "")))
                if day:
                    days.setdefault(day, {"date": day})[parameter] = point.get("value")
    return [days[day] for day in sorted(days)]


async def fetch_meteomatics_forecast(
    lat: float,
    lon: float,
    *,
    land_name: str | None = None,
    slope_pct: float | None = None,
    top_crop: dict[str, Any] | None = None,
) -> dict[str, Any]:
    user, password = os.getenv("METEOMATICS_USER"), os.getenv("METEOMATICS_PASS")
    if not user or not password:
        return {
            "available": False,
            "reason": "Add METEOMATICS_USER and METEOMATICS_PASS to Hamdi/.env.",
            "centroid": {"lat": round(lat, 5), "lon": round(lon, 5)},
        }
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"forecast-{_cache_key(lat, lon)}.json"
    if cache_file.exists() and time.time() - cache_file.stat().st_mtime < CACHE_TTL_SECONDS:
        return json.loads(cache_file.read_text())
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(days=10)
    base = f"{METEOMATICS_URL}/{start:%Y-%m-%dT%H:%M:%SZ}--{end:%Y-%m-%dT%H:%M:%SZ}:P1D"
    result: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=30) as client:
        for parameters in (ALL_PARAMETERS, CORE_PARAMETERS):
            url = f"{base}/{parameters}/{lat:.5f},{lon:.5f}/json"
            try:
                response = await client.get(url, auth=(user, password))
                response.raise_for_status()
                result = {
                    "available": True,
                    "centroid": {"lat": round(lat, 5), "lon": round(lon, 5)},
                    "land_name": land_name,
                    "slope_pct": slope_pct,
                    "top_crop": top_crop,
                    "parameters": parameters.split(","),
                    "daily": _normalize(response.json(), parameters),
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                }
                break
            except httpx.HTTPStatusError as error:
                if parameters == CORE_PARAMETERS or error.response.status_code not in {400, 403}:
                    result = {
                        "available": False,
                        "reason": f"Meteomatics request failed with HTTP {error.response.status_code}.",
                        "centroid": {"lat": round(lat, 5), "lon": round(lon, 5)},
                    }
                    break
            except httpx.HTTPError as error:
                result = {
                    "available": False,
                    "reason": f"Meteomatics connection failed: {error}",
                    "centroid": {"lat": round(lat, 5), "lon": round(lon, 5)},
                }
                break
    if result.get("available"):
        result["alerts"] = build_field_alerts(result["daily"], slope_pct=slope_pct, top_crop=top_crop)
        cache_file.write_text(json.dumps(result))
    return result


def build_field_alerts(
    daily: list[dict[str, Any]],
    *,
    slope_pct: float | None = None,
    top_crop: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    crop_id = (top_crop or {}).get("crop_id", "")
    heat_limit = 30 if crop_id == "winter_wheat" else 35
    for row in daily:
        day = row["date"]
        tmax, tmin = row.get("t_max_2m_24h:C"), row.get("t_min_2m_24h:C")
        rain, soil = row.get("precip_24h:mm"), row.get("soil_moisture_index_-15cm:idx")
        if row.get("frost_warning_24h:idx", 0) >= 1 or (isinstance(tmin, (int, float)) and tmin <= 0):
            alerts.append({"severity": "high", "date": day, "title": "Frost risk", "message": f"Minimum temperature may reach {tmin}°C; protect sensitive crops."})
        if isinstance(tmax, (int, float)) and tmax >= heat_limit:
            alerts.append({"severity": "moderate", "date": day, "title": "Crop heat threshold", "message": f"Maximum temperature may reach {tmax}°C for {crop_id or 'the selected crop'}."})
        if row.get("tstorm_warning_24h:idx", 0) >= 2 or row.get("wind_warning_24h:idx", 0) >= 2:
            alerts.append({"severity": "high", "date": day, "title": "Severe weather warning", "message": "Meteomatics flags thunderstorm or strong-wind risk."})
        if row.get("heavy_rain_warning_24h:idx", 0) >= 1 or (isinstance(rain, (int, float)) and rain >= 20):
            severity = "moderate" if slope_pct is not None and slope_pct > 5 else "moderate"
            message = "Heavy rain may interrupt field operations."
            if slope_pct is not None and slope_pct > 5:
                message += " Slope exceeds 5%; topsoil runoff risk is elevated."
            alerts.append({"severity": severity, "date": day, "title": "Heavy rain / runoff risk", "message": message})
        if isinstance(soil, (int, float)) and 0.35 <= soil <= 0.75 and isinstance(rain, (int, float)) and rain < 5:
            alerts.append({"severity": "optimal", "date": day, "title": "Optimal sowing window", "message": "Root-zone moisture is favorable and forecast rain is below 5 mm."})
    return alerts
