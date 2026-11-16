"""Raster challenges; no answer is returned by HTTP or stored in plaintext."""

import base64
import io
import secrets
from datetime import timedelta

from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import case, delete, select, text
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.dependencies import token_hash
from app.models import CaptchaChallenge, LoginThrottle, now

COOKIE = "ronghub_captcha"
ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def generate_answer():
    return "".join(secrets.choice(ALPHABET) for _ in range(5))


def image(answer):
    canvas = Image.new("RGB", (190, 66), "#f1f6fc")
    draw = ImageDraw.Draw(canvas)
    for _ in range(60):
        x, y = secrets.randbelow(190), secrets.randbelow(66)
        draw.ellipse((x, y, x + 1, y + 1), fill="#acbdcf")
    for _ in range(3):
        draw.line((0, secrets.randbelow(66), 190, secrets.randbelow(66)), fill="#b3cbe3", width=1)
    font = ImageFont.load_default(size=34)
    for index, char in enumerate(answer):
        layer = Image.new("RGBA", (38, 54))
        ImageDraw.Draw(layer).text((6, 5), char, font=font, fill=(25, 55, 92, 255))
        rotated = layer.rotate(secrets.randbelow(19) - 9, resample=Image.Resampling.BICUBIC)
        canvas.paste(rotated, (index * 35 + 7, secrets.randbelow(5) + 3), rotated)
    output = io.BytesIO()
    canvas.save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def rate(db, request, phase):
    current = now()
    key = token_hash(
        "captcha:" + phase + ":" + (request.client.host if request.client else "unknown")
    )
    expired = LoginThrottle.window_end <= current
    count = db.scalar(
        insert(LoginThrottle)
        .values(key=key, attempts=1, window_end=current + timedelta(minutes=5))
        .on_conflict_do_update(
            index_elements=["key"],
            set_={
                "attempts": case((expired, 1), else_=LoginThrottle.attempts + 1),
                "window_end": case(
                    (expired, current + timedelta(minutes=5)), else_=LoginThrottle.window_end
                ),
            },
        )
        .returning(LoginThrottle.attempts)
    )
    db.commit()
    if count > 60:
        raise HTTPException(429, "验证码操作过于频繁，请稍后重试", headers={"Retry-After": "300"})


def issue(db, request, response):
    rate(db, request, "issue")
    binding = request.cookies.get(COOKIE)
    if not binding or not 40 <= len(binding) <= 100:
        binding = secrets.token_urlsafe(40)
    token, answer = secrets.token_urlsafe(32), generate_answer()
    stamp = now()
    expiry = stamp + timedelta(minutes=2)
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:binding, 63636))"),
        {"binding": binding},
    )
    db.execute(
        delete(CaptchaChallenge).where(CaptchaChallenge.expires_at < stamp - timedelta(hours=1))
    )
    db.execute(
        delete(CaptchaChallenge).where(
            CaptchaChallenge.binding_hash == token_hash(binding),
            CaptchaChallenge.consumed_at.is_(None),
        )
    )
    db.add(
        CaptchaChallenge(
            token_hash=token_hash(token),
            binding_hash=token_hash(binding),
            answer_hash=token_hash(token + ":" + answer),
            expires_at=expiry,
        )
    )
    data = {
        "captcha_id": token,
        "image": image(answer),
        "expires_at": expiry.isoformat(),
        "expires_in_seconds": 120,
    }
    db.commit()
    response.set_cookie(
        COOKIE,
        binding,
        httponly=True,
        secure=not settings.demo_mode,
        samesite="lax",
        path="/api/v1/auth",
        max_age=600,
    )
    response.headers["Cache-Control"] = "no-store"
    return data


def verify(db, request, token, answer):
    rate(db, request, "verify")
    row = db.scalar(
        select(CaptchaChallenge)
        .where(CaptchaChallenge.token_hash == token_hash(token))
        .with_for_update()
    )
    binding = request.cookies.get(COOKIE, "")
    if not row or not binding or not secrets.compare_digest(row.binding_hash, token_hash(binding)):
        raise HTTPException(422, "验证码无效或已过期，请换一张后重试")
    valid = (
        row.consumed_at is None
        and row.expires_at > now()
        and secrets.compare_digest(
            row.answer_hash, token_hash(token + ":" + answer.strip().upper())
        )
    )
    row.consumed_at = row.consumed_at or now()
    db.commit()
    if not valid:
        raise HTTPException(422, "验证码无效或已过期，请换一张后重试")
