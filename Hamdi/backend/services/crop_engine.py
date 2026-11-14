"""Backward-compatible Stage 4 facade.

The implementation lives in crop_scorer.py; this module preserves the
historical import and calculate_stage4 contract used by saved lands.
"""

from __future__ import annotations

import json
from pathlib import Path

from .crop_scorer import score_crops

CROP_DATABASE = json.loads((Path(__file__).with_name("crop_database.json")).read_text())
NITROGEN_PRICE_USD_KG = 1.20


def calculate_stage4(*, weather, stage3, soil, terrain):
    return score_crops(weather=weather, stage3=stage3, soil=soil, terrain=terrain)
