from __future__ import annotations

import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from app.main import app


class AccountFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_database = os.environ.get("FIELD_SHIFT_DATABASE")
        os.environ["FIELD_SHIFT_DATABASE"] = os.path.join(self.temp_dir.name, "accounts.sqlite3")
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        if self.previous_database is None:
            os.environ.pop("FIELD_SHIFT_DATABASE", None)
        else:
            os.environ["FIELD_SHIFT_DATABASE"] = self.previous_database
        self.temp_dir.cleanup()

    def register(self, email: str) -> dict:
        response = self.client.post(
            "/api/auth/register",
            json={
                "first_name": "Ari",
                "last_name": "Farmer",
                "email": email,
                "password": "field-shift-password",
                "country": "Tunisia",
                "phone": None,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertNotIn("password_hash", response.json()["user"])
        return response.json()["user"]

    def test_registration_login_and_logout(self) -> None:
        user = self.register("farmer@example.test")
        self.assertEqual(user["email"], "farmer@example.test")
        self.assertEqual(self.client.get("/api/auth/me").status_code, 200)

        self.client.post("/api/auth/logout")
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

        response = self.client.post(
            "/api/auth/login",
            json={"email": "farmer@example.test", "password": "field-shift-password", "remember_me": True},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get("/api/dashboard").json()["farm_count"], 0)

    def test_farms_are_private_to_their_owner(self) -> None:
        self.register("owner@example.test")
        feature = {
            "type": "Feature",
            "properties": {},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [9.18, 36.72],
                    [9.19, 36.72],
                    [9.19, 36.73],
                    [9.18, 36.73],
                    [9.18, 36.72],
                ]],
            },
        }
        created = self.client.post(
            "/api/farms",
            json={"farm_name": "North field", "geojson": feature},
        )
        self.assertEqual(created.status_code, 201, created.text)
        farm_id = created.json()["farm"]["id"]
        saved_analysis = self.client.post(
            "/api/analyses",
            json={
                "farm_id": farm_id,
                "selected_crop": "Wheat",
                "analysis_results": {"suitability": 0.82},
                "climate_scenario": "+2C",
            },
        )
        self.assertEqual(saved_analysis.status_code, 201, saved_analysis.text)
        self.assertEqual(self.client.get("/api/analyses").json()["analyses"][0]["selected_crop"], "Wheat")

        other_client = TestClient(app)
        with other_client:
            response = other_client.get(f"/api/farms/{farm_id}")
        self.assertEqual(response.status_code, 401)

        other_client = TestClient(app)
        with other_client:
            other_client.post(
                "/api/auth/register",
                json={
                    "first_name": "Bea",
                    "last_name": "Farmer",
                    "email": "other@example.test",
                    "password": "another-secure-password",
                    "country": "Tunisia",
                },
            )
            response = other_client.get(f"/api/farms/{farm_id}")
            self.assertEqual(response.status_code, 404)
            self.assertEqual(
                other_client.patch(f"/api/farms/{farm_id}", json={"farm_name": "Stolen"}).status_code,
                404,
            )
            self.assertEqual(other_client.delete(f"/api/farms/{farm_id}").status_code, 404)
            self.assertEqual(
                other_client.post(
                    "/api/analyses",
                    json={"farm_id": farm_id, "analysis_results": {}},
                ).status_code,
                404,
            )
        self.assertEqual(response.status_code, 404)

    def test_duplicate_email_is_rejected(self) -> None:
        self.register("same@example.test")
        response = self.client.post(
            "/api/auth/register",
            json={
                "first_name": "Another",
                "last_name": "User",
                "email": "SAME@example.test",
                "password": "field-shift-password",
                "country": "Tunisia",
            },
        )
        self.assertEqual(response.status_code, 409)

    def test_failed_logins_are_rate_limited(self) -> None:
        self.register("limited@example.test")
        for _ in range(10):
            response = self.client.post(
                "/api/auth/login",
                json={"email": "limited@example.test", "password": "incorrect-password"},
            )
            self.assertEqual(response.status_code, 401)
        response = self.client.post(
            "/api/auth/login",
            json={"email": "limited@example.test", "password": "incorrect-password"},
        )
        self.assertEqual(response.status_code, 429)

    def test_validation_errors_do_not_echo_passwords(self) -> None:
        password = "do-not-echo-this-password"
        response = self.client.post(
            "/api/auth/login",
            json={"email": "not-an-email", "password": password},
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(password, response.text)

    def test_existing_map_page_and_spatial_api_still_work(self) -> None:
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Interactive farm map", page.text)
        response = self.client.post(
            "/api/spatial/process",
            json={
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [9.18, 36.72],
                        [9.19, 36.72],
                        [9.19, 36.73],
                        [9.18, 36.73],
                        [9.18, 36.72],
                    ]],
                },
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertGreater(response.json()["area"]["hectares"], 0)
