from __future__ import annotations

from pydantic import BaseModel, Field


class KakaoRequest(BaseModel):
    userRequest: dict = Field(default_factory=dict)
    action: dict | None = None
    bot: dict | None = None
    contexts: list[dict] | None = None
