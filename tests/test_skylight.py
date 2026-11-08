"""The Skylight client's write methods and token handling, against a local fake server."""

from datetime import date, time
import time as clock

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from todosky.skylight import API_VERSION, SkylightClient, Tokens


class Fake:
    def __init__(self):
        self.requests = []
        self.valid = {"good"}

    def app(self):
        app = web.Application()
        app.router.add_post("/oauth/token", self.token)
        app.router.add_post("/api/frames/9/chores/create_multiple", self.create)
        app.router.add_put("/api/frames/9/chores/{id}", self.put)
        app.router.add_put("/api/frames/9/chores/{id}/completions", self.put)
        app.router.add_delete("/api/frames/9/chores/{id}", self.delete)
        return app

    def authorized(self, request):
        assert request.headers["skylight-api-version"] == API_VERSION
        return request.headers.get("Authorization", "").removeprefix("Bearer ") in self.valid

    async def token(self, request):
        form = await request.post()
        assert form["grant_type"] == "refresh_token"
        self.valid = {"fresh"}
        return web.json_response({"access_token": "fresh", "refresh_token": "r2", "expires_in": 86400})

    async def create(self, request):
        if not self.authorized(request):
            return web.Response(status=401)
        body = await request.json()
        self.requests.append(("POST", body))
        # Shape captured from the real API: a list, one chore per category.
        chore = {
            "id": "55",
            "type": "chore",
            "attributes": {"summary": body["summary"], "start": body["start"], "start_time": body["start_time"], "status": "pending"},
            "relationships": {"category": {"data": {"id": body["category_ids"][0], "type": "category"}}},
        }
        return web.json_response({"data": [chore]})

    async def put(self, request):
        self.requests.append(("PUT", request.path, await request.json()))
        return web.json_response({"data": {"id": request.match_info["id"]}})

    async def delete(self, request):
        self.requests.append(("DELETE", request.match_info["id"]))
        return web.Response(status=404 if request.match_info["id"] == "gone" else 204)


@pytest.fixture
async def client():
    fake = Fake()
    server = TestServer(fake.app())
    await server.start_server()
    saved = []
    async with aiohttp.ClientSession() as session:
        yield (
            SkylightClient(
                session, "e", "p",
                tokens=Tokens("stale", "r1", clock.time() + 3600),
                on_tokens=saved.append,
                base_url=str(server.make_url("")),
            ),
            fake,
            saved,
        )
    await server.close()


async def test_create_refreshes_rejected_token_and_returns_chore(client):
    sky, fake, saved = client
    chore = await sky.create_chore("9", "2", "Bins", date(2026, 11, 9), time(18, 0))
    assert chore.id == "55" and chore.category_id == "2" and chore.start_time == time(18, 0)
    assert [t.access_token for t in saved] == ["fresh"]
    body = fake.requests[0][1]
    assert (body["summary"], body["start"], body["start_time"], body["category_ids"]) == ("Bins", "2026-11-09", "18:00", ["2"])
    assert body["routine"] is False and body["recurrence_set"] is None


async def test_update_complete_delete(client):
    sky, fake, _ = client
    sky._tokens = Tokens("good")
    await sky.update_chore("9", "55", "Bins!", date(2026, 11, 10), None)
    await sky.complete_chore("9", "55", date(2026, 11, 8))
    await sky.delete_chore("9", "55")
    await sky.delete_chore("9", "gone")  # 404 is fine
    assert fake.requests[0] == ("PUT", "/api/frames/9/chores/55", {"summary": "Bins!", "start": "2026-11-10", "start_time": None})
    assert fake.requests[1] == ("PUT", "/api/frames/9/chores/55/completions", {"status": "complete", "completed_on": "2026-11-08"})
    assert fake.requests[2:] == [("DELETE", "55"), ("DELETE", "gone")]
