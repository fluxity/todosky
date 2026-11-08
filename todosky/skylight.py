"""Async client for the unofficial Skylight API.

Login, token renewal and the read endpoints are ported from SkyLink
(custom_components/skylink/api.py and models.py). The write endpoints match
requests captured from the Skylight web app (docs/skylight-write-api.md).
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time as dtime
import hashlib
import re
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import aiohttp

BASE_URL = "https://app.ourskylight.com"
API_VERSION = "2026-03-01"
CLIENT_ID = "skylight-mobile"
SCOPE = "everything"
REDIRECT_URI = "skylight-family://welcome"
APP_SCHEME = "skylight-family:"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
API_UA = "todosky"
REDIRECT_CODES = (301, 302, 303, 307, 308)
MAX_REDIRECTS = 10
EXPIRY_LEEWAY = 60
TIMEOUT = aiohttp.ClientTimeout(total=30)
CSRF_RE = re.compile(r'name="authenticity_token"[^>]*value="([^"]+)"')
DONE_STATUSES = {"complete", "skipped"}


class SkylightError(Exception):
    """Base class for client errors."""


class SkylightAuthError(SkylightError):
    """Credentials or tokens were rejected, so the user has to act."""


class SkylightConnectionError(SkylightError):
    """Skylight could not be reached, or returned an unusable response. Worth retrying."""


class SkylightNotFound(SkylightError):
    """The resource does not exist (HTTP 404)."""


# ---------------------------------------------------------------- models


@dataclass(frozen=True)
class Chore:
    id: str
    summary: str
    category_id: str | None
    start: date | None
    start_time: dtime | None
    status: str
    completed_on: date | None = None

    @property
    def is_done(self) -> bool:
        return self.status in DONE_STATUSES


@dataclass(frozen=True)
class Category:
    id: str
    label: str


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_time(value: Any) -> dtime | None:
    if not isinstance(value, str):
        return None
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    return None


def _resources(payload: Any) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        data = [data]
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _category_id(item: dict[str, Any]) -> str | None:
    relationship = (item.get("relationships") or {}).get("category") or {}
    data = relationship.get("data") or {}
    return str(data["id"]) if data.get("id") is not None else None


def parse_chores(payload: Any) -> list[Chore]:
    chores = []
    for item in _resources(payload):
        attrs = item.get("attributes") or {}
        chores.append(
            Chore(
                id=str(item.get("id")),
                summary=str(attrs.get("summary") or ""),
                category_id=_category_id(item),
                start=_parse_date(attrs.get("start")),
                start_time=_parse_time(attrs.get("start_time")),
                status=str(attrs.get("status") or "pending"),
                completed_on=_parse_date(attrs.get("completed_on")),
            )
        )
    return chores


def parse_categories(payload: Any) -> list[Category]:
    return [
        Category(id=str(item.get("id")), label=str((item.get("attributes") or {}).get("label") or item.get("id")))
        for item in _resources(payload)
    ]


def _hhmm(value: dtime | None) -> str | None:
    return value.strftime("%H:%M") if value else None


def create_body(category_id: str, summary: str, day: date, start_time: dtime | None) -> dict[str, Any]:
    """Body the web app sends to create a one-off chore (see docs/skylight-write-api.md)."""
    return {
        "start": day.isoformat(),
        "up_for_grabs": False,
        "routine": False,
        "start_time": _hhmm(start_time),
        "recurrence_set": None,
        "renewal_interval": None,
        "renewal_unit": None,
        "recurring_until": None,
        "summary": summary,
        "category_ids": [category_id],
    }


# ---------------------------------------------------------------- auth


@dataclass
class Tokens:
    access_token: str
    refresh_token: str | None = None
    expires_at: float | None = None

    def is_expired(self) -> bool:
        return self.expires_at is not None and time.time() >= self.expires_at - EXPIRY_LEEWAY


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _tokens_from_payload(payload: Any) -> Tokens:
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise SkylightAuthError("Token response had no access token")
    expires_in = payload.get("expires_in")
    created_at = payload.get("created_at")
    expires_at = None
    if isinstance(expires_in, (int, float)):
        issued = created_at if isinstance(created_at, (int, float)) else time.time()
        expires_at = float(issued) + float(expires_in)
    refresh_token = payload.get("refresh_token")
    return Tokens(str(payload["access_token"]), str(refresh_token) if refresh_token else None, expires_at)


class SkylightClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        *,
        tokens: Tokens | None = None,
        on_tokens: Callable[[Tokens], None] | None = None,
        base_url: str = BASE_URL,
    ) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._tokens = tokens
        self._on_tokens = on_tokens
        self._base_url = base_url.rstrip("/")
        self._renew_lock = asyncio.Lock()

    @property
    def tokens(self) -> Tokens | None:
        return self._tokens

    # ------------------------------------------------------------ reads

    async def list_frames(self) -> list[dict[str, Any]]:
        return _resources(await self._request("GET", "/api/frames"))

    async def list_categories(self, frame_id: str) -> list[Category]:
        return parse_categories(await self._request("GET", f"/api/frames/{frame_id}/categories"))

    async def list_chores(self, frame_id: str, after: date, before: date) -> list[Chore]:
        params = {"after": after.isoformat(), "before": before.isoformat(), "include_late": "true"}
        return parse_chores(await self._request("GET", f"/api/frames/{frame_id}/chores", params=params))

    # ------------------------------------------------------------ writes (captured from the web app 2026-11-08)

    async def create_chore(
        self, frame_id: str, category_id: str, summary: str, day: date, start_time: dtime | None
    ) -> Chore:
        payload = await self._request(
            "POST",
            f"/api/frames/{frame_id}/chores/create_multiple",
            json=create_body(category_id, summary, day, start_time),
        )
        # One chore comes back per category; we only send one.
        chores = [c for c in parse_chores(payload) if c.id and c.id != "None"]
        if not chores:
            raise SkylightConnectionError("Creating a chore returned no id")
        return next((c for c in chores if c.category_id == category_id), chores[0])

    async def update_chore(
        self, frame_id: str, chore_id: str, summary: str, day: date, start_time: dtime | None
    ) -> None:
        """Set the title, date and time. The app sends only changed fields; sending all three is accepted too."""
        body = {"summary": summary, "start": day.isoformat(), "start_time": _hhmm(start_time)}
        await self._request("PUT", f"/api/frames/{frame_id}/chores/{chore_id}", json=body)

    async def complete_chore(self, frame_id: str, chore_id: str, day: date) -> None:
        body = {"status": "complete", "completed_on": day.isoformat()}
        await self._request("PUT", f"/api/frames/{frame_id}/chores/{chore_id}/completions", json=body)

    async def delete_chore(self, frame_id: str, chore_id: str) -> None:
        try:
            await self._request("DELETE", f"/api/frames/{frame_id}/chores/{chore_id}")
        except SkylightNotFound:
            pass  # Already gone, which is what we wanted.

    # ------------------------------------------------------------ login

    async def login(self) -> None:
        """Log in with the password. Raises SkylightAuthError if it is rejected."""
        try:
            # The login form relies on cookies, which must not end up in a shared session.
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True), timeout=TIMEOUT) as session:
                self._store(await self._login_flow(session))
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise SkylightConnectionError(f"Network error during login: {type(err).__name__}") from err

    def _store(self, tokens: Tokens) -> None:
        self._tokens = tokens
        if self._on_tokens:
            self._on_tokens(tokens)

    async def _login_flow(self, session: aiohttp.ClientSession) -> Tokens:
        verifier = _b64url(secrets.token_bytes(32))
        state = _b64url(secrets.token_bytes(18))
        params = {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
            "state": state,
            "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()),
            "code_challenge_method": "S256",
            "prompt": "login",
        }
        headers = {"User-Agent": BROWSER_UA}
        async with session.get(
            f"{self._base_url}/oauth/authorize", params=params, headers=headers, allow_redirects=False
        ) as resp:
            status, location, _ = resp.status, resp.headers.get("Location"), await resp.read()
        html = await self._follow_to_page(session, status, location, f"{self._base_url}/oauth/authorize")
        match = CSRF_RE.search(html)
        if not match:
            raise SkylightConnectionError("The Skylight login page has changed: no CSRF token")

        form = {"authenticity_token": match.group(1), "email": self._email, "password": self._password}
        async with session.post(
            f"{self._base_url}/auth/session", data=form, headers=headers, allow_redirects=False
        ) as resp:
            status, location, _ = resp.status, resp.headers.get("Location"), await resp.read()
        # Rails re-renders the form with a 200 when the credentials are wrong.
        if status == 200:
            raise SkylightAuthError("Skylight rejected the email or password")
        if status >= 500:
            raise SkylightConnectionError(f"Login returned HTTP {status}")

        location = await self._chase_to_app(session, status, location, f"{self._base_url}/auth/session")
        if not location:
            raise SkylightAuthError("Login did not return to the app")
        query = parse_qs(urlparse(location).query)
        if query.get("state", [""])[0] != state:
            raise SkylightAuthError("OAuth state mismatch")
        code = query.get("code", [""])[0]
        if not code:
            raise SkylightAuthError("OAuth response had no code")
        return await self._token_request(
            session,
            {
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": verifier,
            },
        )

    async def _follow_to_page(
        self, session: aiohttp.ClientSession, status: int, location: str | None, url: str
    ) -> str:
        html = ""
        for _ in range(MAX_REDIRECTS):
            if status not in REDIRECT_CODES or not location or location.startswith(APP_SCHEME):
                break
            url = urljoin(url, location)
            async with session.get(url, headers={"User-Agent": BROWSER_UA}, allow_redirects=False) as resp:
                status, location, html = resp.status, resp.headers.get("Location"), await resp.text()
        return html

    async def _chase_to_app(
        self, session: aiohttp.ClientSession, status: int, location: str | None, url: str
    ) -> str | None:
        for _ in range(MAX_REDIRECTS):
            if location and location.startswith(APP_SCHEME):
                return location
            if status not in REDIRECT_CODES or not location:
                return None
            url = urljoin(url, location)
            async with session.get(url, headers={"User-Agent": BROWSER_UA}, allow_redirects=False) as resp:
                status, location, _ = resp.status, resp.headers.get("Location"), await resp.read()
        return None

    async def _token_request(self, session: aiohttp.ClientSession, form: dict[str, str]) -> Tokens:
        async with session.post(
            f"{self._base_url}/oauth/token",
            data=form,
            headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
            timeout=TIMEOUT,
        ) as resp:
            if resp.status >= 500:
                raise SkylightConnectionError(f"Token endpoint returned HTTP {resp.status}")
            if resp.status != 200:
                raise SkylightAuthError(f"Token endpoint returned HTTP {resp.status}")
            try:
                payload = await resp.json(content_type=None)
            except ValueError as err:
                raise SkylightConnectionError("Token endpoint did not return JSON") from err
        return _tokens_from_payload(payload)

    async def _renew(self, rejected: Tokens | None) -> None:
        """Get a working token: refresh first, then log in again if that is refused."""
        async with self._renew_lock:
            # Another request may have renewed the token while this one waited.
            if self._tokens is not rejected and self._tokens and not self._tokens.is_expired():
                return
            refresh_token = self._tokens.refresh_token if self._tokens else None
            if refresh_token:
                form = {"grant_type": "refresh_token", "client_id": CLIENT_ID, "refresh_token": refresh_token}
                try:
                    self._store(await self._token_request(self._session, form))
                    return
                except SkylightAuthError:
                    pass
                except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                    raise SkylightConnectionError(f"Network error during refresh: {type(err).__name__}") from err
            await self.login()

    # ------------------------------------------------------------ transport

    async def _send(
        self, method: str, path: str, params: dict[str, str] | None, json: Any
    ) -> tuple[int, Any]:
        assert self._tokens is not None
        headers = {
            "Authorization": f"Bearer {self._tokens.access_token}",
            "skylight-api-version": API_VERSION,
            "Accept": "application/json",
            "User-Agent": API_UA,
        }
        try:
            async with self._session.request(
                method, f"{self._base_url}{path}", params=params, json=json, headers=headers, timeout=TIMEOUT
            ) as resp:
                if resp.status not in (200, 201):
                    await resp.read()
                    return resp.status, None
                text = await resp.text()
                if not text.strip():
                    return resp.status, None
                try:
                    return resp.status, await resp.json(content_type=None)
                except ValueError as err:
                    raise SkylightConnectionError(f"{method} {path} did not return JSON") from err
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise SkylightConnectionError(f"Network error: {type(err).__name__}") from err

    async def _request(
        self, method: str, path: str, *, params: dict[str, str] | None = None, json: Any = None
    ) -> Any:
        if self._tokens is None or self._tokens.is_expired():
            await self._renew(self._tokens)
        status, payload = await self._send(method, path, params, json)
        if status == 401:
            await self._renew(self._tokens)
            status, payload = await self._send(method, path, params, json)
            if status == 401:
                raise SkylightAuthError("Skylight rejected a freshly issued token")
        if status == 404:
            raise SkylightNotFound(f"{method} {path} returned HTTP 404")
        if status not in (200, 201, 204):
            raise SkylightConnectionError(f"{method} {path} returned HTTP {status}")
        return payload
