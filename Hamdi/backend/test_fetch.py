"""Live Module 2 smoke test for a small agricultural polygon near Beja, Tunisia."""

from __future__ import annotations

import asyncio
import json

from services.data_fetcher import FetchRequest, fetch_bundle


BEJA_FARM = {
    "type": "Polygon",
    "coordinates": [[
        [9.18055, 36.72445],
        [9.18285, 36.72445],
        [9.18285, 36.72675],
        [9.18055, 36.72675],
        [9.18055, 36.72445],
    ]],
}


async def main() -> None:
    request = FetchRequest(
        latitude=36.7256,
        longitude=9.1817,
        bbox=[9.18055, 36.72445, 9.18285, 36.72675],
        geometry=BEJA_FARM,
        start="2025-01-01",
        end="2025-12-31",
    )
    bundle = await fetch_bundle(request)
    weather = bundle["weather_summary"]
    soil = bundle["soil_profile"]
    terrain = bundle["terrain_slope"]
    modis = bundle["modis"]
    stage4 = bundle["stage4_agronomics"]
    stage5 = bundle["stage5_climate"]
    print("Module 2 live fetch: Beja, Tunisia")
    print("NASA POWER:", json.dumps(weather, sort_keys=True))
    print("SoilGrids:", json.dumps(soil, sort_keys=True))
    print("Terrain:", json.dumps(terrain, sort_keys=True))
    print("MODIS:", json.dumps({
        "status": modis.get("status"),
        "products": modis.get("products", []),
        "source": modis.get("source"),
    }, sort_keys=True))
    print("Best crop:", stage4["crop_rankings_worst_to_best"][-1]["name"])
    print("Rotations:", len(stage4["rotation_strategies"]))
    print("Climate scenario:", json.dumps(stage5["deltas"], sort_keys=True))
    print("Warnings:", bundle.get("warnings", []))

    if not weather.get("observations"):
        raise RuntimeError("NASA POWER returned no cleaned observations.")
    if "modis" not in bundle:
        raise RuntimeError("MODIS result was not included in the Stage 2 bundle.")
    if len(stage4["rotation_strategies"]) < 3:
        raise RuntimeError("Stage 4 returned fewer than three rotation strategies.")
    if stage5["scenario"]["warming_c"] != 2:
        raise RuntimeError("Stage 5 did not use the required +2°C scenario.")
    if stage5["deltas"]["heat_stress_days"] < 0:
        raise RuntimeError("Stage 5 returned an invalid heat-stress delta.")
    if not stage5["crop_impacts"] or not stage5["rotation_impacts"]:
        raise RuntimeError("Stage 5 returned no crop or rotation impacts.")
    if not stage5["recommendations"]["crop_recommendations"]:
        raise RuntimeError("Stage 5 returned no warming crop recommendations.")
    if not stage5["recommendations"]["cycle_recommendations"]:
        raise RuntimeError("Stage 5 returned no warming cycle recommendations.")


if __name__ == "__main__":
    asyncio.run(main())
