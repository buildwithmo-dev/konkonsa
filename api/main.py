import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import init_db
from services.realtime import start_listener, stop_listener
from routers.sources import router as sources_router
from routers.feed import router as feed_router
from routers.classify import router as classify_router
from routers.trends import router as trends_router
from routers.painpoints import router as painpoints_router
from routers.solutions import router as solutions_router
from routers.misc import (
    search_router,
    clusters_router,
    alerts_router,
    jobs_router,
    analytics_router,
    settings_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await start_listener()
    yield
    await stop_listener()


app = FastAPI(
    title="Konkonsa API",
    description="Social listening + opportunity intelligence pipeline",
    version="1.0.0",
    lifespan=lifespan,
)

frontend_url = os.getenv(
    "FRONTEND_URL",
    "https://konkonsa-frontend-lwf8.vercel.app",
).rstrip("/")

cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", frontend_url).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://konkonsa-frontend-lwf8.vercel.app",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sources_router)
app.include_router(feed_router)
app.include_router(classify_router)
app.include_router(trends_router)
app.include_router(painpoints_router)
app.include_router(solutions_router)

app.include_router(search_router)
app.include_router(clusters_router)
app.include_router(alerts_router)
app.include_router(jobs_router)
app.include_router(analytics_router)
app.include_router(settings_router)


@app.get("/", tags=["Root"])
async def root():
    return {
        "name": "Konkonsa API",
        "docs": "/docs",
        "redoc": "/redoc",
        "health": "/healthz",
    }


@app.get("/healthz", tags=["Health"])
async def healthz():
    return {"status": "ok"}