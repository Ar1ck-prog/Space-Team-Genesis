"""A small WSGI transport for the public environment contract.

The app deliberately knows nothing about generators, truth files or matcher details.
The server-owned EpisodeFactory supplies an already configured environment.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import unquote

from .environment import AuditEnvironment, EpisodeFinalizedError


MAX_REQUEST_BYTES = 64 * 1024


class EpisodeFactory(Protocol):
    """Trusted server boundary: picks seed and evaluation mode, never the client."""

    def create(self, dataset_profile: str, company_size: str) -> AuditEnvironment:
        """Return a resettable environment for the requested public profile."""


@dataclass
class _Session:
    token: str
    environment: AuditEnvironment


@dataclass
class EnvironmentHttpApp:
    """WSGI application implementing the four v1 HTTP routes."""

    factory: EpisodeFactory
    token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32)
    _sessions: dict[str, _Session] = field(default_factory=dict, init=False)

    def __call__(self, environ: Mapping[str, Any], start_response: Callable[..., Any]) -> list[bytes]:
        try:
            status, headers, body = self._dispatch(environ)
        except Exception:
            # Never return exception text: it can contain closed paths or values.
            status, headers, body = self._error("500 Internal Server Error", "internal_error", "Internal server error.")
        start_response(status, headers)
        return [body]

    def _dispatch(self, environ: Mapping[str, Any]) -> tuple[str, list[tuple[str, str]], bytes]:
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = unquote(environ.get("PATH_INFO", ""))
        if method == "POST" and path == "/api/v1/episodes":
            return self._create_episode(environ)

        prefix = "/api/v1/episodes/"
        if not path.startswith(prefix):
            return self._error("404 Not Found", "episode_not_found", "Episode was not found.")
        suffix = path[len(prefix) :]
        parts = suffix.split("/")
        if not parts[0]:
            return self._error("404 Not Found", "episode_not_found", "Episode was not found.")
        episode_id = parts[0]
        session, error = self._authorized_session(environ, episode_id)
        if error is not None:
            return error
        assert session is not None

        if method == "POST" and parts[1:] == ["steps"]:
            return self._step(environ, session)
        if method == "GET" and len(parts) == 1:
            return self._json("200 OK", session.environment.public_state())
        if method == "GET" and parts[1:] == ["trajectory"]:
            return self._trajectory(session.environment)
        return self._error("404 Not Found", "episode_not_found", "Episode was not found.")

    def _create_episode(self, environ: Mapping[str, Any]) -> tuple[str, list[tuple[str, str]], bytes]:
        payload, error = self._json_body(environ)
        if error is not None:
            return error
        if not isinstance(payload, dict) or set(payload) != {"dataset_profile", "company_size"}:
            return self._error("400 Bad Request", "invalid_request", "Request body does not match the public schema.")
        if payload["dataset_profile"] not in {"train", "dev"} or payload["company_size"] not in {"small", "medium", "large"}:
            return self._error("400 Bad Request", "invalid_request", "Request body does not match the public schema.")
        environment = self.factory.create(payload["dataset_profile"], payload["company_size"])
        reset = environment.reset()
        token = self.token_factory()
        self._sessions[token] = _Session(token=token, environment=environment)
        return self._json(
            "201 Created",
            {"episode_token": token, "observation": reset["observation"], "info": reset["info"]},
        )

    def _authorized_session(
        self, environ: Mapping[str, Any], episode_id: str
    ) -> tuple[_Session | None, tuple[str, list[tuple[str, str]], bytes] | None]:
        authorization = environ.get("HTTP_AUTHORIZATION")
        if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
            return None, self._error("401 Unauthorized", "missing_token", "Authorization token is required.")
        token = authorization.removeprefix("Bearer ")
        session = self._sessions.get(token)
        if session is None:
            return None, self._error("403 Forbidden", "invalid_token", "Authorization token is invalid.")
        if session.environment.episode_id != episode_id:
            return None, self._error("404 Not Found", "episode_not_found", "Episode was not found.")
        return session, None

    def _step(self, environ: Mapping[str, Any], session: _Session) -> tuple[str, list[tuple[str, str]], bytes]:
        payload, error = self._json_body(environ)
        if error is not None:
            return error
        try:
            result = session.environment.step(payload)
        except EpisodeFinalizedError:
            return self._error("409 Conflict", "episode_finalized", "Episode is already finalized.")
        return self._json("200 OK", result)

    def _trajectory(self, environment: AuditEnvironment) -> tuple[str, list[tuple[str, str]], bytes]:
        lines = "".join(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n" for row in environment.trajectory())
        return "200 OK", [("Content-Type", "application/x-ndjson; charset=utf-8")], lines.encode("utf-8")

    def _json_body(self, environ: Mapping[str, Any]) -> tuple[Any | None, tuple[str, list[tuple[str, str]], bytes] | None]:
        content_type = environ.get("CONTENT_TYPE", "")
        if not isinstance(content_type, str) or not content_type.lower().startswith("application/json"):
            return None, self._error("415 Unsupported Media Type", "unsupported_media_type", "Content-Type must be application/json.")
        try:
            length = int(environ.get("CONTENT_LENGTH", "0"))
        except ValueError:
            return None, self._error("400 Bad Request", "malformed_json", "Request body is not valid JSON.")
        if length < 0 or length > MAX_REQUEST_BYTES:
            return None, self._error("413 Payload Too Large", "request_too_large", "Request body is too large.")
        raw = environ["wsgi.input"].read(length)
        try:
            return json.loads(raw.decode("utf-8")), None
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, self._error("400 Bad Request", "malformed_json", "Request body is not valid JSON.")

    @staticmethod
    def _json(status: str, payload: Mapping[str, Any]) -> tuple[str, list[tuple[str, str]], bytes]:
        return status, [("Content-Type", "application/json; charset=utf-8")], json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    def _error(self, status: str, code: str, message: str) -> tuple[str, list[tuple[str, str]], bytes]:
        return self._json(status, {"error": {"code": code, "message": message}})
