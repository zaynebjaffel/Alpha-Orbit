"""Reproduce the new-polygon environmental fetch path without the browser."""

from __future__ import annotations

import asyncio
import json

from backend.services.data_fetcher import FetchRequest, fetch_bundle


POLYGONS = {
    "beja": {
        "centroid": (36.7256, 9.1817),
        "bbox": [9.18055, 36.72445, 9.18285, 36.72675],
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [9.18055, 36.72445],
                [9.18285, 36.72445],
                [9.18285, 36.72675],
                [9.18055, 36.72675],
                [9.18055, 36.72445],
            ]],
        },
    },
    "new-small-field": {
        "centroid": (36.726, 9.182),
        "bbox": [9.1815, 36.7255, 9.1825, 36.7265],
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [9.1815, 36.7255],
                [9.1825, 36.7255],
                [9.1825, 36.7265],
                [9.1815, 36.7265],
                [9.1815, 36.7255],
            ]],
        },
    },
}


async def check_polygon(name: str, definition: dict) -> None:
    latitude, longitude = definition["centroid"]
    request = FetchRequest(
        latitude=latitude,
        longitude=longitude,
        bbox=definition["bbox"],
        geometry=definition["geometry"],
        start="2025-01-01",
        end="2025-12-31",
    )
    bundle = await fetch_bundle(request)
    weather = bundle["weather_summary"]
    assert weather["observations"] > 0, f"{name}: NASA POWER returned no observations"
    assert bundle["stage4_agronomics"]["rotation_strategies"], f"{name}: Stage 4 returned no rotations"
    assert bundle["stage5_climate"]["scenario"]["warming_c"] == 2, f"{name}: Stage 5 scenario missing"
    print(json.dumps({
        "polygon": name,
        "observations": weather["observations"],
        "warnings": bundle.get("warnings", []),
        "cache_key": bundle["cache_key"],
        "status": "ok",
    }, sort_keys=True))


async def main() -> None:
    for name, definition in POLYGONS.items():
        await check_polygon(name, definition)


if __name__ == "__main__":
    asyncio.run(main())
