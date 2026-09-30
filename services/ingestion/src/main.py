"""FastAPI app exposing /health and /metrics; runs the Kafka consumer as a background task."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import config
from .kafka_consumer import IngestionConsumer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

_consumer: IngestionConsumer | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _consumer
    _consumer = IngestionConsumer()
    _consumer.start()
    yield
    _consumer.stop()


app = FastAPI(title="ingestion", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "contract_version": config.CONTRACT_VERSION}


@app.get("/metrics")
def metrics():
    lag = _consumer.consumer_lag() if _consumer else None
    return _consumer.metrics.snapshot(consumer_lag=lag) if _consumer else {}
