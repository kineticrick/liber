"""Service tokens for the HTTP server: named, random, stored only as sha256 hashes."""

import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from liber.errors import LiberError
from liber.server.settings import data_dir, write_private_file

_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


@dataclass(frozen=True)
class TokenInfo:
    name: str
    created: str


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class TokenStore:
    def __init__(self, path: Path):
        self.path = path

    @classmethod
    def default(cls) -> "TokenStore":
        return cls(data_dir() / "service-tokens.json")

    def _load(self) -> list[dict]:
        if not self.path.is_file():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LiberError(f"{self.path} is unreadable: {exc}") from exc
        tokens = data.get("tokens") if isinstance(data, dict) else None
        return [t for t in tokens if isinstance(t, dict)] if isinstance(tokens, list) else []

    def _save(self, tokens: list[dict]) -> None:
        text = json.dumps({"tokens": tokens}, indent=2)
        write_private_file(self.path, text)

    def create(self, name: str, today: date) -> str:
        if not _NAME.match(name):
            raise LiberError("token names use lowercase letters, digits and dashes (max 40), e.g. voice-backend")
        tokens = self._load()
        if any(t.get("name") == name for t in tokens):
            raise LiberError(f"a token named {name!r} already exists; revoke it first")
        token = secrets.token_urlsafe(32)
        tokens.append({"name": name, "sha256": _digest(token), "created": today.isoformat()})
        self._save(tokens)
        return token

    def list(self) -> list[TokenInfo]:
        return [TokenInfo(str(t.get("name")), str(t.get("created"))) for t in self._load()]

    def revoke(self, name: str) -> None:
        tokens = self._load()
        kept = [t for t in tokens if t.get("name") != name]
        if len(kept) == len(tokens):
            raise LiberError(f"no token named {name!r}")
        self._save(kept)

    def match(self, token: str) -> str | None:
        digest = _digest(token)
        found = None
        for entry in self._load():  # no early exit: constant work per stored token
            if hmac.compare_digest(digest, str(entry.get("sha256", ""))):
                found = str(entry.get("name"))
        return found
