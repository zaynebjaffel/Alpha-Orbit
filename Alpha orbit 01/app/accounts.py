from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import smtplib
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field
from pyproj import CRS, Transformer
from shapely.geometry import shape
from shapely.ops import transform

from app.database import database_path, get_db

router = APIRouter(prefix="/api")
password_hasher = PasswordHasher()
SESSION_COOKIE = "field_shift_session"
SESSION_SECONDS = 60 * 60 * 24
REMEMBER_SECONDS = 60 * 60 * 24 * 30
RESET_SECONDS = 60 * 30
MAX_AVATAR_BYTES = 2 * 1024 * 1024
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^[0-9+().\-\s]{0,30}$")


class RegisterInput(BaseModel):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)
    country: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=30)


class LoginInput(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)
    remember_me: bool = False


class ProfileInput(BaseModel):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    country: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=30)


class PasswordInput(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class ForgotPasswordInput(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class ResetPasswordInput(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: str = Field(min_length=12, max_length=128)


class FarmInput(BaseModel):
    farm_name: str = Field(min_length=1, max_length=100)
    geojson: dict[str, Any]


class FarmUpdateInput(BaseModel):
    farm_name: str | None = Field(default=None, min_length=1, max_length=100)
    geojson: dict[str, Any] | None = None


class AnalysisInput(BaseModel):
    farm_id: str = Field(min_length=1, max_length=80)
    selected_crop: str | None = Field(default=None, max_length=100)
    analysis_results: dict[str, Any]
    climate_scenario: str | None = Field(default=None, max_length=100)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_email(value: str) -> str:
    email = value.strip().lower()
    if not EMAIL_RE.fullmatch(email):
        raise HTTPException(422, "Enter a valid email address.")
    return email


def _clean_text(value: str, field: str) -> str:
    value = value.strip()
    if not value:
        raise HTTPException(422, f"{field} is required.")
    return value


def _validate_phone(phone: str | None) -> str | None:
    if phone is None or not phone.strip():
        return None
    value = phone.strip()
    if not PHONE_RE.fullmatch(value):
        raise HTTPException(422, "Enter a valid phone number.")
    return value


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _rate_key(value: str) -> str:
    return _token_hash(value)


def _rate_limit(db: sqlite3.Connection, key: str, maximum: int, window: int) -> None:
    now = int(time.time())
    row = db.execute(
        "SELECT window_started, attempts FROM auth_rate_limits WHERE rate_key = ?",
        (_rate_key(key),),
    ).fetchone()
    if row and now - row["window_started"] < window:
        if row["attempts"] >= maximum:
            raise HTTPException(429, "Too many attempts. Please try again later.")
        db.execute(
            "UPDATE auth_rate_limits SET attempts = attempts + 1 WHERE rate_key = ?",
            (_rate_key(key),),
        )
    else:
        db.execute(
            """
            INSERT INTO auth_rate_limits(rate_key, window_started, attempts)
            VALUES (?, ?, 1)
            ON CONFLICT(rate_key) DO UPDATE SET window_started = excluded.window_started, attempts = 1
            """,
            (_rate_key(key), now),
        )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _secure_cookie() -> bool:
    default = "true" if os.getenv("FIELD_SHIFT_ENV", "").strip().lower() == "production" else "false"
    return os.getenv("FIELD_SHIFT_COOKIE_SECURE", default).strip().lower() in {"1", "true", "yes"}


def _check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host"):
        raise HTTPException(403, "Cross-origin requests are not allowed.")


def _public_user(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "email": row["email"],
        "country": row["country"],
        "phone": row["phone"],
        "has_profile_picture": bool(row["profile_picture"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _set_session(response: Response, user_id: str, remember_me: bool) -> None:
    token = secrets.token_urlsafe(32)
    lifetime = REMEMBER_SECONDS if remember_me else SESSION_SECONDS
    now = int(time.time())
    with get_db() as db:
        db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        db.execute(
            "INSERT INTO sessions(id, user_id, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), user_id, _token_hash(token), now + lifetime, _now()),
        )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=lifetime,
        httponly=True,
        secure=_secure_cookie(),
        samesite="lax",
        path="/",
    )


def get_current_user(request: Request) -> dict[str, Any]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, "Please sign in to continue.")
    with get_db() as db:
        row = db.execute(
            """
            SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ? AND s.expires_at > ?
            """,
            (_token_hash(token), int(time.time())),
        ).fetchone()
    if not row:
        raise HTTPException(401, "Your session has expired. Please sign in again.")
    return _public_user(row)


def _owned_farm(db: sqlite3.Connection, farm_id: str, user_id: str) -> sqlite3.Row:
    row = db.execute(
        "SELECT * FROM farms WHERE id = ? AND user_id = ?",
        (farm_id, user_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Farm not found.")
    return row


def _farm_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "farm_name": row["farm_name"],
        "geojson": json.loads(row["geojson"]),
        "centroid_latitude": row["centroid_latitude"],
        "centroid_longitude": row["centroid_longitude"],
        "area_hectares": row["area_hectares"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _validated_polygon(feature: dict[str, Any]) -> tuple[dict[str, Any], float, float, float]:
    if feature.get("type") != "Feature" or not isinstance(feature.get("geometry"), dict):
        raise HTTPException(422, "Farm boundary must be a GeoJSON Feature.")
    try:
        geometry = shape(feature["geometry"])
    except (TypeError, ValueError) as error:
        raise HTTPException(422, "Farm boundary contains invalid GeoJSON coordinates.") from error
    if geometry.geom_type != "Polygon" or geometry.is_empty or not geometry.is_valid or geometry.area == 0:
        raise HTTPException(422, "Farm boundary must be a valid, non-empty Polygon.")
    coordinates = feature["geometry"].get("coordinates", [])
    for ring in coordinates:
        for coordinate in ring:
            if (
                len(coordinate) < 2
                or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in coordinate[:2])
                or not -180 <= coordinate[0] <= 180
                or not -90 <= coordinate[1] <= 90
            ):
                raise HTTPException(422, "Coordinates must be valid WGS84 longitude/latitude values.")
    centroid = geometry.centroid
    zone = min(60, max(1, int((centroid.x + 180) // 6) + 1))
    epsg = (32600 if centroid.y >= 0 else 32700) + zone
    projected = transform(
        Transformer.from_crs(CRS.from_epsg(4326), CRS.from_epsg(epsg), always_xy=True).transform,
        geometry,
    )
    clean_feature = {
        "type": "Feature",
        "properties": feature.get("properties") if isinstance(feature.get("properties"), dict) else {},
        "geometry": geometry.__geo_interface__,
    }
    return clean_feature, centroid.y, centroid.x, projected.area / 10_000


def _avatar_dir() -> Path:
    path = database_path().parent / "avatars"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _avatar_extension(content: bytes) -> str | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return ".webp"
    return None


def _send_reset_email(address: str, token: str, base_url: str) -> None:
    host = os.getenv("FIELD_SHIFT_SMTP_HOST")
    sender = os.getenv("FIELD_SHIFT_SMTP_FROM")
    if not host or not sender:
        raise HTTPException(503, "Password reset email is not configured on this server.")
    port = int(os.getenv("FIELD_SHIFT_SMTP_PORT", "587"))
    username = os.getenv("FIELD_SHIFT_SMTP_USERNAME")
    password = os.getenv("FIELD_SHIFT_SMTP_PASSWORD")
    use_tls = os.getenv("FIELD_SHIFT_SMTP_STARTTLS", "true").strip().lower() in {"1", "true", "yes"}
    reset_url = f"{base_url.rstrip('/')}/#/reset-password?token={token}"
    message = EmailMessage()
    message["Subject"] = "Reset your Field Shift password"
    message["From"] = sender
    message["To"] = address
    message.set_content(f"Use this link within 30 minutes to reset your password:\n\n{reset_url}\n")
    try:
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            if use_tls:
                smtp.starttls()
            if username:
                smtp.login(username, password or "")
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as error:
        raise HTTPException(502, "Password reset email could not be sent.") from error


@router.post("/auth/register", status_code=201)
def register(payload: RegisterInput, request: Request, response: Response) -> dict[str, Any]:
    _check_origin(request)
    email = _normalize_email(payload.email)
    first_name = _clean_text(payload.first_name, "First name")
    last_name = _clean_text(payload.last_name, "Last name")
    country = _clean_text(payload.country, "Country")
    phone = _validate_phone(payload.phone)
    with get_db() as db:
        _rate_limit(db, f"register:{_client_ip(request)}", 5, 3600)
        if db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            db.commit()
            raise HTTPException(409, "An account with this email already exists.")
        user_id = str(uuid.uuid4())
        now = _now()
        try:
            db.execute(
                """
                INSERT INTO users(id, first_name, last_name, email, password_hash, country, phone, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, first_name, last_name, email, password_hasher.hash(payload.password), country, phone, now, now),
            )
        except sqlite3.IntegrityError as error:
            raise HTTPException(409, "An account with this email already exists.") from error
    _set_session(response, user_id, False)
    with get_db() as db:
        user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return {"user": _public_user(user)}


@router.post("/auth/login")
def login(payload: LoginInput, request: Request, response: Response) -> dict[str, Any]:
    _check_origin(request)
    email = _normalize_email(payload.email)
    with get_db() as db:
        _rate_limit(db, f"login:{_client_ip(request)}:{email}", 10, 900)
        row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        valid = False
        if row:
            try:
                valid = password_hasher.verify(row["password_hash"], payload.password)
            except (VerifyMismatchError, InvalidHashError):
                valid = False
        if not valid:
            db.commit()
            raise HTTPException(401, "Email or password is incorrect.")
        user = _public_user(row)
    _set_session(response, user["id"], payload.remember_me)
    return {"user": user}


@router.post("/auth/logout")
def logout(request: Request, response: Response) -> dict[str, bool]:
    _check_origin(request)
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with get_db() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, secure=_secure_cookie(), samesite="lax")
    return {"logged_out": True}


@router.get("/auth/me")
def who_am_i(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return {"user": user}


@router.post("/auth/forgot-password")
def forgot_password(payload: ForgotPasswordInput, request: Request) -> dict[str, str]:
    _check_origin(request)
    email = _normalize_email(payload.email)
    if not os.getenv("FIELD_SHIFT_SMTP_HOST") or not os.getenv("FIELD_SHIFT_SMTP_FROM"):
        raise HTTPException(503, "Password reset email is not configured on this server.")
    configured_url = os.getenv("FIELD_SHIFT_PUBLIC_URL")
    production = os.getenv("FIELD_SHIFT_ENV", "").strip().lower() == "production"
    if production and not configured_url:
        raise HTTPException(503, "Password reset email is not configured on this server.")
    base_url = configured_url or str(request.base_url)
    parsed_url = urlsplit(base_url)
    if (
        parsed_url.scheme not in {"http", "https"}
        or not parsed_url.netloc
        or parsed_url.username
        or parsed_url.password
        or (production and parsed_url.scheme != "https")
    ):
        raise HTTPException(503, "Password reset email is not configured on this server.")
    with get_db() as db:
        _rate_limit(db, f"forgot:{_client_ip(request)}", 3, 3600)
        user = db.execute("SELECT id, email FROM users WHERE email = ?", (email,)).fetchone()
        if not user:
            return {"message": "If an account exists for that email, a reset link will be sent."}
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        db.execute("DELETE FROM password_resets WHERE expires_at <= ?", (now,))
        db.execute(
            "INSERT INTO password_resets(token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (_token_hash(token), user["id"], now + RESET_SECONDS, _now()),
        )
    _send_reset_email(user["email"], token, base_url)
    return {"message": "If an account exists for that email, a reset link will be sent."}


@router.post("/auth/reset-password")
def reset_password(payload: ResetPasswordInput, request: Request) -> dict[str, bool]:
    _check_origin(request)
    with get_db() as db:
        reset = db.execute(
            "SELECT user_id FROM password_resets WHERE token_hash = ? AND expires_at > ?",
            (_token_hash(payload.token), int(time.time())),
        ).fetchone()
        if not reset:
            raise HTTPException(400, "This password reset link is invalid or expired.")
        db.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (password_hasher.hash(payload.new_password), _now(), reset["user_id"]),
        )
        db.execute("DELETE FROM password_resets WHERE user_id = ?", (reset["user_id"],))
        db.execute("DELETE FROM sessions WHERE user_id = ?", (reset["user_id"],))
    return {"password_reset": True}


@router.get("/profile")
def get_profile(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return {"user": user}


@router.patch("/profile")
def update_profile(
    payload: ProfileInput,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    _check_origin(request)
    first_name = _clean_text(payload.first_name, "First name")
    last_name = _clean_text(payload.last_name, "Last name")
    country = _clean_text(payload.country, "Country")
    phone = _validate_phone(payload.phone)
    with get_db() as db:
        db.execute(
            "UPDATE users SET first_name = ?, last_name = ?, country = ?, phone = ?, updated_at = ? WHERE id = ?",
            (first_name, last_name, country, phone, _now(), user["id"]),
        )
        row = db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
    return {"user": _public_user(row)}


@router.post("/profile/password")
def change_password(
    payload: PasswordInput,
    request: Request,
    response: Response,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, bool]:
    _check_origin(request)
    with get_db() as db:
        row = db.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
        try:
            valid = password_hasher.verify(row["password_hash"], payload.current_password)
        except (VerifyMismatchError, InvalidHashError):
            valid = False
        if not valid:
            raise HTTPException(400, "Current password is incorrect.")
        db.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (password_hasher.hash(payload.new_password), _now(), user["id"]),
        )
        db.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, secure=_secure_cookie(), samesite="lax")
    return {"password_changed": True}


@router.post("/profile/avatar")
async def upload_avatar(
    request: Request,
    file: UploadFile = File(...),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, bool]:
    _check_origin(request)
    content = await file.read(MAX_AVATAR_BYTES + 1)
    extension = _avatar_extension(content)
    if len(content) > MAX_AVATAR_BYTES or not extension:
        raise HTTPException(422, "Upload a PNG, JPEG, or WebP image no larger than 2 MB.")
    filename = f"{user['id']}-{uuid.uuid4().hex}{extension}"
    avatar_path = _avatar_dir() / filename
    avatar_path.write_bytes(content)
    with get_db() as db:
        row = db.execute("SELECT profile_picture FROM users WHERE id = ?", (user["id"],)).fetchone()
        db.execute(
            "UPDATE users SET profile_picture = ?, updated_at = ? WHERE id = ?",
            (filename, _now(), user["id"]),
        )
    if row["profile_picture"]:
        old_path = _avatar_dir() / Path(row["profile_picture"]).name
        if old_path.parent == _avatar_dir() and old_path.exists():
            old_path.unlink()
    return {"uploaded": True}


@router.get("/profile/avatar")
def get_avatar(user: dict[str, Any] = Depends(get_current_user)) -> Response:
    from fastapi.responses import FileResponse

    with get_db() as db:
        row = db.execute("SELECT profile_picture FROM users WHERE id = ?", (user["id"],)).fetchone()
    if not row["profile_picture"]:
        raise HTTPException(404, "No profile picture has been uploaded.")
    path = _avatar_dir() / Path(row["profile_picture"]).name
    if not path.is_file():
        raise HTTPException(404, "Profile picture not found.")
    return FileResponse(path)


@router.get("/dashboard")
def dashboard(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    with get_db() as db:
        farm_count = db.execute("SELECT COUNT(*) FROM farms WHERE user_id = ?", (user["id"],)).fetchone()[0]
        analysis_count = db.execute(
            "SELECT COUNT(*) FROM farm_analyses WHERE user_id = ?",
            (user["id"],),
        ).fetchone()[0]
        latest_farms = db.execute(
            "SELECT id, farm_name, area_hectares, created_at FROM farms WHERE user_id = ? ORDER BY created_at DESC LIMIT 5",
            (user["id"],),
        ).fetchall()
    return {
        "user": user,
        "farm_count": farm_count,
        "analysis_count": analysis_count,
        "farms": [dict(row) for row in latest_farms],
    }


@router.get("/farms")
def list_farms(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM farms WHERE user_id = ? ORDER BY created_at DESC",
            (user["id"],),
        ).fetchall()
    return {"farms": [_farm_dict(row) for row in rows]}


@router.post("/farms", status_code=201)
def create_farm(
    payload: FarmInput,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    _check_origin(request)
    farm_name = _clean_text(payload.farm_name, "Farm name")
    feature, latitude, longitude, area_hectares = _validated_polygon(payload.geojson)
    farm_id = str(uuid.uuid4())
    now = _now()
    with get_db() as db:
        db.execute(
            """
            INSERT INTO farms(id, user_id, farm_name, geojson, centroid_latitude, centroid_longitude,
                              area_hectares, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (farm_id, user["id"], farm_name, json.dumps(feature), latitude, longitude, area_hectares, now, now),
        )
        row = db.execute("SELECT * FROM farms WHERE id = ?", (farm_id,)).fetchone()
    return {"farm": _farm_dict(row)}


@router.get("/farms/{farm_id}")
def get_farm(farm_id: str, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    with get_db() as db:
        return {"farm": _farm_dict(_owned_farm(db, farm_id, user["id"]))}


@router.patch("/farms/{farm_id}")
def update_farm(
    farm_id: str,
    payload: FarmUpdateInput,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    _check_origin(request)
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(422, "Provide a farm name or boundary to update.")
    with get_db() as db:
        existing = _owned_farm(db, farm_id, user["id"])
        name = existing["farm_name"]
        feature = json.loads(existing["geojson"])
        latitude = existing["centroid_latitude"]
        longitude = existing["centroid_longitude"]
        area_hectares = existing["area_hectares"]
        if "farm_name" in updates:
            name = _clean_text(updates["farm_name"] or "", "Farm name")
        if "geojson" in updates:
            feature, latitude, longitude, area_hectares = _validated_polygon(updates["geojson"])
        db.execute(
            """
            UPDATE farms SET farm_name = ?, geojson = ?, centroid_latitude = ?, centroid_longitude = ?,
                             area_hectares = ?, updated_at = ? WHERE id = ? AND user_id = ?
            """,
            (name, json.dumps(feature), latitude, longitude, area_hectares, _now(), farm_id, user["id"]),
        )
        row = _owned_farm(db, farm_id, user["id"])
    return {"farm": _farm_dict(row)}


@router.delete("/farms/{farm_id}", status_code=204)
def delete_farm(
    farm_id: str,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    _check_origin(request)
    with get_db() as db:
        _owned_farm(db, farm_id, user["id"])
        db.execute("DELETE FROM farms WHERE id = ? AND user_id = ?", (farm_id, user["id"]))
    return Response(status_code=204)


@router.get("/analyses")
def list_analyses(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT a.id, a.farm_id, f.farm_name, a.selected_crop, a.analysis_results,
                   a.climate_scenario, a.created_at
            FROM farm_analyses a JOIN farms f ON f.id = a.farm_id
            WHERE a.user_id = ? ORDER BY a.created_at DESC
            """,
            (user["id"],),
        ).fetchall()
    return {
        "analyses": [
            {
                **{key: row[key] for key in ("id", "farm_id", "farm_name", "selected_crop", "climate_scenario", "created_at")},
                "analysis_results": json.loads(row["analysis_results"]),
            }
            for row in rows
        ]
    }


@router.post("/analyses", status_code=201)
def save_analysis(
    payload: AnalysisInput,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, str]:
    _check_origin(request)
    analysis_id = str(uuid.uuid4())
    with get_db() as db:
        _owned_farm(db, payload.farm_id, user["id"])
        db.execute(
            """
            INSERT INTO farm_analyses(id, user_id, farm_id, selected_crop, analysis_results, climate_scenario, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                analysis_id,
                user["id"],
                payload.farm_id,
                payload.selected_crop.strip() if payload.selected_crop else None,
                json.dumps(payload.analysis_results),
                payload.climate_scenario.strip() if payload.climate_scenario else None,
                _now(),
            ),
        )
    return {"id": analysis_id}
