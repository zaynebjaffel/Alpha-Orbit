from __future__ import annotations

import math
from typing import Any

from .crop_engine import CROP_DATABASE

FALLOW_CROP = {
    "id": "fallow",
    "name": "Bare Fallow",
    "category": "Fallow",
    "typical_growing_days": 0,
    "erosion_control_rating": 0.0,
}


def _value(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _et0(row: dict[str, Any], latitude: float, temperature_delta: float = 0.0) -> float | None:
    tmean = _value(row, "T2M")
    tmax = _value(row, "T2M_MAX")
    tmin = _value(row, "T2M_MIN")
    radiation = _value(row, "ALLSKY_SFC_SW_DWN")
    humidity = _value(row, "RH2M")
    wind = _value(row, "WS2M")
    if None in (tmean, tmax, tmin, radiation, humidity, wind):
        return None
    tmean += temperature_delta
    tmax += temperature_delta
    tmin += temperature_delta
    day = __import__("datetime").date.fromisoformat(str(row["date"])).timetuple().tm_yday
    lat = math.radians(latitude)
    distance = 1 + 0.033 * math.cos(2 * math.pi * day / 365)
    declination = 0.409 * math.sin(2 * math.pi * day / 365 - 1.39)
    sunset = math.acos(max(-1, min(1, -math.tan(lat) * math.tan(declination))))
    ra = (24 * 60 / math.pi) * 0.0820 * distance * (
        sunset * math.sin(lat) * math.sin(declination)
        + math.cos(lat) * math.cos(declination) * math.sin(sunset)
    )
    rso = 0.75 * ra
    es_max = 0.6108 * math.exp(17.27 * tmax / (tmax + 237.3))
    es_min = 0.6108 * math.exp(17.27 * tmin / (tmin + 237.3))
    es = (es_max + es_min) / 2
    ea = max(0, es * humidity / 100)
    delta = 4098 * (0.6108 * math.exp(17.27 * tmean / (tmean + 237.3))) / (tmean + 237.3) ** 2
    gamma = 0.000665 * 101.3
    shortwave = 0.77 * radiation
    ratio = min(radiation / rso, 1) if rso else 0
    longwave = 4.903e-9 * (((tmax + 273.16) ** 4 + (tmin + 273.16) ** 4) / 2) * (0.34 - 0.14 * math.sqrt(ea)) * (1.35 * ratio - 0.35)
    net_radiation = max(0, shortwave - longwave)
    return max(0, (0.408 * delta * net_radiation + gamma * (900 / (tmean + 273)) * wind * max(es - ea, 0)) / (delta + gamma * (1 + 0.34 * wind)))


def _scenario_crop(crop_id: str, baseline_et0: float, scenario_et0: float, heat_days: int, baseline_heat_days: int, rainfall_mm: float | None) -> dict[str, Any]:
    crop = CROP_DATABASE[crop_id]
    days = crop["typical_growing_days"]
    baseline_demand = baseline_et0 * days * crop["kc_mid"]
    scenario_demand = scenario_et0 * days * crop["kc_mid"]
    rain = rainfall_mm * days / 365 if rainfall_mm is not None else None
    baseline_irrigation = max(0, baseline_demand - rain) if rain is not None else None
    scenario_irrigation = max(0, scenario_demand - rain) if rain is not None else None
    heat_penalty_delta = min(0.35, heat_days / days * 1.5) - min(0.35, baseline_heat_days / days * 1.5)
    yield_factor_delta = -max(0, heat_penalty_delta)
    return {
        "crop_id": crop_id,
        "name": crop["name"],
        "baseline_water_demand_mm_ha": round(baseline_demand, 2),
        "scenario_water_demand_mm_ha": round(scenario_demand, 2),
        "additional_irrigation_m3_ha": round((scenario_irrigation - baseline_irrigation) * 10, 2) if baseline_irrigation is not None else None,
        "baseline_heat_days": baseline_heat_days,
        "scenario_heat_days": heat_days,
        "yield_factor_change": round(yield_factor_delta, 3),
        "risk": "high" if heat_penalty_delta >= 0.15 else "moderate" if heat_penalty_delta > 0 else "low",
    }


def _planting_shift(days: int) -> str:
    if days >= 135:
        return "7–14 days earlier than the historical local planting window"
    if days >= 100:
        return "5–10 days earlier than the historical local planting window"
    return "3–7 days earlier than the historical local planting window"


def _warming_crop_recommendation(impact: dict[str, Any]) -> dict[str, Any]:
    crop = CROP_DATABASE[impact["crop_id"]]
    days = int(crop["typical_growing_days"])
    shift = _planting_shift(days)
    if impact["risk"] == "high":
        action = (
            f"Use {crop['name']} only with reliable supplemental irrigation; "
            f"plant {shift} and avoid its peak water demand during the hottest weeks."
        )
    elif impact["risk"] == "moderate":
        action = (
            f"Keep {crop['name']} as a monitored option, plant {shift}, "
            "and reserve irrigation for flowering and grain or pod filling."
        )
    elif "Cover Crop" in crop["category"]:
        action = (
            f"Use {crop['name']} as a low-risk shoulder-season cover; "
            f"establish it {shift} and terminate it before it competes for spring water."
        )
    else:
        action = (
            f"{crop['name']} is a comparatively resilient warming option; "
            f"plant {shift} and maintain residue to conserve soil moisture."
        )
    return {
        "crop_id": impact["crop_id"],
        "name": impact["name"],
        "risk": impact["risk"],
        "recommended_planting_shift": shift,
        "recommended_action": action,
        "water_priority": "high" if (impact["additional_irrigation_m3_ha"] or 0) >= 150 else "moderate" if (impact["additional_irrigation_m3_ha"] or 0) > 0 else "low",
    }


def _warming_cycle_recommendation(
    rotation: dict[str, Any],
    crop_impacts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    schedule = []
    for year, crop_id in enumerate(rotation["sequence"], start=1):
        if crop_id == "fallow":
            schedule.append({
                "year": year,
                "crop_id": crop_id,
                "crop": FALLOW_CROP["name"],
                "typical_growing_days": 0,
                "planting_timing": "No planting; keep the phase as a monitored baseline.",
                "cycle_action": "Baseline exposure phase: monitor erosion and bare-ground moisture loss.",
            })
            continue
        crop = CROP_DATABASE.get(crop_id)
        if crop is None:
            continue
        impact = crop_impacts.get(crop_id)
        if impact is None:
            continue
        schedule.append({
            "year": year,
            "crop_id": crop_id,
            "crop": crop["name"],
            "typical_growing_days": crop["typical_growing_days"],
            "planting_timing": _planting_shift(int(crop["typical_growing_days"])),
            "cycle_action": (
                "Use as a winter or shoulder-season cover and terminate before the next cash crop."
                if "Cover Crop" in crop["category"]
                else "Prioritize irrigation and scouting during flowering and reproductive growth."
            ),
        })
    impacts = [crop_impacts[crop_id] for crop_id in rotation["sequence"] if crop_id in crop_impacts]
    additional_water = sum(row["additional_irrigation_m3_ha"] or 0 for row in impacts)
    mean_yield_change = sum(row["yield_factor_change"] for row in impacts) / len(impacts) if impacts else 0
    crop_entries = [CROP_DATABASE[crop_id] for crop_id in rotation["sequence"] if crop_id in CROP_DATABASE]
    has_legume = any("Legume" in crop["category"] for crop in crop_entries)
    has_cover = any("Cover Crop" in crop["category"] for crop in crop_entries)
    return {
        "name": rotation["name"],
        "sequence": rotation["sequence"],
        "climate_fit": "strong" if additional_water <= 250 and mean_yield_change >= -0.08 else "moderate" if additional_water <= 450 else "weak",
        "recommendation": (
            "Preferred warming cycle: retain the legume phase and use the cover phase to protect soil moisture."
            if has_legume and has_cover
            else "Use only where irrigation capacity covers the projected warming water increase."
        ),
        "schedule": schedule,
    }


def calculate_stage5(
    daily: list[dict[str, Any]],
    *,
    latitude: float,
    rainfall_mm: float | None,
    stage4: dict[str, Any],
    warming_c: float = 2.0,
) -> dict[str, Any]:
    baseline_gdd = scenario_gdd = 0.0
    baseline_et0_values: list[float] = []
    scenario_et0_values: list[float] = []
    baseline_heat_days: list[str] = []
    scenario_heat_days: list[str] = []
    scenario_daily: list[dict[str, Any]] = []
    for row in daily:
        tmax = _value(row, "T2M_MAX")
        tmin = _value(row, "T2M_MIN")
        tmean = _value(row, "T2M")
        if tmax is not None and tmin is not None:
            baseline_gdd += max(0, min((tmax + tmin) / 2, 35) - 10)
            scenario_gdd += max(0, min((tmax + warming_c + tmin + warming_c) / 2, 35) - 10)
            if tmax > 35:
                baseline_heat_days.append(str(row["date"]))
            if tmax + warming_c > 35:
                scenario_heat_days.append(str(row["date"]))
        base_et0 = _et0(row, latitude)
        warm_et0 = _et0(row, latitude, warming_c)
        if base_et0 is not None:
            baseline_et0_values.append(base_et0)
        if warm_et0 is not None:
            scenario_et0_values.append(warm_et0)
        scenario_daily.append({
            "date": row.get("date"),
            "temperature_delta_c": warming_c,
            "tmax_c": tmax + warming_c if tmax is not None else None,
            "tmin_c": tmin + warming_c if tmin is not None else None,
            "et0_mm": warm_et0,
            "precipitation_mm": _value(row, "PRECTOTCORR"),
        })
    base_et0 = sum(baseline_et0_values) / len(baseline_et0_values) if baseline_et0_values else None
    warm_et0 = sum(scenario_et0_values) / len(scenario_et0_values) if scenario_et0_values else None
    crops = [
        _scenario_crop(crop_id, base_et0, warm_et0, len(scenario_heat_days), len(baseline_heat_days), rainfall_mm)
        for crop_id in CROP_DATABASE
    ] if base_et0 is not None and warm_et0 is not None else []
    rotations = []
    for rotation in stage4.get("rotation_strategies", []):
        crop_rows = {row["crop_id"]: row for row in crops}
        impacts = [crop_rows[crop_id] for crop_id in rotation["sequence"] if crop_id in crop_rows]
        rotations.append({
            "name": rotation["name"],
            "sequence": rotation["sequence"],
            "additional_water_m3_ha": round(sum(row["additional_irrigation_m3_ha"] or 0 for row in impacts), 2),
            "mean_yield_factor_change": round(sum(row["yield_factor_change"] for row in impacts) / len(impacts), 3) if impacts else None,
        })
    crop_recommendations = [_warming_crop_recommendation(impact) for impact in crops]
    crop_recommendations.sort(
        key=lambda row: (
            {"low": 0, "moderate": 1, "high": 2}[row["risk"]],
            {"low": 0, "moderate": 1, "high": 2}[row["water_priority"]],
        )
    )
    impact_by_crop = {row["crop_id"]: row for row in crops}
    cycle_recommendations = [
        _warming_cycle_recommendation(rotation, impact_by_crop)
        for rotation in stage4.get("rotation_strategies", [])
    ]
    cycle_recommendations.sort(
        key=lambda row: (
            {"strong": 0, "moderate": 1, "weak": 2}[row["climate_fit"]],
            sum(impact_by_crop[crop_id]["additional_irrigation_m3_ha"] or 0 for crop_id in row["sequence"] if crop_id in impact_by_crop),
        )
    )
    return {
        "scenario": {"name": f"+{warming_c:g}°C warming", "warming_c": warming_c, "precipitation_assumption": "Unchanged from historical series"},
        "baseline": {"gdd": round(baseline_gdd, 2), "heat_stress_days": len(baseline_heat_days), "mean_et0_mm_day": round(base_et0, 2) if base_et0 is not None else None, "total_et0_mm": round(sum(baseline_et0_values), 2)},
        "scenario_result": {"gdd": round(scenario_gdd, 2), "heat_stress_days": len(scenario_heat_days), "mean_et0_mm_day": round(warm_et0, 2) if warm_et0 is not None else None, "total_et0_mm": round(sum(scenario_et0_values), 2)},
        "deltas": {"gdd": round(scenario_gdd - baseline_gdd, 2), "heat_stress_days": len(scenario_heat_days) - len(baseline_heat_days), "et0_mm": round(sum(scenario_et0_values) - sum(baseline_et0_values), 2)},
        "crop_impacts": crops,
        "rotation_impacts": rotations,
        "recommendations": {
            "crop_recommendations": crop_recommendations,
            "cycle_recommendations": cycle_recommendations,
            "summary": (
                "Prefer the lowest-risk crop and the strongest-fit cycle under +2°C; "
                "shift planting earlier and protect soil moisture with residue or cover crops."
            ),
        },
        "daily": scenario_daily,
    }
