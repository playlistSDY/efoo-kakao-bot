from __future__ import annotations

import struct
import zlib

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, Response

from app.services.meals.image_cache import meal_image_cache


router = APIRouter()


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", zlib.crc32(chunk_type + data))


def _placeholder_png(width: int = 600, height: int = 300) -> bytes:
    """Build a small neutral PNG without adding a binary asset to the image."""
    signature = b"\x89PNG\r\n\x1a\n"
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    pixel = bytes((238, 242, 247))
    scanline = b"\x00" + pixel * width
    pixels = zlib.compress(scanline * height, level=9)
    return signature + _png_chunk(b"IHDR", header) + _png_chunk(b"IDAT", pixels) + _png_chunk(b"IEND", b"")


PLACEHOLDER_PNG = _placeholder_png()


@router.get("/media/meals/placeholder.png", include_in_schema=False)
def meal_image_placeholder():
    return Response(
        PLACEHOLDER_PNG,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/media/meals/{image_key}", include_in_schema=False)
def meal_image(image_key: str):
    path, content_type, source_url = meal_image_cache.resolve(image_key)
    headers = {"Cache-Control": "public, max-age=86400, stale-while-revalidate=604800"}
    if path:
        return FileResponse(path, media_type=content_type, headers=headers)
    if source_url:
        return RedirectResponse(source_url, status_code=307, headers=headers)
    raise HTTPException(status_code=404, detail="meal image not found")
