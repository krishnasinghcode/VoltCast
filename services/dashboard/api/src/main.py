"""API service entrypoint (dashboardMaster.md Part D.1): mounts routers, CORS, request metrics + rate limiting."""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from . import config, db, rate_limit
from .errors import error_response, install_handlers
from .metrics import publish_safely, request_metrics, retract_safely
from .routers import (alert_router, auth_router, fleet_router, ml_router, observability_router,
                      prediction_router, simulation_router, sql_sandbox_router, vehicle_router, ws_router)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def _publish_metrics_forever() -> None:
    """Each worker pushes its request metrics to Redis so /observability can merge all workers."""
    while True:
        await asyncio.sleep(2)
        await run_in_threadpool(publish_safely, db.get_redis())


@asynccontextmanager
async def lifespan(app: FastAPI):
    ws_router.hub.start()
    publisher = asyncio.create_task(_publish_metrics_forever(), name="metrics-publisher")
    yield
    publisher.cancel()
    await ws_router.hub.stop()
    await run_in_threadpool(retract_safely, db.get_redis())


app = FastAPI(title="CVI Platform API", version=config.CONTRACT_VERSION, lifespan=lifespan)
install_handlers(app)


@app.middleware("http")
async def metrics_and_rate_limit(request: Request, call_next):
    started = time.perf_counter()
    path = request.url.path
    status = 500
    try:
        if request.method != "OPTIONS" and path not in rate_limit.EXEMPT_PATHS and path.startswith("/api/"):
            endpoint = rate_limit.route_template(request)
            who = rate_limit.identity(request, endpoint)
            decision = await run_in_threadpool(rate_limit.check, who, endpoint)
            if not decision.allowed:
                status = 429
                return error_response(
                    429, "RATE_LIMITED", f"rate limit of {decision.limit} requests/minute exceeded",
                    {"limit": decision.limit, "retry_after_s": decision.retry_after},
                    headers={"Retry-After": str(decision.retry_after), "X-RateLimit-Limit": str(decision.limit),
                             "X-RateLimit-Remaining": "0"},
                )
            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(decision.limit)
            response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        else:
            response = await call_next(request)
        status = response.status_code
        return response
    finally:
        if path not in ("/health", "/api/v1/health"):
            request_metrics.record(status, (time.perf_counter() - started) * 1000)


# Added last so it is the outermost middleware: CORS headers also land on 429s and errors.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["X-RateLimit-Limit", "X-RateLimit-Remaining", "Retry-After"],
)


@app.get("/health")
@app.get("/api/v1/health")
def health():
    return {"status": "ok", "contract_version": config.CONTRACT_VERSION, "service": config.SERVICE_NAME}


for r in (auth_router, fleet_router, vehicle_router, alert_router, prediction_router, simulation_router,
          ml_router, observability_router, sql_sandbox_router, ws_router):
    app.include_router(r.router)
