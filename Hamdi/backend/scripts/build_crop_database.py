"""Build the reproducible Alpha Orbit crop catalogue.

The agronomic values are consolidated from FAO-56 crop coefficient/root-zone
tables, FAO AquaCrop crop calendars, USDA PLANTS functional traits, and
FAOSTAT benchmark yield/price ranges. They are intentionally explicit so a
reviewer can audit or replace a value without changing the engine.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "backend" / "services" / "crop_database.json"
DATA_OUTPUT = ROOT / "backend" / "data" / "crop_database.json"


def crop(
    id: str, name: str, scientific_name: str, category: str, family: str,
    kc: tuple[float, float, float], root: float, p: float, ky: float,
    days: int, base: float, gdd: int, optimal: float, heat: float, frost: str,
    ph: tuple[float, float], ec: float, textures: list[str], erosion: float,
    nitrogen: float, fixation: str, yield_t: float, price: float, cost: float,
) -> dict:
    return {
        "id": id, "name": name, "scientific_name": scientific_name,
        "category": category, "plant_family": family,
        "kc_initial": kc[0], "kc_mid": kc[1], "kc_end": kc[2],
        "max_root_depth_m": root, "depletion_fraction_p": p,
        "water_stress_sensitivity_ky": ky, "typical_growing_days": days,
        "base_temp_c": base, "min_gdd_required": gdd,
        "optimal_temp_max_c": optimal, "heat_tolerance_max_c": heat,
        "frost_tolerance": frost, "optimal_ph_min": ph[0],
        "optimal_ph_max": ph[1], "salinity_threshold_ec": ec,
        "preferred_textures": textures, "erosion_control_rating": erosion,
        "n_contribution_kg_ha": nitrogen, "n_fixation_rating": fixation,
        "baseline_yield_t_ha": yield_t, "market_price_per_t_usd": price,
        "production_cost_per_ha_usd": cost,
        # Legacy Stage 4 aliases retained for saved lands and old clients.
        "yield_t_ha": yield_t, "price_usd_t": price,
        "fertilizer_cost_usd_ha": cost,
    }


CATALOG = [
    crop("corn", "Grain Corn", "Zea mays", "Cereal", "Poaceae", (0.30, 1.20, 0.60), 1.50, 0.55, 1.25, 135, 10, 1350, 30, 35, "none", (5.8, 7.0), 1.7, ["loam", "silt loam"], 0.45, -160, "None", 9.5, 190, 190),
    crop("winter_wheat", "Winter Wheat", "Triticum aestivum", "Cereal", "Poaceae", (0.40, 1.15, 0.40), 1.80, 0.55, 1.00, 160, 4, 1500, 25, 32, "moderate", (6.0, 7.5), 6.0, ["loam", "clay loam"], 0.60, -110, "None", 5.5, 240, 135),
    crop("spring_wheat", "Spring Wheat", "Triticum aestivum", "Cereal", "Poaceae", (0.30, 1.15, 0.40), 1.50, 0.55, 1.00, 120, 4, 1300, 25, 32, "moderate", (6.0, 7.5), 6.0, ["loam", "silt loam"], 0.55, -110, "None", 4.0, 250, 125),
    crop("barley", "Barley", "Hordeum vulgare", "Cereal", "Poaceae", (0.30, 1.15, 0.25), 1.50, 0.55, 0.95, 110, 3, 1100, 24, 30, "moderate", (6.0, 7.5), 8.0, ["loam", "sandy loam"], 0.55, -90, "None", 4.5, 220, 115),
    crop("sorghum", "Grain Sorghum", "Sorghum bicolor", "Cereal", "Poaceae", (0.30, 1.10, 0.55), 1.80, 0.55, 0.90, 120, 8, 1250, 30, 38, "none", (5.5, 8.0), 6.8, ["loam", "clay loam"], 0.50, -100, "None", 5.5, 180, 130),
    crop("pearl_millet", "Pearl Millet", "Cenchrus americanus", "Cereal", "Poaceae", (0.30, 1.05, 0.40), 1.50, 0.60, 0.80, 100, 10, 1050, 32, 40, "none", (5.0, 8.0), 6.0, ["sandy loam", "loam"], 0.50, -70, "None", 2.5, 230, 105),
    crop("oats", "Oats", "Avena sativa", "Cereal", "Poaceae", (0.30, 1.15, 0.25), 1.20, 0.55, 0.85, 100, 4, 1050, 23, 30, "moderate", (5.5, 7.5), 6.0, ["loam", "silt loam"], 0.65, -80, "None", 3.5, 230, 105),
    crop("upland_rice", "Upland Rice", "Oryza sativa", "Cereal", "Poaceae", (0.35, 1.20, 0.60), 1.20, 0.20, 1.10, 120, 10, 1400, 30, 35, "none", (5.0, 6.5), 3.0, ["clay loam", "loam"], 0.35, -120, "None", 3.5, 350, 240),
    crop("soybeans", "Soybeans", "Glycine max", "Legume", "Fabaceae", (0.40, 1.15, 0.50), 1.30, 0.50, 0.85, 120, 8, 1350, 30, 35, "none", (6.0, 7.5), 5.0, ["loam", "silt loam"], 0.65, 40, "High", 3.2, 430, 85),
    crop("chickpeas", "Chickpeas", "Cicer arietinum", "Legume", "Fabaceae", (0.40, 1.00, 0.35), 0.90, 0.50, 0.70, 110, 5, 1050, 26, 34, "moderate", (6.0, 9.0), 8.0, ["sandy loam", "loam"], 0.55, 60, "High", 1.5, 700, 140),
    crop("lentils", "Lentils", "Lens culinaris", "Legume", "Fabaceae", (0.40, 1.10, 0.30), 0.80, 0.50, 0.70, 100, 4, 950, 24, 30, "moderate", (6.0, 8.0), 5.0, ["loam", "silt loam"], 0.60, 55, "High", 1.4, 650, 125),
    crop("faba_beans", "Faba / Broad Beans", "Vicia faba", "Legume", "Fabaceae", (0.40, 1.10, 0.30), 1.00, 0.45, 0.65, 140, 3, 1200, 24, 30, "hardy", (6.0, 8.0), 6.0, ["clay loam", "loam"], 0.75, 80, "High", 3.0, 420, 160),
    crop("dry_beans", "Common Dry Beans", "Phaseolus vulgaris", "Legume", "Fabaceae", (0.35, 1.15, 0.35), 0.90, 0.45, 1.10, 100, 8, 1150, 28, 35, "none", (5.8, 7.5), 2.0, ["loam", "sandy loam"], 0.55, 40, "Medium", 2.0, 850, 180),
    crop("field_peas", "Field Peas", "Pisum sativum", "Legume", "Fabaceae", (0.40, 1.15, 0.30), 0.80, 0.45, 0.70, 100, 4, 900, 24, 30, "hardy", (5.5, 7.5), 5.0, ["loam", "clay loam"], 0.65, 55, "High", 2.5, 300, 120),
    crop("cowpeas", "Cowpeas", "Vigna unguiculata", "Legume", "Fabaceae", (0.40, 1.05, 0.35), 1.00, 0.50, 0.75, 95, 10, 1050, 30, 38, "none", (5.5, 7.5), 4.0, ["sandy loam", "loam"], 0.55, 50, "High", 1.5, 600, 130),
    crop("groundnuts", "Peanuts / Groundnuts", "Arachis hypogaea", "Legume", "Fabaceae", (0.40, 1.15, 0.55), 1.00, 0.50, 0.70, 130, 10, 1450, 30, 36, "none", (5.8, 7.0), 3.0, ["sandy loam"], 0.50, 45, "Medium", 3.0, 520, 220),
    crop("sunflower", "Sunflower", "Helianthus annuus", "Oilseed", "Asteraceae", (0.35, 1.15, 0.35), 1.80, 0.45, 0.95, 125, 6, 1350, 30, 36, "none", (6.0, 7.5), 4.5, ["loam", "clay loam"], 0.50, -70, "None", 2.2, 450, 160),
    crop("canola", "Canola / Rapeseed", "Brassica napus", "Oilseed", "Brassicaceae", (0.35, 1.15, 0.35), 1.50, 0.45, 0.90, 150, 5, 1450, 25, 30, "hardy", (6.0, 7.5), 6.0, ["loam", "clay loam"], 0.65, -80, "None", 2.5, 500, 220),
    crop("safflower", "Safflower", "Carthamus tinctorius", "Oilseed", "Asteraceae", (0.35, 1.05, 0.25), 1.80, 0.55, 0.75, 130, 5, 1250, 28, 38, "none", (6.0, 8.0), 10.0, ["sandy loam", "loam"], 0.55, -60, "None", 1.5, 500, 135),
    crop("sesame", "Sesame", "Sesamum indicum", "Oilseed", "Pedaliaceae", (0.35, 1.10, 0.45), 1.20, 0.60, 0.70, 110, 10, 1200, 30, 40, "none", (5.5, 8.0), 5.0, ["sandy loam"], 0.45, -50, "None", 1.0, 1100, 150),
    crop("cotton", "Upland Cotton", "Gossypium hirsutum", "Oilseed", "Malvaceae", (0.35, 1.15, 0.70), 1.50, 0.65, 0.85, 170, 15, 1800, 32, 40, "none", (5.5, 8.0), 7.7, ["loam", "clay loam"], 0.40, -120, "None", 1.2, 1500, 900),
    crop("potatoes", "Potatoes", "Solanum tuberosum", "Root/Cash", "Solanaceae", (0.50, 1.15, 0.75), 0.60, 0.35, 1.10, 110, 7, 1200, 25, 30, "moderate", (5.0, 6.5), 1.7, ["sandy loam", "loam"], 0.55, -140, "None", 30.0, 180, 1800),
    crop("sugar_beets", "Sugar Beets", "Beta vulgaris", "Root/Cash", "Amaranthaceae", (0.35, 1.10, 0.70), 1.20, 0.55, 1.10, 170, 5, 1700, 25, 32, "moderate", (6.0, 8.0), 7.0, ["loam", "silt loam"], 0.60, -140, "None", 70.0, 55, 2200),
    crop("processing_tomatoes", "Processing Tomatoes", "Solanum lycopersicum", "Root/Cash", "Solanaceae", (0.40, 1.15, 0.70), 1.00, 0.40, 1.05, 140, 10, 1400, 30, 35, "none", (5.5, 7.5), 2.5, ["loam", "sandy loam"], 0.45, -120, "None", 80.0, 110, 4000),
    crop("quinoa", "Quinoa", "Chenopodium quinoa", "Root/Cash", "Amaranthaceae", (0.35, 1.00, 0.30), 0.60, 0.50, 0.60, 120, 4, 1150, 25, 32, "moderate", (6.0, 8.5), 8.0, ["sandy loam", "loam"], 0.55, 30, "Low", 2.0, 1200, 220),
    crop("alfalfa", "Alfalfa / Lucerne", "Medicago sativa", "Forage", "Fabaceae", (0.40, 1.20, 1.15), 2.00, 0.55, 0.40, 180, 5, 1800, 28, 35, "hardy", (6.5, 7.5), 2.0, ["loam", "silt loam"], 0.90, 180, "High", 12.0, 180, 1200),
    crop("sudan_grass", "Sudan Grass", "Sorghum × drummondii", "Forage", "Poaceae", (0.35, 1.10, 0.50), 1.50, 0.55, 0.70, 100, 10, 1150, 30, 38, "none", (5.5, 8.0), 6.0, ["loam", "sandy loam"], 0.85, -40, "None", 10.0, 120, 650),
    crop("red_clover", "Red Clover (Cover Crop)", "Trifolium pratense", "Cover Crop", "Fabaceae", (0.40, 1.05, 0.90), 0.80, 0.50, 0.60, 90, 4, 850, 24, 30, "hardy", (6.0, 7.0), 3.0, ["loam", "silt loam"], 0.95, 90, "High", 0.0, 0, 35),
    crop("crimson_clover", "Crimson Clover", "Trifolium incarnatum", "Cover Crop", "Fabaceae", (0.40, 1.05, 0.90), 0.70, 0.50, 0.60, 90, 4, 850, 24, 30, "moderate", (5.5, 7.0), 3.0, ["sandy loam", "loam"], 0.90, 100, "High", 0.0, 0, 35),
    crop("hairy_vetch", "Hairy Vetch", "Vicia villosa", "Cover Crop", "Fabaceae", (0.40, 1.05, 0.90), 0.90, 0.50, 0.55, 120, 4, 950, 22, 30, "hardy", (5.5, 7.5), 4.0, ["loam", "clay loam"], 0.95, 150, "High", 0.0, 0, 40),
    crop("winter_rye", "Winter Rye (Cover Crop)", "Secale cereale", "Cover Crop", "Poaceae", (0.30, 1.00, 0.40), 1.20, 0.55, 0.70, 100, 3, 1000, 24, 32, "hardy", (5.0, 7.5), 8.0, ["sandy loam", "loam"], 0.95, 0, "None", 0.0, 0, 30),
    crop("tillage_radish", "Daikon Tillage Radish", "Raphanus sativus", "Cover Crop", "Brassicaceae", (0.40, 1.05, 0.80), 0.60, 0.45, 0.55, 70, 5, 650, 24, 30, "moderate", (5.5, 7.5), 4.0, ["loam", "clay loam"], 0.95, 30, "Low", 0.0, 0, 45),
    crop("buckwheat", "Buckwheat", "Fagopyrum esculentum", "Cover Crop", "Polygonaceae", (0.35, 1.00, 0.50), 0.60, 0.50, 0.50, 75, 8, 700, 25, 35, "none", (5.0, 7.0), 3.0, ["loam", "sandy loam"], 0.85, 20, "Low", 0.0, 0, 35),
]


def main() -> None:
    if len(CATALOG) < 30:
        raise RuntimeError("Crop catalogue must contain at least 30 entries.")
    if len({item["id"] for item in CATALOG}) != len(CATALOG):
        raise RuntimeError("Crop IDs must be unique.")
    payload = json.dumps({item["id"]: item for item in CATALOG}, indent=2) + "\n"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    DATA_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(payload)
    DATA_OUTPUT.write_text(payload)
    print(f"Wrote {len(CATALOG)} crops to {OUTPUT} and {DATA_OUTPUT}")


if __name__ == "__main__":
    main()
