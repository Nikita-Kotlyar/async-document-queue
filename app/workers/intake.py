from __future__ import annotations

import asyncio
import email
import json
import logging
import mimetypes
import os
from email.header import decode_header, make_header
from pathlib import Path

import httpx

from app.config import get_settings

logger = logging.getLogger("adq.intake")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

settings = get_settings()
STATE_FILE = Path(settings.state_dir) / "intake_state.json"


def _load_state() -> dict[str, str]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict[str, str]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(STATE_FILE)


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


async def upload(
    client: httpx.AsyncClient,
    path: Path,
    filename: str,
    source: str,
    source_ref: str,
) -> str:
    """Return accepted, rejected (permanent) or retry (temporary)."""
    mime, _ = mimetypes.guess_type(filename)
    try:
        content = path.read_bytes()
        response = await client.post(
            "/api/v1/documents",
            files={"file": (filename, content, mime or "application/octet-stream")},
            data={"source": source, "source_ref": source_ref},
        )
    except (OSError, httpx.HTTPError) as exc:
        logger.warning("upload failed source=%s ref=%s error=%s", source, source_ref, exc)
        return "retry"
    if 200 <= response.status_code < 300:
        try:
            duplicate = response.json().get("duplicate")
        except ValueError:
            duplicate = None
        logger.info(
            "uploaded source=%s ref=%s status=%s duplicate=%s",
            source,
            source_ref,
            response.status_code,
            duplicate,
        )
        return "accepted"
    logger.warning(
        "upload rejected source=%s ref=%s status=%s body=%s",
        source,
        source_ref,
        response.status_code,
        response.text[:300],
    )
    return "rejected" if 400 <= response.status_code < 500 else "retry"


async def watch_mail(client: httpx.AsyncClient, state: dict[str, str]) -> None:
    mail_dir = Path(settings.intake_dir) / "mail"
    while True:
        if mail_dir.exists():
            for eml_path in sorted(mail_dir.glob("*.eml")):
                key = f"mail:{eml_path.name}"
                if key in state:
                    continue
                try:
                    message = email.message_from_bytes(eml_path.read_bytes())
                except Exception as exc:
                    logger.warning("cannot parse %s: %s", eml_path.name, exc)
                    continue
                message_id = _decode_header(message.get("Message-ID")) or eml_path.name
                attachments = [
                    part
                    for part in message.walk()
                    if part.get_content_disposition() == "attachment"
                ]
                outcomes: list[str] = []
                for index, part in enumerate(attachments):
                    filename = _decode_header(part.get_filename()) or f"attachment-{index + 1}.bin"
                    payload = part.get_payload(decode=True) or b""
                    temp = Path(settings.state_dir) / f".upload-{eml_path.stem}-{index}"
                    temp.parent.mkdir(parents=True, exist_ok=True)
                    temp.write_bytes(payload)
                    try:
                        outcome = await upload(
                            client,
                            temp,
                            filename,
                            source="email",
                            source_ref=f"email:{message_id}:{filename}",
                        )
                    finally:
                        temp.unlink(missing_ok=True)
                    outcomes.append(outcome)
                if all(outcome in {"accepted", "rejected"} for outcome in outcomes):
                    state[key] = "processed" if "accepted" in outcomes else "rejected"
                    _save_state(state)
                    logger.info("email processed %s attachments=%s outcomes=%s", eml_path.name, len(attachments), outcomes)
        await asyncio.sleep(5)


async def watch_drop(client: httpx.AsyncClient, state: dict[str, str]) -> None:
    drop_dir = Path(settings.intake_dir) / "drop"
    while True:
        if drop_dir.exists():
            for path in sorted(drop_dir.iterdir()):
                if not path.is_file():
                    continue
                stat = path.stat()
                key = f"drop:{path.name}:{stat.st_mtime_ns}"
                if key in state:
                    continue
                outcome = await upload(
                    client,
                    path,
                    path.name,
                    source="folder",
                    source_ref=f"folder:{path.name}:{stat.st_mtime_ns}",
                )
                if outcome in {"accepted", "rejected"}:
                    state[key] = outcome
                    _save_state(state)
        await asyncio.sleep(5)


async def main() -> None:
    state = _load_state()
    headers = {}
    if settings.api_key:
        headers["X-API-Key"] = settings.api_key
    async with httpx.AsyncClient(base_url=settings.api_url, timeout=60.0, headers=headers) as client:
        await asyncio.gather(watch_mail(client, state), watch_drop(client, state))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
