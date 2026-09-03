from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from app.services.meals.image_cache import meal_image_cache


router = APIRouter()


@router.get("/media/meals/{image_key}", include_in_schema=False)
def meal_image(image_key: str):
    path, content_type, source_url = meal_image_cache.resolve(image_key)
    headers = {"Cache-Control": "public, max-age=86400, stale-while-revalidate=604800"}
    if path:
        return FileResponse(path, media_type=content_type, headers=headers)
    if source_url:
        return RedirectResponse(source_url, status_code=307, headers=headers)
    raise HTTPException(status_code=404, detail="meal image not found")
