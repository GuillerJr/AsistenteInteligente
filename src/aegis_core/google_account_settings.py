"""Local OAuth account switching with state, PKCE and verified account identity."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx
from aiohttp import web

from aegis_core.mcp.client import McpServerConfiguration
from aegis_core.mcp.google_workspace import (
    GOOGLE_WORKSPACE_OAUTH_PORT,
    GOOGLE_WORKSPACE_REDIRECT_URI,
)
from aegis_core.secrets import MacOSGenericSecret
from aegis_core.settings_store import read_private_json, write_private_json

GOOGLE_SCOPES = (
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive",
)


def credential_path(server: McpServerConfiguration, email: str) -> Path:
    if re.fullmatch(r"[a-z0-9][a-z0-9._+%-]*@[a-z0-9.-]+\.[a-z]{2,63}", email) is None:
        raise ValueError("Invalid Google account")
    directory = Path(server.environment["WORKSPACE_MCP_CREDENTIALS_DIR"])
    if not directory.is_absolute() or directory.resolve() != directory:
        raise ValueError("Invalid Google credential directory")
    return directory / f"{quote(email, safe='@._-')}.json"


class GoogleAccountAuthorization:
    def __init__(
        self,
        server: McpServerConfiguration,
        email: str,
        progress: dict,
        commit: Callable[[str], None],
    ) -> None:
        self.server = server
        self.email = email
        self.progress = progress
        self.commit = commit
        self.runner: web.AppRunner | None = None
        self.completed = asyncio.Event()
        self.state = secrets.token_urlsafe(32)
        self.verifier = secrets.token_urlsafe(64)
        self.claimed = False

    async def run(self) -> None:
        try:
            path = credential_path(self.server, self.email)
            self.client_id = self.server.environment["GOOGLE_OAUTH_CLIENT_ID"]
            self.client_secret = await asyncio.to_thread(
                MacOSGenericSecret(
                    service=f"ai.aegis.mcp.{self.server.server_id}",
                    account=self.server.secret_environment["GOOGLE_OAUTH_CLIENT_SECRET"],
                ).get
            )
            self.path = path
            app = web.Application(client_max_size=4096)
            app.router.add_get("/oauth2callback", self.callback)
            self.runner = web.AppRunner(app, access_log=None)
            await self.runner.setup()
            await web.TCPSite(self.runner, "127.0.0.1", GOOGLE_WORKSPACE_OAUTH_PORT).start()
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            self.progress.update(
                state="awaiting_authorization",
                url=(
                    "https://accounts.google.com/o/oauth2/v2/auth?"
                    + urlencode(
                        {
                            "client_id": self.client_id,
                            "redirect_uri": GOOGLE_WORKSPACE_REDIRECT_URI,
                            "response_type": "code",
                            "scope": " ".join(GOOGLE_SCOPES),
                            "access_type": "offline",
                            "prompt": "consent select_account",
                            "login_hint": self.email,
                            "state": self.state,
                            "code_challenge": challenge,
                            "code_challenge_method": "S256",
                        }
                    )
                ),
            )
            await asyncio.wait_for(self.completed.wait(), timeout=300)
        except asyncio.CancelledError:
            self.progress.update(state="cancelled")
            raise
        except Exception:
            self.progress.update(state="error", reason="google_authorization_failed")
        finally:
            self.progress.pop("url", None)
            if self.runner is not None:
                await self.runner.cleanup()

    async def callback(self, request: web.Request) -> web.Response:
        if (
            request.remote != "127.0.0.1"
            or self.claimed
            or not secrets.compare_digest(request.query.get("state", ""), self.state)
        ):
            return web.Response(status=400, text="Autorización inválida.")
        self.claimed = True
        self.progress.pop("url", None)
        self.progress["state"] = "verifying"
        try:
            code = request.query.get("code", "")
            if not code or len(code) > 4096 or "error" in request.query:
                raise ValueError("Authorization declined")
            async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
                response = await client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "redirect_uri": GOOGLE_WORKSPACE_REDIRECT_URI,
                        "code": code,
                        "grant_type": "authorization_code",
                        "code_verifier": self.verifier,
                    },
                )
                response.raise_for_status()
                token = response.json()
                scopes = set(token.get("scope", "").split())
                if not set(GOOGLE_SCOPES).issubset(scopes) or not token.get("refresh_token"):
                    raise ValueError("Required permissions or refresh token missing")
                identity = await client.get(
                    "https://www.googleapis.com/oauth2/v2/userinfo",
                    headers={"Authorization": f"Bearer {token['access_token']}"},
                )
                identity.raise_for_status()
                user = identity.json()
                if user.get("email", "").lower() != self.email or not user.get("verified_email"):
                    self.progress["reason"] = "google_account_mismatch"
                    # Preserve the previous account's grant if it was selected
                    # accidentally. Other unwanted grants never become Jarvis's account.
                    if user.get("email", "").lower() not in self.server.integration.google_accounts:
                        await client.post(
                            "https://oauth2.googleapis.com/revoke",
                            data={"token": token["refresh_token"]},
                        )
                    raise ValueError("Wrong Google account")
            expiry = datetime.now(UTC) + timedelta(seconds=int(token.get("expires_in", 3600)))
            write_private_json(
                self.path,
                {
                    "token": token["access_token"],
                    "refresh_token": token["refresh_token"],
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scopes": sorted(scopes),
                    "expiry": expiry.replace(tzinfo=None).isoformat(),
                },
            )
            self.commit(self.email)
            self.progress.update(state="done", restart_required=True)
            message = (
                "Cuenta conectada. Regresa a Jarvis y aplica los cambios "
                "para reiniciar el servicio."
            )
        except Exception:
            self.progress.update(
                state="error", reason=self.progress.get("reason", "google_authorization_failed")
            )
            message = "No se conectó la cuenta. Regresa a Jarvis para revisar el resultado."
        self.completed.set()
        return web.Response(text=message, headers={"Cache-Control": "no-store"})


async def revoke_google_account(server: McpServerConfiguration, email: str) -> None:
    path = credential_path(server, email)
    if not path.exists():
        return
    credentials = read_private_json(path)
    token = credentials.get("refresh_token") or credentials.get("token")
    if token:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.post(
                "https://oauth2.googleapis.com/revoke", data={"token": token}
            )
            # Already revoked/expired credentials are safe to remove locally.
            if response.status_code not in {200, 400}:
                raise ValueError("Google revocation failed")
    path.unlink()


async def probe_google_account(server: McpServerConfiguration, email: str) -> None:
    path = credential_path(server, email)
    credentials = read_private_json(path)
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": credentials["client_id"],
                "client_secret": credentials["client_secret"],
                "refresh_token": credentials["refresh_token"],
                "grant_type": "refresh_token",
            },
        )
        response.raise_for_status()
        token = response.json()
        identity = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {token['access_token']}"},
        )
        identity.raise_for_status()
        user = identity.json()
        if user.get("email", "").lower() != email or not user.get("verified_email"):
            raise ValueError("Wrong account")
    credentials["token"] = token["access_token"]
    credentials["expiry"] = (
        (datetime.now(UTC) + timedelta(seconds=int(token.get("expires_in", 3600))))
        .replace(tzinfo=None)
        .isoformat()
    )
    write_private_json(path, credentials)
