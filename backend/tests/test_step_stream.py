"""Workflow timeline API (Phase 2): the step list, scoped stream tokens,
and the SSE stream (existing rows first, live events, heartbeat, end).

Route tests drive the real app against the real database, following
`test_reports_routes.py`; every user created is deleted afterwards."""

import json
import uuid
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import delete

from app.database.session import async_session_factory
from app.main import app
from app.modules.auth.models import User
from app.modules.auth.security import create_stream_token, decode_stream_token
from app.modules.research.enums import ResearchRunStatus, ResearchStepStatus
from app.modules.research.models import ResearchRun, ResearchStep
from app.modules.research import step_stream
from app.modules.research.step_stream import step_event_stream

PASSWORD = "correct-horse-battery"


def test_stream_token_is_scoped_to_one_resource():
    user_id = uuid.uuid4()
    token = create_stream_token(user_id, "run:abc")
    assert decode_stream_token(token, resource="run:abc") == user_id
    with pytest.raises(HTTPException) as info:
        decode_stream_token(token, resource="run:other")
    assert info.value.status_code == 401


def _step(index: int, status: ResearchStepStatus) -> ResearchStep:
    return ResearchStep(
        id=uuid.UUID(int=index + 1),
        run_id=uuid.UUID(int=99),
        attempt=1,
        step_index=index,
        node_name=f"node{index}",
        title=f"Node {index}",
        status=status,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        step_metadata={},
    )


class FakePubSub:
    def __init__(self, messages: list[dict]) -> None:
        self.messages = messages

    async def get_message(self, ignore_subscribe_messages: bool, timeout: float):
        return self.messages.pop(0) if self.messages else None

    async def aclose(self) -> None:
        pass


def _parse(chunks: list[str]) -> list[tuple[str, dict | None]]:
    events = []
    for chunk in chunks:
        if chunk.startswith(":"):
            events.append(("heartbeat", None))
            continue
        name = chunk.split("\n")[0].removeprefix("event: ")
        events.append((name, json.loads(chunk.split("\ndata: ")[1])))
    return events


@pytest.mark.asyncio
async def test_stream_sends_existing_then_live_then_ends(monkeypatch):
    snapshots = [
        ([_step(0, ResearchStepStatus.COMPLETED), _step(1, ResearchStepStatus.RUNNING)], False),
        ([_step(0, ResearchStepStatus.COMPLETED), _step(1, ResearchStepStatus.COMPLETED)], True),
    ]

    async def load(session):
        return snapshots.pop(0)

    live = json.dumps(
        {"id": str(uuid.UUID(int=2)), "status": "completed", "completed_at": "x", "duration_ms": 5}
    )
    pubsub = FakePubSub([{"type": "message", "data": live}])

    async def fake_subscribe(channel):
        return None, pubsub

    monkeypatch.setattr(step_stream, "_subscribe", fake_subscribe)
    chunks = [
        chunk
        async for chunk in step_event_stream(
            channel="run:1:steps", load=load, tick_seconds=0.01, heartbeat_seconds=0
        )
    ]
    events = _parse(chunks)
    names = [name for name, _ in events]
    # Existing rows first, in order.
    assert [data["node_name"] for name, data in events[:2]] == ["node0", "node1"]
    assert "heartbeat" in names
    # The live event, the final DB state of step 1, then the end marker;
    # step 0 (unchanged) is never re-sent.
    step_events = [data for name, data in events if name == "step"]
    assert sum(1 for data in step_events if data["id"] == str(uuid.UUID(int=1))) == 1
    assert events[-1] == ("end", {"reason": "terminal"})


@pytest.mark.asyncio
async def test_stream_without_redis_still_delivers_and_times_out(monkeypatch):
    async def load(session):
        return [_step(0, ResearchStepStatus.RUNNING)], False

    async def no_redis(channel):
        return None, None

    monkeypatch.setattr(step_stream, "_subscribe", no_redis)
    chunks = [
        chunk
        async for chunk in step_event_stream(
            channel="c", load=load, tick_seconds=0.01, heartbeat_seconds=60, max_seconds=0.05
        )
    ]
    events = _parse(chunks)
    assert events[0][0] == "step"
    assert events[-1] == ("end", {"reason": "timeout"})


# --- routes against the real database ---------------------------------------


async def _register(client: httpx.AsyncClient, emails: list[str]) -> tuple[dict, uuid.UUID]:
    email = f"pytest-{uuid.uuid4().hex[:12]}@example.com"
    emails.append(email)
    await client.post(
        "/api/v1/auth/register", json={"email": email, "password": PASSWORD, "persona": "student"}
    )
    tokens = (
        await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    ).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    return headers, uuid.UUID(me["id"])


@pytest.mark.asyncio
async def test_step_routes_list_token_and_stream_with_ownership():
    emails: list[str] = []
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            owner, owner_id = await _register(client, emails)
            other, _ = await _register(client, emails)
            project = (
                await client.post("/api/v1/projects", json={"name": "Steps"}, headers=owner)
            ).json()

            async with async_session_factory() as session:
                run = ResearchRun(
                    project_id=uuid.UUID(project["id"]),
                    owner_id=owner_id,
                    query="q",
                    status=ResearchRunStatus.COMPLETED,
                    include_assets=False,
                    include_web=False,
                    max_results=3,
                )
                session.add(run)
                await session.flush()
                session.add(
                    ResearchStep(
                        run_id=run.id,
                        step_index=0,
                        node_name="synthesis",
                        title="Synthesize",
                        status=ResearchStepStatus.COMPLETED,
                        model_name="groq/a",
                        input_tokens=5,
                        step_metadata={"used_web": False, "reason": "Found enough in your documents."},
                    )
                )
                await session.commit()
                run_id = run.id

            base = f"/api/v1/research/runs/{run_id}/steps"
            listed = await client.get(base, headers=owner)
            assert listed.status_code == 200
            [step] = listed.json()
            assert (step["model_name"], step["input_tokens"]) == ("groq/a", 5)
            assert step["metadata"]["used_web"] is False

            assert (await client.get(base, headers=other)).status_code == 404
            assert (await client.post(f"{base}/stream-token", headers=other)).status_code == 404

            token = (await client.post(f"{base}/stream-token", headers=owner)).json()
            assert token["expires_in"] == 60

            # Another run's token, or no token, is refused.
            wrong = create_stream_token(owner_id, f"run:{uuid.uuid4()}")
            assert (await client.get(f"{base}/stream", params={"token": wrong})).status_code == 401
            assert (await client.get(f"{base}/stream")).status_code == 422

            async with client.stream("GET", f"{base}/stream", params={"token": token["token"]}) as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
                assert response.headers["cache-control"] == "no-cache"
                assert response.headers["x-accel-buffering"] == "no"
                body = "".join([chunk async for chunk in response.aiter_text()])
            # A finished run: its steps, then `end`, then the server closes.
            assert '"node_name": "synthesis"' in body
            assert body.rstrip().endswith('data: {"reason": "terminal"}')
    finally:
        async with async_session_factory() as session:
            await session.execute(delete(User).where(User.email.in_(emails)))
            await session.commit()
