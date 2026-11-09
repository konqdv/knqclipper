"""Symmetric encryption for credentials stored in the database (Fernet / AES-128-CBC + HMAC)."""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SecretBox:
    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    @classmethod
    def from_settings(cls, secret_key: str | None, data_dir: Path) -> "SecretBox":
        if secret_key:
            return cls(secret_key.encode())
        key_file = data_dir / "secret.key"
        if key_file.exists():
            return cls(key_file.read_bytes().strip())
        key = Fernet.generate_key()
        key_file.write_bytes(key)
        os.chmod(key_file, 0o600)
        return cls(key)

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, token: str) -> str | None:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken:
            return None
