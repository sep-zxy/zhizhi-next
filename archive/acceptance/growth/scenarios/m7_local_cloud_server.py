"""Serve an isolated PostgreSQL cloud for Windows desktop acceptance rehearsal."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from ahadiff.growth.cloud.api import CloudSettings, create_app
from ahadiff.growth.cloud.auth import AuthVerifier
from ahadiff.growth.cloud.db import apply_migrations

ISSUER = "https://growth-acceptance.invalid/auth/v1"


def load_identity(
    credentials: Path,
    dsn: str,
    port: int,
    applied: list[str],
) -> AuthVerifier:
    """Refresh a local test token while retaining the original account and issuer."""
    key_file = credentials.with_name("issuer-private-key.pem")
    database_fingerprint = hashlib.sha256(dsn.encode("utf-8")).hexdigest()
    if credentials.exists() != key_file.exists():
        raise ValueError("隔离云身份文件不完整；不能换密钥冒充原账号")

    if credentials.exists():
        previous = json.loads(credentials.read_text(encoding="utf-8"))
        if previous.get("database_fingerprint") != database_fingerprint:
            raise ValueError("隔离云数据库与原账号不一致")
        account_id = uuid.UUID(previous["account_id"])
        private_key = serialization.load_pem_private_key(
            key_file.read_bytes(),
            password=None,
        )
        if not isinstance(private_key, rsa.RSAPrivateKey):
            raise ValueError("隔离云签名密钥不是 RSA 私钥")
    else:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        account_id = uuid.uuid4()
        credentials.parent.mkdir(parents=True, exist_ok=True)
        key_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        with os.fdopen(
            os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
            "wb",
        ) as stream:
            stream.write(key_bytes)
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    access_token = jwt.encode(
        {
            "iss": ISSUER,
            "aud": "authenticated",
            "sub": str(account_id),
            "role": "authenticated",
            "exp": datetime.now(UTC) + timedelta(hours=1),
        },
        private_key,
        algorithm="RS256",
    )
    credentials.write_text(
        json.dumps(
            {
                "account_id": str(account_id),
                "access_token": access_token,
                "port": port,
                "migrations_applied": applied,
                "database_fingerprint": database_fingerprint,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return AuthVerifier(issuer=ISSUER, public_key_pem=public_pem)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--reme-url")
    parser.add_argument("--acceptance-mode", action="store_true")
    args = parser.parse_args()
    applied = apply_migrations(args.dsn)
    auth = load_identity(args.credentials, args.dsn, args.port, applied)
    settings = CloudSettings(args.dsn, auth, reme_url=args.reme_url,
                             acceptance_mode=args.acceptance_mode)
    uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
