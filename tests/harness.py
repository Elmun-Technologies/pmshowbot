"""In-memory Telegram harness for end-to-end bot flow tests.

The registration flow is where every reported production bug lives (wrong
direction lists, silent freezes, wrong event dates).  Unit tests that call
handlers directly cannot catch those, because the bugs come from the wiring:
which ``db`` facade a handler receives, which text helper it uses, whether an
exception inside a handler leaves a participant without an answer.

This harness builds the *real* production dispatcher through
:class:`bot.bot_manager.BotManager` and feeds it real :class:`aiogram.types.Update`
objects, while the Telegram API itself is a recorder that never touches the
network.
"""
from __future__ import annotations

import datetime as dt
import os
import tempfile
from typing import Any, Optional

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    Chat,
    ChatFullInfo,
    ChatMemberAdministrator,
    Contact,
    File,
    Message,
    PhotoSize,
    Update,
    User,
)
from cryptography.fernet import Fernet

from bot.bot_manager import BotManager
from bot.config import Config
from bot.db import Database
from bot.services.fsm_storage import SqliteFSMStorage

FAKE_TOKEN = "123456:TEST-FAKE-TOKEN"


def _jpeg_stub() -> bytes:
    """A real (tiny) JPEG so photo storage *and* ticket rendering both work."""
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (40, 60, 90)).save(buffer, "JPEG")
    return buffer.getvalue()


_JPEG_STUB = _jpeg_stub()


class FakeSession(BaseSession):
    """Records every outgoing Telegram method instead of performing HTTP."""

    def __init__(self) -> None:
        super().__init__()
        self.methods: list[Any] = []
        self.downloads = 0
        # Tests may register canned answers or exceptions per method name.
        self.handlers: dict[str, Any] = {}
        self._message_id = 1000

    # -- BaseSession API -------------------------------------------------
    async def close(self) -> None:  # pragma: no cover - nothing to release
        return None

    async def make_request(self, bot: Bot, method: Any, timeout: Optional[int] = None) -> Any:
        self.methods.append(method)
        name = type(method).__name__
        if name in self.handlers:
            result = self.handlers[name](method)
            if isinstance(result, Exception):
                raise result
            return result
        return self._default_result(name, method)

    async def stream_content(
        self,
        url: str,
        headers: Optional[dict] = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ):
        self.downloads += 1
        yield _JPEG_STUB

    # -- helpers ---------------------------------------------------------
    def _default_result(self, name: str, method: Any) -> Any:
        if name == "GetFile":
            return File(
                file_id=getattr(method, "file_id", "file"),
                file_unique_id="unique",
                file_path="photos/stub.jpg",
                file_size=len(_JPEG_STUB),
            )
        if name == "GetMe":
            return User(id=1, is_bot=True, first_name="TestBot", username="test_bot")
        if name in {"SendMessage", "SendPhoto", "SendDocument", "SendMediaGroup"}:
            return self._echo(method, name)
        if name in {"EditMessageText", "EditMessageReplyMarkup", "EditMessageCaption"}:
            return True
        if name in {"AnswerCallbackQuery", "DeleteMessage", "SetMyCommands", "DeleteWebhook"}:
            return True
        if name == "GetChat":
            return ChatFullInfo(
                id=getattr(method, "chat_id", -100),
                type="channel",
                title="Test channel",
                username="testchannel",
                max_reaction_count=0,
                accent_color_id=0,
            )
        if name == "GetChatMember":
            return ChatMemberAdministrator(
                user=User(id=1, is_bot=True, first_name="TestBot"),
                status="administrator",
                is_anonymous=False,
                can_be_edited=True,
            )
        raise NotImplementedError(f"FakeSession has no canned result for {name}")

    def _echo(self, method: Any, name: str) -> Message:
        self._message_id += 1
        chat_id = getattr(method, "chat_id", 1)
        message = Message(
            message_id=self._message_id,
            date=dt.datetime.now(dt.timezone.utc),
            chat=Chat(id=chat_id if isinstance(chat_id, int) else 1, type="private"),
            text=str(getattr(method, "text", "") or getattr(method, "caption", "") or ""),
        )
        return message

    # -- assertions ------------------------------------------------------
    def texts_to(self, chat_id: int) -> list[str]:
        """Every text/caption this bot sent to one chat, in order."""
        out: list[str] = []
        for method in self.methods:
            name = type(method).__name__
            if name not in {"SendMessage", "SendPhoto", "EditMessageText"}:
                continue
            if getattr(method, "chat_id", None) != chat_id:
                continue
            text = getattr(method, "text", None) or getattr(method, "caption", None) or ""
            if text:
                out.append(str(text))
        return out

    def methods_named(self, name: str) -> list[Any]:
        return [m for m in self.methods if type(m).__name__ == name]

    def fail_next(self, name: str, error: Exception) -> None:
        """Make the next call of one API method raise ``error`` once."""
        original = self.handlers.get(name)

        def handler(method: Any) -> Any:
            self.handlers.pop(name, None)
            if original is not None:
                self.handlers[name] = original
            raise error

        self.handlers[name] = handler

    def fail_always(self, name: str, error: Exception) -> None:
        self.handlers[name] = lambda _method: error

    def fail_for_chat(self, name: str, chat_id: int, error: Exception) -> None:
        """Make one API method fail only for one chat.

        Needed to test delivery fallbacks: the participant's chat keeps failing
        while the moderation chat still receives the "forward this manually"
        copy of the ticket.
        """
        base = self.handlers.get(name)

        def handler(method: Any) -> Any:
            if getattr(method, "chat_id", None) == chat_id:
                raise error
            if base is not None:
                return base(method)
            return self._default_result(name, method)

        self.handlers[name] = handler


def make_bot(session: Optional[FakeSession] = None) -> tuple[Bot, FakeSession]:
    session = session or FakeSession()
    return Bot(token=FAKE_TOKEN, session=session), session


def make_config(db_path: str, media_dir: str, *, require_subscription: bool = False) -> Config:
    """Process config that never talks to Google and never gates on a channel."""
    return Config(
        super_admin_password="super-pw",
        encryption_key=Fernet.generate_key().decode(),
        google_credentials_file="",
        db_path=db_path,
        media_dir=media_dir,
        require_subscription=require_subscription,
        registration_closed=False,
        panel_port=8080,
        admin_user_ids=frozenset(),
    )


class BotHarness:
    """A temp database, one tenant, and its production dispatcher."""

    def __init__(
        self,
        *,
        slug: str = "splshow",
        name: str = "SPL Show",
        admin_chat_id: int = -1001234567890,
        channel_url: str = "https://t.me/splshow",
        storage: Any = None,
    ) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmp.name, "test.db")
        self.media_dir = os.path.join(self._tmp.name, "media")
        self.config = make_config(self.db_path, self.media_dir)
        self.db = Database(self.db_path, encryption_key=self.config.encryption_key, bootstrap=self.config)
        self.session = FakeSession()
        self.bot = Bot(token=FAKE_TOKEN, session=self.session)
        self.slug = slug
        self.name = name
        self.admin_chat_id = admin_chat_id
        self.channel_url = channel_url
        self._storage = storage
        self._update_id = 0
        self.tenant: Any = None
        self.tenant_config: Any = None
        self.dispatcher: Any = None

    async def start(self) -> "BotHarness":
        await self.db.init()
        self.tenant = await self.db.create_tenant(
            slug=self.slug,
            name=self.name,
            bot_token=FAKE_TOKEN,
            admin_chat_id=self.admin_chat_id,
            required_channel="@splshow",
            channel_url=self.channel_url,
            admin_password="tenant-pw",
        )
        # Re-run migrations/seeding now that the tenant exists (production runs
        # ``init`` at boot with every tenant already in place).
        await self.db.init()
        self.tenant = await self.db.get_tenant(self.tenant.id)
        self.tenant_config = self.config.tenant_config(self.tenant, bot_token=FAKE_TOKEN)
        # Same storage as production (SQLite next to the applications) unless a
        # test injects its own; that is what lets a restart be simulated.
        if self._storage is None:
            self._storage = SqliteFSMStorage(self.db_path)
        manager = BotManager(self.db, self.config, storage=self._storage)
        self.dispatcher = manager._new_dispatcher(self.tenant_config)
        return self

    async def stop(self) -> None:
        await self.bot.session.close()
        if self.dispatcher is not None and self.dispatcher.storage is not None:
            await self.dispatcher.storage.close()
        self._tmp.cleanup()

    # -- feeding updates -------------------------------------------------
    async def feed(self, update: Update) -> None:
        self.dispatcher.run_workflow = getattr(self.dispatcher, "run_workflow", None)
        await self.dispatcher.feed_update(self.bot, update)

    def _next_update(self, payload: Any) -> Update:
        self._update_id += 1
        return Update(update_id=self._update_id, **payload)

    async def send_text(self, user_id: int, text: str, chat_id: Optional[int] = None) -> None:
        await self.feed(
            self._next_update(
                {"message": _message(user_id, chat_id, text=text)}
            )
        )

    async def send_command(self, user_id: int, text: str = "/start") -> None:
        await self.send_text(user_id, text)

    async def send_photo(self, user_id: int, file_id: str = "photo-1") -> None:
        await self.feed(
            self._next_update(
                {
                    "message": _message(
                        user_id,
                        None,
                        photo=[
                            PhotoSize(
                                file_id=file_id,
                                file_unique_id=f"{file_id}-u",
                                width=1080,
                                height=720,
                            )
                        ],
                    )
                }
            )
        )

    async def send_contact(self, user_id: int, phone: str = "+998901234567") -> None:
        await self.feed(
            self._next_update(
                {
                    "message": _message(
                        user_id,
                        None,
                        contact=Contact(phone_number=phone, first_name="Tester"),
                    )
                }
            )
        )

    async def tap(self, user_id: int, data: str, *, chat_id: Optional[int] = None, text: str = "card") -> Any:
        """Feed a callback query as if an inline button was pressed."""
        message = _message(user_id, chat_id, text=text)
        query = CallbackQuery(
            id=f"cq-{data}-{self._update_id}",
            from_user=User(id=user_id, is_bot=False, first_name="Tester"),
            chat_instance="ci",
            message=message,
            data=data,
        )
        await self.feed(self._next_update({"callback_query": query}))
        return query

    # -- assertions ------------------------------------------------------
    def private_texts(self, user_id: int) -> list[str]:
        return self.session.texts_to(user_id)

    def group_texts(self) -> list[str]:
        return self.session.texts_to(self.admin_chat_id)


def _message(
    user_id: int,
    chat_id: Optional[int],
    *,
    text: Optional[str] = None,
    photo: Optional[list] = None,
    contact: Optional[Contact] = None,
) -> Message:
    return Message(
        message_id=1,
        date=dt.datetime.now(dt.timezone.utc),
        chat=Chat(id=chat_id or user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Tester", username="tester"),
        text=text,
        photo=photo,
        contact=contact,
    )


async def with_harness(coro_factory, **kwargs):
    """Run ``coro_factory(harness)`` with a started harness, always cleaning up."""
    harness = BotHarness(**kwargs)
    await harness.start()
    try:
        return await coro_factory(harness)
    finally:
        await harness.stop()


__all__ = [
    "BotHarness",
    "FakeSession",
    "FAKE_TOKEN",
    "MemoryStorage",
    "make_bot",
    "make_config",
    "with_harness",
]
