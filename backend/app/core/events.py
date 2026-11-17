import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

import redis
import redis.asyncio as aioredis

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def job_channel(job_id: str) -> str:
    return f"job_events:{job_id}"


def publish_job_event(job_id: str, event_type: str, data: dict) -> None:
    """Publish a job progress/status event to Redis Pub/Sub from synchronous worker/API code."""
    settings = get_settings()
    payload = {
        "event": event_type,
        "job_id": job_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **data,
    }
    try:
        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        client.publish(job_channel(job_id), json.dumps(payload, ensure_ascii=False))
        client.close()
    except Exception as exc:
        logger.warning("Failed to publish Redis job event for %s: %s", job_id, exc)


async def subscribe_job_events(job_id: str) -> AsyncGenerator[str, None]:
    """Yield Server-Sent Events lines for a job until completion or client disconnect."""
    settings = get_settings()
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    pubsub = client.pubsub()
    await pubsub.subscribe(job_channel(job_id))
    try:
        while True:
            try:
                message = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                    timeout=15.0,
                )
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            if message is None:
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)
                continue
            raw_data = message.get("data")
            if not raw_data:
                continue
            try:
                parsed = json.loads(raw_data)
                event_name = parsed.get("event", "status")
                yield f"event: {event_name}\ndata: {raw_data}\n\n"
                if parsed.get("status") in ("completed", "failed", "cancelled"):
                    break
            except json.JSONDecodeError:
                yield f"data: {raw_data}\n\n"
    finally:
        await pubsub.unsubscribe(job_channel(job_id))
        await pubsub.close()
        await client.aclose()

