from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.db import SyncSessionLocal
from app.models import WebhookEndpoint
from app.services.storage import save_bytes, sha256_bytes


def seed_webhook(url: str, secret: str) -> None:
    with SyncSessionLocal() as session:
        existing = session.scalar(select(WebhookEndpoint).where(WebhookEndpoint.url == url))
        if existing:
            print(f"endpoint already exists: {existing.id}")
            return
        endpoint = WebhookEndpoint(
            name="1C/CRM sink",
            url=url,
            secret=secret,
            events=["document.processed", "document.needs_review", "document.failed"],
        )
        session.add(endpoint)
        session.commit()
        print(f"created endpoint {endpoint.id} -> {url}")


def upload(path: Path) -> None:
    import httpx

    settings = get_settings()
    data = path.read_bytes()
    digest = sha256_bytes(data)
    save_bytes(data, path.name, digest)
    with httpx.Client(base_url=settings.api_url, timeout=60) as client:
        response = client.post(
            "/api/v1/documents",
            files={"file": (path.name, data, "application/octet-stream")},
            data={"source": "cli", "source_ref": str(path)},
        )
        print(response.status_code, response.text)


def main() -> int:
    parser = argparse.ArgumentParser(prog="adq")
    sub = parser.add_subparsers(dest="command", required=True)
    seed = sub.add_parser("seed-webhook")
    seed.add_argument("--url", default="http://webhook-sink:8090/hooks/documents")
    seed.add_argument("--secret", default=get_settings().webhook_secret)
    upload_parser = sub.add_parser("upload")
    upload_parser.add_argument("path", type=Path)
    args = parser.parse_args()
    if args.command == "seed-webhook":
        seed_webhook(args.url, args.secret)
    elif args.command == "upload":
        upload(args.path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
