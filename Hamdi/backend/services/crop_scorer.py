"""Data-driven crop scoring and rotation synthesis for Stages 4-5."""

from __future__ import annotations

from typing import Any

import json
from pathlib import Path

CROP_DATABASE = json.loads((Path(__file__).with_name("crop_database.json")).read_text())
NITROGEN_PRICE_USD_KG = 1.20


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _assessment(
    crop_id: str,
    crop: dict[str, Any],
    *,
    et0: float | None,
    rainfall: float | None,
    wetness: float | None,
    heat_days: int,
    slope: float | None,
    awc: float | None,
    warming: bool = False,
) -> dict[str, Any]:
    days = float(crop["typical_growing_days"])
    demand = et0 * days * float(crop["kc_mid"]) if et0 is not None else None
    seasonal_rain = rainfall * days / 365 if rainfall is not None else None
    deficit = max(0.0, demand - seasonal_rain) if demand is not None and seasonal_rain is not None else None
    deficit_ratio = min(1.0, deficit / demand) if deficit is not None and demand else None
    thermal_days = heat_days * (1.15 if warming else 1.0)
    heat_penalty = min(0.45, thermal_days / max(days, 1) * 1.5)
    water_factor = 1.0 if deficit_ratio is None else max(0.0, 1.0 - deficit_ratio)
    stress_factor = 1.0 if wetness is None else min(1.0, max(0.0, wetness * 1.5))
    soil_factor = 1.0 if awc is None else min(1.0, max(0.0, awc / 30.0))
    slope_factor = 1.0 if slope is None else max(0.0, 1.0 - (0.1 if slope >= 15 else 0.0))
    heat_factor = max(0.0, 1.0 - heat_penalty)
    score = 100 * (0.38 * water_factor + 0.20 * stress_factor + 0.16 * soil_factor + 0.10 * slope_factor + 0.16 * heat_factor)
    yield_factor = max(0.0, water_factor * (1 - heat_penalty))
    projected_yield = float(crop["baseline_yield_t_ha"]) * yield_factor
    margin = projected_yield * float(crop["market_price_per_t_usd"]) - float(crop["production_cost_per_ha_usd"])
    strengths = []
    risks = []
    if crop["erosion_control_rating"] >= 0.8:
        strengths.append("strong soil-cover and erosion control")
    if crop["n_fixation_rating"] in {"High", "Medium"}:
        strengths.append(f"{crop['n_fixation_rating'].lower()} nitrogen fixation")
    if float(crop["max_root_depth_m"]) >= 1.5:
        strengths.append("deep rooting")
    if float(crop["heat_tolerance_max_c"]) >= 36:
        strengths.append("higher heat tolerance")
    if deficit_ratio is not None and deficit_ratio > 0.35:
        risks.append("high modeled water deficit")
    if heat_penalty > 0.10:
        risks.append("heat stress exposure")
    if slope is not None and slope >= 10 and crop["erosion_control_rating"] < 0.6:
        risks.append("limited erosion protection on sloped land")
    result = {
        "crop_id": crop_id,
        "name": crop["name"],
        "category": crop["category"],
        "plant_family": crop["plant_family"],
        "scientific_name": crop["scientific_name"],
        "suitability_score": round(max(0, min(100, score)), 2),
        "plus2c_score": round(max(0, min(100, score - (heat_penalty * 22))), 2),
        "score_delta_plus2c": round(-(heat_penalty * 22), 2),
        "seasonal_water_demand_mm": round(demand, 2) if demand is not None else None,
        "water_demand_mm_ha": round(demand, 2) if demand is not None else None,
        "water_deficit_mm": round(deficit, 2) if deficit is not None else None,
        "irrigation_need_m3_ha": round(deficit * 10, 2) if deficit is not None else None,
        "expected_yield_loss_pct": round((1 - yield_factor) * 100, 2),
        "projected_yield_t_ha": round(projected_yield, 2),
        "yield_factor": round(yield_factor, 3),
        "net_margin_usd_ha": round(margin, 2),
        "estimated_revenue_usd_ha": round(projected_yield * float(crop["market_price_per_t_usd"]), 2),
        "fertilizer_saving_usd_ha": round(max(0, float(crop["n_contribution_kg_ha"]) * NITROGEN_PRICE_USD_KG), 2),
        "n_contribution_kg_ha": crop["n_contribution_kg_ha"],
        "nitrogen_contribution_kg_ha": crop["n_contribution_kg_ha"],
        "max_root_depth_m": crop["max_root_depth_m"],
        "erosion_control_rating": crop["erosion_control_rating"],
        "water_stress_sensitivity_ky": crop["water_stress_sensitivity_ky"],
        "heat_tolerance_max_c": crop["heat_tolerance_max_c"],
        "strengths": strengths,
        "risks": risks,
        "confidence": "medium" if et0 is not None and rainfall is not None else "low",
    }
    return result


def _rotation(name: str, sequence: list[str], assessments: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = [
        assessments.get(crop_id, {
            "irrigation_need_m3_ha": 0,
            "estimated_revenue_usd_ha": 0,
            "fertilizer_saving_usd_ha": 0,
            "n_contribution_kg_ha": 0,
            "suitability_score": 0,
            "plus2c_score": 0,
        })
        for crop_id in sequence
    ]
    return {
        "name": name,
        "sequence": sequence,
        "years": len(sequence),
        "water_use_m3_ha": round(sum(row["irrigation_need_m3_ha"] or 0 for row in rows), 2),
        "supplemental_income_usd_ha": round(sum(row["estimated_revenue_usd_ha"] for row in rows), 2),
        "fertilizer_savings_usd_ha": round(sum(row["fertilizer_saving_usd_ha"] for row in rows), 2),
        "net_nitrogen_contribution_kg_ha": round(sum(row["n_contribution_kg_ha"] for row in rows), 2),
        "mean_suitability_score": round(sum(row["suitability_score"] for row in rows) / len(rows), 2),
        "mean_plus2c_score": round(sum(row["plus2c_score"] for row in rows) / len(rows), 2),
    }


def _pick(rows: list[dict[str, Any]], category: str, *, key: str = "suitability_score") -> dict[str, Any]:
    candidates = [row for row in rows if row["category"] == category]
    if not candidates:
        raise ValueError(f"No crops available in category {category}.")
    return max(candidates, key=lambda row: (row[key], row["net_margin_usd_ha"]))


def synthesize_rotations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cereal = _pick(rows, "Cereal")
    legume = _pick(rows, "Legume")
    cover = _pick(rows, "Cover Crop")
    drought = sorted(rows, key=lambda row: (row["plus2c_score"], -row["seasonal_water_demand_mm"] if row["seasonal_water_demand_mm"] is not None else 0), reverse=True)
    drought = [row for row in drought if row["seasonal_water_demand_mm"] is not None][:3]
    cash = sorted(rows, key=lambda row: row["net_margin_usd_ha"], reverse=True)
    cash_legume = next(row for row in cash if row["category"] == "Legume")
    strategies = [
        ("Conventional baseline", [cereal["crop_id"], "fallow", cereal["crop_id"]]),
        ("Adaptive cereal-legume-cover", [cereal["crop_id"], legume["crop_id"], cover["crop_id"]]),
        ("Water-conservation resilience", [row["crop_id"] for row in drought[:3]]),
        ("High-margin soil-building", [cash[0]["crop_id"], cash_legume["crop_id"], cover["crop_id"]]),
    ]
    result = []
    for name, sequence in strategies:
        if len(sequence) == 3:
            result.append(_rotation(name, sequence, {row["crop_id"]: row for row in rows}))
    return result


def score_crops(*, weather: dict[str, Any], stage3: dict[str, Any], soil: dict[str, Any], terrain: dict[str, Any]) -> dict[str, Any]:
    et0 = _number(stage3.get("reference_et0", {}).get("mean_daily_mm"))
    rainfall = _number(weather.get("total_rainfall_mm"))
    wetness = _number(stage3.get("moisture_stress", {}).get("mean_root_zone_wetness"))
    heat_days = int(stage3.get("vulnerability", {}).get("heat_stress", {}).get("days", 0))
    slope = _number(terrain.get("mean_slope_percent"))
    awc = _number(soil.get("available_water_capacity", {}).get("33_kpa"))
    rows = [_assessment(crop_id, crop, et0=et0, rainfall=rainfall, wetness=wetness, heat_days=heat_days, slope=slope, awc=awc) for crop_id, crop in CROP_DATABASE.items()]
    ranked = sorted(rows, key=lambda row: row["suitability_score"])
    rotations = synthesize_rotations(rows)
    best_crop = max(rows, key=lambda row: (row["suitability_score"], row["net_margin_usd_ha"]))
    best_rotation = max(rotations, key=lambda row: row["mean_suitability_score"]) if rotations else None
    return {
        "crop_rankings_worst_to_best": ranked,
        "rotation_strategies": rotations,
        "catalog_size": len(rows),
        "recommendation_summary": {
            "best_crop": best_crop,
            "best_rotation": best_rotation,
            "actions": [
                f"Prioritize {best_crop['name']} for the strongest measured environmental fit ({best_crop['suitability_score']:.1f}/100).",
                f"Use {best_rotation['name']} as the preferred three-year strategy." if best_rotation else "No complete rotation could be synthesized.",
                "Use a nitrogen-fixing crop and high-cover phase to protect soil and reduce fertilizer dependence.",
            ],
        },
        "assumptions": {
            "nitrogen_price_usd_kg": NITROGEN_PRICE_USD_KG,
            "water_conversion_l_m2_to_m3_ha": 10,
            "rainfall_allocation": "Annual rainfall apportioned across each crop's typical growing days.",
        },
    }


def custom_rotation(crop_ids: list[str], *, weather: dict[str, Any], stage3: dict[str, Any], soil: dict[str, Any], terrain: dict[str, Any]) -> dict[str, Any]:
    if len(crop_ids) != 3 or any(crop_id not in CROP_DATABASE for crop_id in crop_ids):
        raise ValueError("crop_ids must contain exactly three known crop IDs.")
    scored = score_crops(weather=weather, stage3=stage3, soil=soil, terrain=terrain)
    rows = {row["crop_id"]: row for row in scored["crop_rankings_worst_to_best"]}
    return _rotation("Custom rotation", crop_ids, rows)
