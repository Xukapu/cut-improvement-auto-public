
"""MAX: подключение уведомлений по телефону или персональной ссылке."""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time

from contextlib import suppress
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import DbSession, ManagerUser
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.client import Client
from app.models.notification import ClientNotificationPreference
from app.services.notification import (
    get_or_create_preferences,
    sync_future_appointments_for_client,
)

logger = logging.getLogger("uvicorn.error")

API_URL = "https://platform-api2.max.ru"
LINK_LIFETIME = 600

_task: asyncio.Task[None] | None = None
_bot_username: str | None = None
_lock = threading.RLock()


@dataclass
class PendingLink:
    client_id: UUID
    expires_at: float
    claimed_user_id: int | None = None


_links: dict[str, PendingLink] = {}
_waiting_contacts: dict[int, str] = {}


router = APIRouter(
    prefix="/max-link",
    tags=["MAX: подключение клиентов"],
)


class LinkResponse(BaseModel):
    client_number: int
    link: str
    expires_in_minutes: int


def _hash_token(value: str) -> str:
    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


def _clear_expired() -> None:
    now = time.monotonic()

    for key, item in list(_links.items()):
        if item.expires_at <= now:
            del _links[key]

    for user_id, key in list(_waiting_contacts.items()):
        if key not in _links:
            del _waiting_contacts[user_id]


def _normalize_phone(value: str | None) -> str | None:
    if not value:
        return None

    digits = re.sub(r"\D", "", value)

    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]

    if len(digits) == 10:
        digits = "7" + digits

    if len(digits) != 11 or not digits.startswith("7"):
        return None

    return digits


def _phone_from_vcard(vcard: str) -> str | None:
    for line in vcard.splitlines():
        if line.upper().startswith("TEL") and ":" in line:
            phone = line.split(":", 1)[1]
            normalized = _normalize_phone(phone)

            if normalized:
                return normalized

    return None


def _valid_contact(
    token: str,
    user_id: int,
    attachment: dict,
) -> str | None:
    if attachment.get("type") != "contact":
        return None

    payload = attachment.get("payload") or {}
    vcard = payload.get("vcf_info")
    signature = payload.get("hash")

    if not isinstance(vcard, str) or not isinstance(
        signature, str
    ):
        return None

    max_info = payload.get("max_info") or {}
    contact_user_id = max_info.get("user_id")

    if (
        contact_user_id is not None
        and contact_user_id != user_id
    ):
        return None

    # MAX подписывает информацию из VCF токеном бота.
    vcard = vcard.replace("\\r\\n", "\r\n")

    calculated = hmac.new(
        token.encode("utf-8"),
        vcard.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(
        calculated,
        signature.lower(),
    ):
        return None

    return _phone_from_vcard(vcard)


def _api_request(
    token: str,
    method: str,
    path: str,
    params: dict | None = None,
    payload: dict | None = None,
) -> dict:
    url = API_URL + path

    if params:
        url += "?" + urlencode(params)

    body = None
    headers = {"Authorization": token}

    if payload is not None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
        ).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(
        url,
        data=body,
        headers=headers,
        method=method,
    )

    try:
        with urlopen(request, timeout=45) as response:
            return json.load(response)

    except HTTPError as exc:
        raise RuntimeError(
            f"MAX API: HTTP {exc.code}"
        ) from exc

    except URLError as exc:
        raise RuntimeError(
            f"MAX API: ошибка соединения: {exc.reason}"
        ) from exc


def _send(
    token: str,
    user_id: int,
    text: str,
    request_contact: bool = False,
) -> None:
    message: dict = {"text": text}

    if request_contact:
        message["attachments"] = [
            {
                "type": "inline_keyboard",
                "payload": {
                    "buttons": [
                        [
                            {
                                "type": "request_contact",
                                "text": "Подключить уведомления",
                            }
                        ]
                    ]
                },
            }
        ]

    _api_request(
        token,
        "POST",
        "/messages",
        params={"user_id": user_id},
        payload=message,
    )


@router.post(
    "/clients/{client_number}/link",
    response_model=LinkResponse,
    summary="Персональная ссылка подключения MAX",
)
def create_personal_link(
    client_number: int,
    db: DbSession,
    _current_user: ManagerUser,
) -> LinkResponse:
    if not _bot_username:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MAX-бот ещё не подключён.",
        )

    client = db.scalar(
        select(Client).where(
            Client.client_number == client_number,
            Client.deleted_at.is_(None),
            Client.archived_at.is_(None),
        )
    )

    if client is None:
        raise HTTPException(
            status_code=404,
            detail="Клиент не найден.",
        )

    existing = db.scalar(
        select(ClientNotificationPreference).where(
            ClientNotificationPreference.client_id == client.id,
            ClientNotificationPreference.deleted_at.is_(None),
        )
    )

    if existing is not None and existing.max_user_id:
        raise HTTPException(
            status_code=409,
            detail="Клиент уже подключён к MAX.",
        )

    random_token = secrets.token_urlsafe(24)
    key = _hash_token(random_token)

    with _lock:
        _clear_expired()

        for old_key, item in list(_links.items()):
            if item.client_id == client.id:
                del _links[old_key]

        _clear_expired()

        _links[key] = PendingLink(
            client_id=client.id,
            expires_at=time.monotonic() + LINK_LIFETIME,
        )

    payload = "link_" + random_token

    link = (
        f"https://max.ru/{quote(_bot_username, safe='')}"
        f"?{urlencode({'start': payload})}"
    )

    return LinkResponse(
        client_number=client_number,
        link=link,
        expires_in_minutes=10,
    )


def _connect(
    user_id: int,
    phone: str,
    link_key: str | None,
) -> str:
    with _lock:
        _clear_expired()

        with SessionLocal() as db:
            client = None

            if link_key is not None:
                challenge = _links.get(link_key)

                if (
                    challenge is None
                    or challenge.claimed_user_id != user_id
                ):
                    return (
                        "❌ Персональная ссылка недействительна "
                        "или срок её действия закончился. "
                        "Попросите новую ссылку в СТО."
                    )

                client = db.get(Client, challenge.client_id)

                if (
                    client is None
                    or client.deleted_at is not None
                    or client.archived_at is not None
                ):
                    return "❌ Карточка клиента недоступна."

            else:
                # В СТО могут храниться два телефона клиента.
                clients = db.scalars(
                    select(Client).where(
                        Client.deleted_at.is_(None),
                        Client.archived_at.is_(None),
                    )
                ).all()

                matches = [
                    item
                    for item in clients
                    if phone in {
                        _normalize_phone(item.phone_primary),
                        _normalize_phone(item.phone_secondary),
                    }
                ]

                if not matches:
                    return (
                        "⚠️ Ваш номер не найден в базе СТО.\n"
                        "Обратитесь к сотруднику за "
                        "персональной ссылкой."
                    )

                if len(matches) > 1:
                    return (
                        "⚠️ Номер найден в нескольких карточках.\n"
                        "Для безопасного подключения "
                        "обратитесь к сотруднику СТО."
                    )

                client = matches[0]

            another = db.scalar(
                select(ClientNotificationPreference).where(
                    ClientNotificationPreference.max_user_id
                    == str(user_id),
                    ClientNotificationPreference.client_id
                    != client.id,
                    ClientNotificationPreference.deleted_at.is_(None),
                )
            )

            if another is not None:
                return (
                    "⚠️ Этот MAX-аккаунт уже подключён "
                    "к другой карточке. Обратитесь в СТО."
                )

            preference = get_or_create_preferences(
                db,
                client.id,
            )

            if preference.max_user_id not in (
                None,
                str(user_id),
            ):
                return (
                    "⚠️ К этой карточке уже подключён "
                    "другой MAX-аккаунт. Обратитесь в СТО."
                )

            already_connected = (
                preference.max_user_id == str(user_id)
                and preference.max_enabled
            )

            preference.max_user_id = str(user_id)
            preference.max_enabled = True

            if not already_connected:
                sync_future_appointments_for_client(
                    db,
                    client.id,
                )

            db.commit()

            number = client.client_number

        if link_key is not None:
            _links.pop(link_key, None)

        _waiting_contacts.pop(user_id, None)

    return (
        f"✅ Уведомления подключены!\n\n"
        f"Карточка клиента №{number} успешно "
        "связана с вашим аккаунтом MAX.\n\n"
        "Теперь СТО сможет отправлять вам "
        "подтверждения и напоминания о записях."
    )


def _process_contact(
    token: str,
    user_id: int,
    attachments: list,
) -> str:
    with _lock:
        _clear_expired()
        link_key = _waiting_contacts.get(user_id)

    for attachment in attachments:
        phone = _valid_contact(
            token,
            user_id,
            attachment,
        )

        if phone:
            try:
                return _connect(
                    user_id,
                    phone,
                    link_key,
                )
            except Exception:
                logger.exception(
                    "MAX: ошибка привязки клиента"
                )
                return (
                    "⚠️ Не удалось сохранить подключение. "
                    "Попробуйте ещё раз."
                )

    return (
        "❌ Не удалось подтвердить номер MAX.\n"
        "Нажмите кнопку подключения ещё раз."
    )


def _process_start(
    token: str,
    user_id: int,
    payload: str | None = None,
) -> None:
    if payload and payload.startswith("link_"):
        key = _hash_token(payload[5:])

        with _lock:
            _clear_expired()
            entry = _links.get(key)

            if (
                entry is None
                or entry.claimed_user_id not in (
                    None,
                    user_id,
                )
            ):
                _send(
                    token,
                    user_id,
                    "❌ Ссылка недействительна. "
                    "Попросите новую ссылку в СТО.",
                )
                return

            entry.claimed_user_id = user_id
            _waiting_contacts[user_id] = key

        _send(
            token,
            user_id,
            "🚘 ЦУТ Improvement Auto\n\n"
            "Вы перешли по персональной ссылке СТО.\n"
            "Подтвердите подключение, поделившись "
            "контактом MAX. Вводить номер не нужно.",
            request_contact=True,
        )
        return

    with _lock:
        _waiting_contacts.pop(user_id, None)

    _send(
        token,
        user_id,
        "👋 Добро пожаловать в ЦУТ Improvement Auto!\n\n"
        "Нажмите кнопку ниже, чтобы подключить "
        "уведомления о записях.\n\n"
        "MAX предложит передать номер телефона "
        "с вашего согласия. Вводить его не нужно.",
        request_contact=True,
    )


def _process_message(token: str, message: dict) -> None:
    sender = message.get("sender") or {}
    recipient = message.get("recipient") or {}

    if sender.get("is_bot"):
        return

    if recipient.get("chat_type") != "dialog":
        return

    user_id = sender.get("user_id")

    if not isinstance(user_id, int):
        return

    body = message.get("body") or {}
    attachments = body.get("attachments") or []

    if any(
        attachment.get("type") == "contact"
        for attachment in attachments
        if isinstance(attachment, dict)
    ):
        result = _process_contact(
            token,
            user_id,
            attachments,
        )
        _send(token, user_id, result)
        return

    content = (body.get("text") or "").strip()
    command = content.casefold()

    if command.startswith("/start link_"):
        _process_start(
            token,
            user_id,
            content.split(maxsplit=1)[1],
        )
        return

    if command in {
        "привет",
        "начать",
        "/start",
        "подключить",
        "подключить уведомления",
    }:
        _process_start(token, user_id)
        return

    if content:
        _send(
            token,
            user_id,
            "🚘 Для подключения уведомлений "
            "напишите «Привет» и нажмите "
            "кнопку подключения.",
        )


def _process_update(token: str, update: dict) -> None:
    kind = update.get("update_type")

    if kind == "bot_started":
        user = update.get("user") or {}
        user_id = user.get("user_id")

        if isinstance(user_id, int):
            _process_start(
                token,
                user_id,
                update.get("payload"),
            )

    elif kind == "message_created":
        _process_message(
            token,
            update.get("message") or {},
        )


async def _poll_messages(token: str) -> None:
    global _bot_username

    marker: str | int | None = None
    initialized = False

    while True:
        try:
            if not initialized:
                me = await asyncio.to_thread(
                    _api_request,
                    token,
                    "GET",
                    "/me",
                )

                subscriptions = await asyncio.to_thread(
                    _api_request,
                    token,
                    "GET",
                    "/subscriptions",
                )

                if subscriptions.get("subscriptions"):
                    logger.error(
                        "MAX: активен Webhook. "
                        "Long Polling не запускается."
                    )
                    return

                initial = await asyncio.to_thread(
                    _api_request,
                    token,
                    "GET",
                    "/updates",
                    {"limit": 100, "timeout": 0},
                )

                marker = initial.get("marker")
                _bot_username = (
                    me.get("username") or ""
                ).lstrip("@") or None

                initialized = True

                logger.info(
                    "MAX подключён: %s",
                    me.get("first_name", "бот"),
                )

            params: dict = {
                "limit": 100,
                "timeout": 30,
                "types": "message_created,bot_started",
            }

            if marker is not None:
                params["marker"] = marker

            result = await asyncio.to_thread(
                _api_request,
                token,
                "GET",
                "/updates",
                params,
            )

            for update in result.get("updates", []):
                try:
                    await asyncio.to_thread(
                        _process_update,
                        token,
                        update,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "MAX: ошибка обработки сообщения"
                    )

            marker = result.get("marker", marker)

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception(
                "MAX: ошибка соединения"
            )
            await asyncio.sleep(5)


async def start_max_bot() -> None:
    global _task

    if os.getenv("PYTEST_CURRENT_TEST"):
        return

    if _task is not None and not _task.done():
        return

    settings = get_settings()

    if settings.max_bot_token is None:
        logger.warning("MAX: токен не указан")
        return

    token = (
        settings.max_bot_token
        .get_secret_value()
        .strip()
    )

    if not token:
        logger.warning("MAX: токен пуст")
        return

    _task = asyncio.create_task(
        _poll_messages(token),
        name="max-bot",
    )

    logger.info("MAX: сервис запущен")


async def stop_max_bot() -> None:
    global _task, _bot_username

    if _task is None:
        return

    _task.cancel()

    with suppress(asyncio.CancelledError):
        await _task

    _task = None
    _bot_username = None

    logger.info("MAX: сервис остановлен")
