from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from database import init_db
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
    yield


app = FastAPI(
    title="Trend Radar API",
    description="Social listening + opportunity intelligence pipeline",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register all routers
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
        "name": "Trend Radar API",
        "docs": "/docs",
        "redoc": "/redoc",
        "health": "/settings/health",
    }
