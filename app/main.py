from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health, kakao, test
from app.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Efoo 학식 추천 카카오톡 챗봇", lifespan=lifespan)
app.include_router(health.router)
app.include_router(kakao.router)
app.include_router(test.router)
