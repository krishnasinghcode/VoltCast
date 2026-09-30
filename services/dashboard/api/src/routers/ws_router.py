"""WS /ws/live — polling-based live updates (dashboardMaster.md §6).

Auth: JWT passed as `?token=` — a browser WebSocket cannot set an Authorization header, so this is the one
documented exception to header auth. Invalid/expired token -> close code 4401.

Honest design note: this is poll-and-push, not pub/sub. One background task (LiveHub) wakes every
WS_PUSH_INTERVAL_SECONDS, reads the fleet summary (10 s Redis cache) + latest alerts once, then sends each
connected client that shared snapshot plus the Redis latest-state hashes of the vehicles that client
subscribed to. Client -> server messages:
    {"action": "subscribe", "vehicle_ids": ["EV-000000001", ...]}   (replaces the set, max 50)
    {"action": "ping"}
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from .. import config, db
from ..auth import decode_token
from ..errors import ApiError
from .alert_router import list_alerts_query
from .fleet_router import get_fleet_summary
from .vehicle_router import parse_redis_state

log = logging.getLogger(__name__)
router = APIRouter(tags=["live"])

MAX_SUBSCRIPTIONS = 50


class LiveHub:
    def __init__(self) -> None:
        self.clients: dict[WebSocket, set[str]] = {}
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="live-hub")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def add(self, ws: WebSocket) -> None:
        async with self._lock:
            self.clients[ws] = set()

    async def remove(self, ws: WebSocket) -> None:
        async with self._lock:
            self.clients.pop(ws, None)

    async def subscribe(self, ws: WebSocket, vehicle_ids: list[str]) -> None:
        async with self._lock:
            if ws in self.clients:
                self.clients[ws] = {str(v) for v in vehicle_ids[:MAX_SUBSCRIPTIONS] if v}

    @staticmethod
    def _shared_snapshot() -> dict:
        snapshot: dict = {"type": "live", "ts": datetime.now(timezone.utc).isoformat()}
        try:
            snapshot["fleet_summary"] = get_fleet_summary(None)
        except Exception as exc:
            log.warning("live: fleet summary failed: %s", exc)
            snapshot["fleet_summary"] = None
        try:
            snapshot["recent_alerts"] = list_alerts_query(limit=10)["items"]
        except Exception as exc:
            log.warning("live: alerts failed: %s", exc)
            snapshot["recent_alerts"] = []
        return snapshot

    @staticmethod
    def _vehicle_states(vehicle_ids: set[str]) -> dict:
        if not vehicle_ids:
            return {}
        ids = sorted(vehicle_ids)
        try:
            pipe = db.get_redis().pipeline(transaction=False)
            for vid in ids:
                pipe.hgetall(f"vehicle:state:{vid}")
                pipe.smembers(f"vehicle:alert_flags:{vid}")
            results = pipe.execute()
        except Exception as exc:
            log.warning("live: redis states failed: %s", exc)
            return {}
        out = {}
        for idx, vid in enumerate(ids):
            state, flags = results[2 * idx], results[2 * idx + 1]
            if state or flags:
                out[vid] = {**parse_redis_state(state or {}), "alert_flags": sorted(flags or [])}
        return out

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.sleep(config.WS_PUSH_INTERVAL_SECONDS)
                async with self._lock:
                    clients = list(self.clients.items())
                if not clients:
                    continue
                shared = await run_in_threadpool(self._shared_snapshot)
                wanted = set().union(*(subs for _, subs in clients))
                states = await run_in_threadpool(self._vehicle_states, wanted)
                for ws, subs in clients:
                    payload = {**shared, "vehicle_states": {v: states[v] for v in subs if v in states}}
                    try:
                        await ws.send_json(payload)
                    except Exception:
                        await self.remove(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # never let the broadcaster die
                log.exception("live hub iteration failed: %s", exc)


hub = LiveHub()


@router.websocket("/ws/live")
async def live(websocket: WebSocket, token: Optional[str] = None):
    await websocket.accept()
    try:
        user = decode_token(token or "")
    except ApiError:
        await websocket.send_json({"type": "error", "error": {"code": "UNAUTHORIZED",
                                                              "message": "invalid or expired token", "details": {}}})
        await websocket.close(code=4401)
        return

    await hub.add(websocket)
    hub.start()
    try:
        # Immediate first frame so the UI doesn't wait a full interval.
        shared = await run_in_threadpool(hub._shared_snapshot)
        await websocket.send_json({**shared, "vehicle_states": {}, "user": {"role": user.role}})
        while True:
            msg = await websocket.receive_json()
            action = msg.get("action") if isinstance(msg, dict) else None
            if action == "subscribe":
                ids = msg.get("vehicle_ids") or []
                if isinstance(ids, list):
                    await hub.subscribe(websocket, ids)
                    states = await run_in_threadpool(hub._vehicle_states, set(ids[:MAX_SUBSCRIPTIONS]))
                    await websocket.send_json({"type": "vehicle_states", "vehicle_states": states})
            elif action == "ping":
                await websocket.send_json({"type": "pong", "ts": datetime.now(timezone.utc).isoformat()})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.info("live socket closed: %s", exc)
    finally:
        await hub.remove(websocket)
