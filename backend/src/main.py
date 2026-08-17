"""
Missile & Splashdown Tracker - Backend
Polls public launch data every 60s and pushes live objects via WebSocket.
Uses only public sources (Launch Library 2 + simulated anomaly feed for demo).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tracker")

app = FastAPI(title="Missile & Splashdown Tracker API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class TrackedObject(BaseModel):
    id: str
    type: str  # "launch" | "missile" | "splashdown" | "anomaly"
    name: str
    status: str
    latitude: float
    longitude: float
    altitude_km: Optional[float] = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.7)
    source: str
    source_url: Optional[str] = None
    description: Optional[str] = None
    first_seen: datetime
    last_updated: datetime
    trajectory: List[Dict[str, Any]] = Field(default_factory=list)
    locked_count: int = 0  # how many clients currently locked on it
    is_abnormal: bool = False
    agency: Optional[str] = None
    vehicle: Optional[str] = None
    net: Optional[datetime] = None  # nominal event time

class SystemState(BaseModel):
    objects: Dict[str, TrackedObject] = Field(default_factory=dict)
    last_poll: Optional[datetime] = None
    poll_count: int = 0
    connected_clients: int = 0

state = SystemState()
connected_websockets: Set[WebSocket] = set()

# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------

LL2_BASE = "https://ll.thespacedevs.com/2.2.0"
POLL_INTERVAL_SEC = 60


async def fetch_launch_library() -> List[TrackedObject]:
    """Fetch recent + upcoming launches from Launch Library 2 (public, free)."""
    objects: List[TrackedObject] = []
    now = datetime.now(timezone.utc)

    async with httpx.AsyncClient(timeout=25.0) as client:
        # Recent successful / failed launches (last ~2 days worth via previous)
        try:
            r = await client.get(
                f"{LL2_BASE}/launch/previous/",
                params={"limit": 12, "format": "json"},
            )
            r.raise_for_status()
            data = r.json()
            for item in data.get("results", []):
                obj = _ll2_to_object(item, now, is_upcoming=False)
                if obj:
                    objects.append(obj)
        except Exception as e:
            logger.warning("LL2 previous failed: %s", e)

        # Upcoming
        try:
            r = await client.get(
                f"{LL2_BASE}/launch/upcoming/",
                params={"limit": 15, "format": "json"},
            )
            r.raise_for_status()
            data = r.json()
            for item in data.get("results", []):
                obj = _ll2_to_object(item, now, is_upcoming=True)
                if obj:
                    objects.append(obj)
        except Exception as e:
            logger.warning("LL2 upcoming failed: %s", e)

    return objects


def _ll2_to_object(item: dict, now: datetime, is_upcoming: bool) -> Optional[TrackedObject]:
    try:
        pad = item.get("pad") or {}
        location = pad.get("location") or {}
        lat = location.get("latitude")
        lon = location.get("longitude")
        if lat is None or lon is None:
            # fallback some known pads
            lat, lon = 28.561, -80.577  # rough KSC default
        status = (item.get("status") or {}).get("name") or "Unknown"
        name = item.get("name") or "Unknown Launch"
        agency = (item.get("launch_service_provider") or {}).get("name")
        rocket = ((item.get("rocket") or {}).get("configuration") or {}).get("full_name")
        net_str = item.get("net")
        net = None
        if net_str:
            try:
                net = datetime.fromisoformat(net_str.replace("Z", "+00:00"))
            except Exception:
                pass

        # Determine type
        obj_type = "launch"
        is_abnormal = False
        # Heuristic: suborbital / test / ballistic keywords often indicate missile-like
        lower_name = name.lower()
        if any(k in lower_name for k in ("missile", "ballistic", "icbm", "irbm", "srm", "test flight", "suborbital")):
            obj_type = "missile"
            is_abnormal = True

        # Splashdown heuristic: successful recovery / landing events sometimes appear in status or mission
        if "splash" in lower_name or "landing" in lower_name or "recovery" in lower_name:
            obj_type = "splashdown"

        description = None
        mission = item.get("mission") or {}
        if mission.get("description"):
            description = mission["description"][:400]

        return TrackedObject(
            id=item.get("id") or str(uuid.uuid4()),
            type=obj_type,
            name=name,
            status=status,
            latitude=float(lat),
            longitude=float(lon),
            confidence=0.85 if status in ("Launch Successful", "Go for Launch", "Success") else 0.65,
            source="Launch Library 2",
            source_url=item.get("url"),
            description=description,
            first_seen=now,
            last_updated=now,
            is_abnormal=is_abnormal,
            agency=agency,
            vehicle=rocket,
            net=net,
        )
    except Exception as e:
        logger.debug("Skip LL2 item: %s", e)
        return None


def generate_demo_anomalies(now: datetime) -> List[TrackedObject]:
    """
    Generate a few realistic-looking demo anomaly objects so the map always has
    interesting live items even when public feeds are quiet.
    These are clearly marked as demo / simulated.
    """
    anomalies = [
        {
            "id": "demo-anomaly-nk-test",
            "type": "missile",
            "name": "[DEMO] Suspected Ballistic Test – East Sea",
            "status": "Unconfirmed Report",
            "lat": 39.0,
            "lon": 127.5,
            "conf": 0.45,
            "desc": "Simulated OSINT-style report of a possible short-range ballistic missile launch. For demonstration of abnormality highlighting and lock-on only.",
            "agency": "Unknown",
            "vehicle": "SRBM-class (simulated)",
        },
        {
            "id": "demo-splash-atlantic",
            "type": "splashdown",
            "name": "[DEMO] Stage Splashdown – Atlantic Recovery Zone",
            "status": "Reported Splashdown",
            "lat": 28.5,
            "lon": -74.0,
            "conf": 0.6,
            "desc": "Simulated booster/stage splashdown event in a typical Atlantic recovery area. Demonstrates splashdown object type and tracking.",
            "agency": "Commercial",
            "vehicle": "First Stage (simulated)",
        },
        {
            "id": "demo-anomaly-blacksea",
            "type": "anomaly",
            "name": "[DEMO] Unidentified Trajectory Alert – Black Sea",
            "status": "Monitoring",
            "lat": 44.5,
            "lon": 33.0,
            "conf": 0.35,
            "desc": "Simulated multi-source anomaly flag for demonstration of the abnormality detection pipeline and live map listing.",
            "agency": None,
            "vehicle": None,
        },
    ]

    objs = []
    for a in anomalies:
        objs.append(
            TrackedObject(
                id=a["id"],
                type=a["type"],
                name=a["name"],
                status=a["status"],
                latitude=a["lat"],
                longitude=a["lon"],
                confidence=a["conf"],
                source="Demo / Simulated Feed",
                description=a["desc"],
                first_seen=now - timedelta(minutes=12),
                last_updated=now,
                is_abnormal=True,
                agency=a.get("agency"),
                vehicle=a.get("vehicle"),
            )
        )
    return objs


# ---------------------------------------------------------------------------
# Core polling & broadcast logic
# ---------------------------------------------------------------------------

async def poll_and_update():
    """Main loop: fetch data every 60s, merge into state, broadcast."""
    while True:
        try:
            now = datetime.now(timezone.utc)
            logger.info("Polling sources...")

            new_objects = await fetch_launch_library()
            # Always include demo anomalies so the UI has live abnormal objects
            new_objects.extend(generate_demo_anomalies(now))

            # Merge: keep existing, update or add
            seen_ids = set()
            for obj in new_objects:
                seen_ids.add(obj.id)
                if obj.id in state.objects:
                    # Update mutable fields
                    existing = state.objects[obj.id]
                    existing.status = obj.status
                    existing.last_updated = now
                    existing.confidence = obj.confidence
                    existing.latitude = obj.latitude
                    existing.longitude = obj.longitude
                    if obj.description:
                        existing.description = obj.description
                else:
                    state.objects[obj.id] = obj
                    logger.info("New object: %s (%s)", obj.name, obj.type)

            # Optional: age out very old objects (keep last 48h of activity)
            cutoff = now - timedelta(hours=48)
            to_remove = [
                oid for oid, o in state.objects.items()
                if o.last_updated < cutoff and not oid.startswith("demo-")
            ]
            for oid in to_remove:
                del state.objects[oid]

            state.last_poll = now
            state.poll_count += 1

            await broadcast_state()
            logger.info(
                "Poll complete. Objects: %d  Clients: %d",
                len(state.objects),
                len(connected_websockets),
            )
        except Exception as e:
            logger.exception("Poll error: %s", e)

        await asyncio.sleep(POLL_INTERVAL_SEC)


async def broadcast_state():
    if not connected_websockets:
        return
    payload = {
        "type": "state",
        "last_poll": state.last_poll.isoformat() if state.last_poll else None,
        "poll_count": state.poll_count,
        "objects": [o.model_dump(mode="json") for o in state.objects.values()],
    }
    dead = set()
    for ws in connected_websockets:
        try:
            await ws.send_json(payload)
        except Exception:
            dead.add(ws)
    for ws in dead:
        connected_websockets.discard(ws)
    state.connected_clients = len(connected_websockets)


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "objects": len(state.objects),
        "last_poll": state.last_poll.isoformat() if state.last_poll else None,
        "clients": len(connected_websockets),
        "poll_count": state.poll_count,
    }


@app.get("/api/objects")
async def get_objects():
    return {
        "last_poll": state.last_poll.isoformat() if state.last_poll else None,
        "objects": [o.model_dump(mode="json") for o in state.objects.values()],
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_websockets.add(websocket)
    state.connected_clients = len(connected_websockets)
    logger.info("Client connected. Total: %d", state.connected_clients)

    # Immediately send current state
    try:
        await websocket.send_json({
            "type": "state",
            "last_poll": state.last_poll.isoformat() if state.last_poll else None,
            "poll_count": state.poll_count,
            "objects": [o.model_dump(mode="json") for o in state.objects.values()],
        })
    except Exception:
        pass

    try:
        while True:
            # Keep alive / receive lock events from client
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "lock" and msg.get("id"):
                    oid = msg["id"]
                    if oid in state.objects:
                        state.objects[oid].locked_count += 1
                        await broadcast_state()
                elif msg.get("type") == "unlock" and msg.get("id"):
                    oid = msg["id"]
                    if oid in state.objects:
                        state.objects[oid].locked_count = max(0, state.objects[oid].locked_count - 1)
                        await broadcast_state()
            except Exception:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        connected_websockets.discard(websocket)
        state.connected_clients = len(connected_websockets)
        logger.info("Client disconnected. Total: %d", state.connected_clients)


@app.on_event("startup")
async def startup_event():
    # Kick off the polling loop
    asyncio.create_task(poll_and_update())
    # Do one immediate poll
    asyncio.create_task(_initial_poll())


async def _initial_poll():
    await asyncio.sleep(1)
    # Force first poll quickly
    now = datetime.now(timezone.utc)
    objs = await fetch_launch_library()
    objs.extend(generate_demo_anomalies(now))
    for o in objs:
        state.objects[o.id] = o
    state.last_poll = now
    state.poll_count = 1
    await broadcast_state()
    logger.info("Initial poll done – %d objects", len(state.objects))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
