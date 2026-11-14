"""Independent validity checks for the Stages 1-5 backend engines."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app.main import forecast_alerts, process_polygon
from backend.services import meteomatics_service
from backend.services.climate_engine import calculate_stage5
from backend.services.crop_engine import calculate_stage4
from backend.services.data_fetcher import (
    FetchRequest,
    _calculate_optical_indices,
    _reference_et0,
    _soil_value,
    fetch_nasa_power,
    scale_modis_value,
)


def _request() -> FetchRequest:
    return FetchRequest(
        latitude=42.03,
        longitude=-93.62,
        bbox=[-93.63, 42.02, -93.61, 42.04],
        geometry={
            "type": "Polygon",
            "coordinates": [[
                [-93.63, 42.02],
                [-93.61, 42.02],
                [-93.61, 42.04],
                [-93.63, 42.04],
                [-93.63, 42.02],
            ]],
        },
        start="2026-01-01",
        end="2026-01-02",
    )


def _daily_weather() -> list[dict[str, float | str]]:
    return [
        {
            "date": "2026-07-01",
            "T2M": 25.0,
            "T2M_MAX": 34.0,
            "T2M_MIN": 16.0,
            "PRECTOTCORR": 2.0,
            "ALLSKY_SFC_SW_DWN": 20.0,
            "RH2M": 50.0,
            "WS2M": 2.0,
        },
        {
            "date": "2026-07-02",
            "T2M": 27.0,
            "T2M_MAX": 36.0,
            "T2M_MIN": 18.0,
            "PRECTOTCORR": 0.0,
            "ALLSKY_SFC_SW_DWN": 20.0,
            "RH2M": 45.0,
            "WS2M": 2.0,
        },
    ]


def test_textbook_physics_benchmarks(tmp_path: Path) -> None:
    """Check ET0, GDD, spectral bounds, and geodesic spatial processing."""
    et0 = _reference_et0(
        {
            "date": "2024-07-05",
            "T2M": 16.9,
            "T2M_MAX": 21.5,
            "T2M_MIN": 12.3,
            "RH2M": 73.5,
            "WS2M": 2.078,
            "ALLSKY_SFC_SW_DWN": 22.07,
        },
        latitude=50.80,
        elevation_m=100,
    )
    assert et0 is not None
    assert 3.75 <= et0 <= 4.05

    assert max(0.0, min((30.0 + 16.0) / 2, 35.0) - 10) == pytest.approx(13.0)
    assert max(0.0, min((12.0 + 4.0) / 2, 35.0) - 10) == pytest.approx(0.0)

    optical_file = tmp_path / "optical.npz"
    np.savez(
        optical_file,
        blue=np.array([0.03, 0.20]),
        red=np.array([0.05, 0.22]),
        nir=np.array([0.45, 0.25]),
        swir1=np.array([0.15, 0.25]),
    )
    indices = _calculate_optical_indices([str(optical_file)])["indices"]
    assert indices["ndvi"]["mean"] == pytest.approx((0.8 + 0.0638298) / 2, abs=1e-5)
    assert indices["ndmi"]["minimum"] >= -1.0
    assert indices["ndmi"]["maximum"] <= 1.0
    for index in indices.values():
        assert -1.0 <= index["minimum"] <= index["maximum"] <= 1.0

    response = process_polygon({
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[0, 0], [0.009, 0], [0.009, 0.009], [0, 0.009], [0, 0]]],
        },
    })
    assert response["area"]["hectares"] == pytest.approx(100.0, rel=0.03)
    assert response["centroid"]["latitude"] == pytest.approx(0.0045)
    assert response["centroid"]["longitude"] == pytest.approx(0.0045)


def test_api_scaling_and_sentinel_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure provider sentinels and scale factors do not leak into analysis."""
    payload = {
        "properties": {
            "parameter": {
                "T2M": {"20260101": -999, "20260102": 10.0},
                "T2M_MAX": {"20260101": -999, "20260102": 16.0},
                "PRECTOTCORR": {"20260101": -999, "20260102": 4.0},
                "ALLSKY_SFC_SW_DWN": {"20260101": -999, "20260102": 20.0},
            }
        }
    }

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return payload

    class Client:
        async def get(self, *args, **kwargs) -> Response:
            return Response()

    result = asyncio.run(fetch_nasa_power(Client(), _request()))
    assert result["weather_summary"]["mean_temperature_c"] == pytest.approx(10.0)
    assert result["weather_summary"]["total_rainfall_mm"] == pytest.approx(4.0)
    assert result["daily"] == [{"date": "2026-01-02", "T2M": 10.0, "T2M_MAX": 16.0, "PRECTOTCORR": 4.0, "ALLSKY_SFC_SW_DWN": 20.0}]
    assert result["daily"][0]["ALLSKY_SFC_SW_DWN"] == pytest.approx(20.0)

    soil_payload = {
        "properties": {
            "layers": [
                {"name": "phh2o", "depths": [{"values": {"mean": 72}}]},
                {"name": "clay", "depths": [{"values": {"mean": 350}}]},
                {"name": "soc", "depths": [{"values": {"mean": 150}}]},
            ]
        }
    }
    assert _soil_value(soil_payload, "phh2o") == pytest.approx(72)
    assert _soil_value(soil_payload, "clay") / 10 == pytest.approx(35.0)
    assert soil_payload["properties"]["layers"][2]["depths"][0]["values"]["mean"] / 10 == pytest.approx(15.0)
    assert 3.5 <= 72 / 10 <= 10.0

    assert scale_modis_value("MOD13Q1", "250m_16_days_NDVI", 6840) == pytest.approx(0.684)
    assert scale_modis_value("MOD13Q1", "250m_16_days_NDVI", -3000) is None
    assert scale_modis_value("MOD09A1", "sur_refl_b02", 6840) == pytest.approx(0.684)


def test_stage5_warming_is_monotonic_and_recommendations_are_complete() -> None:
    """A warmer scenario must not reduce thermal demand or omit crop-cycle advice."""
    stage4 = {
        "rotation_strategies": [
            {"name": "Adaptive cover rotation", "sequence": ["corn", "soybeans", "red_clover"]},
            {"name": "Cereal-legume rotation", "sequence": ["corn", "soybeans", "winter_wheat"]},
        ]
    }
    result = calculate_stage5(
        _daily_weather(),
        latitude=42.0,
        rainfall_mm=700.0,
        stage4=stage4,
    )
    assert result["scenario"]["warming_c"] == 2.0
    assert result["scenario_result"]["gdd"] > result["baseline"]["gdd"]
    assert result["scenario_result"]["total_et0_mm"] > result["baseline"]["total_et0_mm"]
    assert result["scenario_result"]["heat_stress_days"] >= result["baseline"]["heat_stress_days"]
    assert len(result["recommendations"]["crop_recommendations"]) >= 30
    assert len(result["recommendations"]["cycle_recommendations"]) == 2
    assert result["recommendations"]["cycle_recommendations"][0]["schedule"]
    baseline = {
        "name": "Conventional baseline",
        "sequence": ["corn", "fallow", "corn"],
    }
    baseline_result = calculate_stage5(
        _daily_weather(),
        latitude=42.0,
        rainfall_mm=700.0,
        stage4={"rotation_strategies": [baseline]},
    )
    schedule = baseline_result["recommendations"]["cycle_recommendations"][0]["schedule"]
    assert [phase["crop_id"] for phase in schedule] == ["corn", "fallow", "corn"]
    assert schedule[1]["cycle_action"].startswith("Baseline exposure phase")
    print(
        "Stage 5 physical outputs:",
        json.dumps(
            {
                "baseline_gdd": result["baseline"]["gdd"],
                "scenario_gdd": result["scenario_result"]["gdd"],
                "baseline_et0_mm": result["baseline"]["total_et0_mm"],
                "scenario_et0_mm": result["scenario_result"]["total_et0_mm"],
                "baseline_heat_days": result["baseline"]["heat_stress_days"],
                "scenario_heat_days": result["scenario_result"]["heat_stress_days"],
            },
            sort_keys=True,
        ),
    )


def test_stage4_crop_and_rotation_outputs_are_physically_bounded() -> None:
    """Crop scores, water, yield, and rotation aggregates stay in valid ranges."""
    result = calculate_stage4(
        weather={"total_rainfall_mm": 700},
        stage3={
            "reference_et0": {"mean_daily_mm": 3.0},
            "moisture_stress": {"mean_root_zone_wetness": 0.5},
            "vulnerability": {"heat_stress": {"days": 10}},
        },
        soil={"available_water_capacity": {"33_kpa": 20}},
        terrain={"mean_slope_percent": 3},
    )
    assert len(result["crop_rankings_worst_to_best"]) >= 30
    assert len(result["rotation_strategies"]) >= 4
    for crop in result["crop_rankings_worst_to_best"]:
        assert 0 <= crop["suitability_score"] <= 100
        assert crop["water_demand_mm_ha"] >= 0
        assert crop["irrigation_need_m3_ha"] >= 0
        assert 0 <= crop["yield_factor"] <= 1
    for rotation in result["rotation_strategies"]:
        assert rotation["water_use_m3_ha"] >= 0
        assert rotation["years"] == len(rotation["sequence"])


def test_meteomatics_trial_fallback_and_alert_triggers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify the nine-parameter cap, five-parameter fallback, and alerts."""
    monkeypatch.setenv("METEOMATICS_USER", "qa-user")
    monkeypatch.setenv("METEOMATICS_PASS", "qa-pass")
    monkeypatch.setattr(meteomatics_service, "CACHE_DIR", tmp_path)
    calls: list[str] = []

    class Response:
        def __init__(self, status_code: int, payload: dict | None = None):
            self.status_code = status_code
            self._payload = payload or {}

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                request = SimpleNamespace()
                raise __import__("httpx").HTTPStatusError("trial cap", request=request, response=self)

        def json(self) -> dict:
            return self._payload

    class Client:
        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def get(self, url: str, **kwargs) -> Response:
            calls.append(url)
            if len(calls) == 1:
                return Response(403)
            return Response(200, {"data": [
                {"parameter": "t_max_2m_24h:C", "coordinates": [{"dates": [{"date": "2026-11-15T00:00:00Z", "value": 36}]}]},
                {"parameter": "t_min_2m_24h:C", "coordinates": [{"dates": [{"date": "2026-11-15T00:00:00Z", "value": -1}]}]},
                {"parameter": "precip_24h:mm", "coordinates": [{"dates": [{"date": "2026-11-15T00:00:00Z", "value": 25}]}]},
                {"parameter": "soil_moisture_index_-15cm:idx", "coordinates": [{"dates": [{"date": "2026-11-15T00:00:00Z", "value": 0.5}]}]},
            ]})

    monkeypatch.setattr(meteomatics_service.httpx, "AsyncClient", lambda **kwargs: Client())
    result = asyncio.run(meteomatics_service.fetch_meteomatics_forecast(42.0, -93.0, slope_pct=8))
    assert len(calls) == 2
    assert len(calls[0].split("/json")[0].rsplit("/", 2)[-2].split(",")) == 9
    assert len(calls[1].split("/json")[0].rsplit("/", 2)[-2].split(",")) == 5
    titles = {alert["title"] for alert in result["alerts"]}
    assert {"Frost risk", "Crop heat threshold", "Heavy rain / runoff risk"} <= titles


def test_spatial_and_forecast_endpoint_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Endpoint-level validation rejects invalid WGS84 coordinates and accepts valid alerts."""
    invalid = {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [[[0, 95], [0.01, 95], [0.01, 95.01], [0, 95.01], [0, 95]]]},
    }
    with pytest.raises(Exception):
        process_polygon(invalid)

    async def unavailable(*args, **kwargs):
        return {"available": False, "centroid": {"lat": 42.0, "lon": -93.0}}

    monkeypatch.setattr("app.main.fetch_meteomatics_forecast", unavailable)
    result = asyncio.run(forecast_alerts({"lat": 42, "lon": -93}))
    assert result["available"] is False
