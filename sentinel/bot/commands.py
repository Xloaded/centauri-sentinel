"""Telegram bot command and callback handlers.

Commands require a configured chat and user, or explicit trusted-group access.
Unauthorized messages are silently dropped with a WARNING log — the bot
never replies to unknown senders.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from telegram import KeyboardButton, ReplyKeyboardMarkup
from telegram.error import TelegramError

from sentinel.bot.keyboards import (
    SNOOZE_DURATIONS,
    detection_keyboard,
    group_command_keyboard,
    print_keyboard,
)
from sentinel.bot.status_format import format_status_caption
from sentinel.watcher.state import WatcherState

_TUI_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📊 Status"), KeyboardButton("📸 Snapshot")],
        [KeyboardButton("⏸️ Pause"), KeyboardButton("▶️ Resume print"), KeyboardButton("⏹️ Stop")],
        [KeyboardButton("🛡 Detection")],
    ],
    resize_keyboard=True,
)

if TYPE_CHECKING:
    from telegram import Update

    from sentinel.config import Settings
    from sentinel.db.repo import Database
    from sentinel.notify.telegram import TelegramNotifier

logger = logging.getLogger(__name__)

_STOP_CONFIRM_WINDOW = 30.0  # seconds
_RESUME_CONFIRMATION_WAIT_S = 60.0


class BotCommandHandler:
    """Handles all bot commands and inline keyboard callbacks."""

    def __init__(
        self,
        settings: Settings,
        printer: Any,
        camera: Any,
        db: Database,
        watcher: Any,
        notifier: TelegramNotifier,
        *,
        snooze_seconds: float = 600.0,
    ) -> None:
        self._settings = settings
        self._printer = printer
        self._camera = camera
        self._db = db
        self._watcher = watcher
        self._notifier = notifier
        self._snooze_seconds = snooze_seconds
        self._resume_deliveries: dict[tuple[float, int, int], asyncio.Task[None]] = {}
        # Maps user_id → timestamp of /stop command; cleared after confirm or expiry
        self._pending_stops: dict[int, float] = {}
        # Maps user_id → command timestamps for rate limiting
        self._rate_limit_history: dict[int, list[float]] = {}

    # ------------------------------------------------------------------
    # Auth guard
    # ------------------------------------------------------------------

    @staticmethod
    def _reply_keyboard(update: Update) -> ReplyKeyboardMarkup:
        """Address group commands explicitly; preserve the private icon keyboard."""
        if update.effective_chat is not None and update.effective_chat.type in (
            "group",
            "supergroup",
        ):
            return group_command_keyboard(update.get_bot().username)
        return _TUI_KEYBOARD

    def _authorized(self, update: Update) -> bool:
        # Clean up expired pending stop requests to prevent memory leaks (M10)
        now = time.monotonic()
        expired = [
            uid for uid, ts in list(self._pending_stops.items()) if now - ts > _STOP_CONFIRM_WINDOW
        ]
        for uid in expired:
            self._pending_stops.pop(uid, None)

        if update.message is not None:
            user = update.message.from_user
            chat_id: int | str = update.message.chat_id
        elif update.callback_query is not None:
            user = update.callback_query.from_user
            cq_msg = update.callback_query.message
            if cq_msg is None:
                return False
            chat_id = cq_msg.chat.id
        else:
            return False

        if user is None:
            return False

        authorized = self._notifier.is_authorized(chat_id, user.id)
        chat = update.effective_chat
        trusted_groups = {
            value.strip()
            for value in (self._settings.telegram_group_control_chat_ids or "").split(",")
            if value.strip().startswith("-") and value.strip()[1:].isdigit()
        }
        configured_chats = {
            value.strip()
            for value in (
                (self._settings.telegram_chat_id or "")
                + ","
                + (self._settings.telegram_chat_ids or "")
            ).split(",")
            if value.strip()
        }
        if (
            str(chat_id) in trusted_groups & configured_chats
            and chat is not None
            and chat.type in ("group", "supergroup")
            and user.is_bot is False
            and not (update.message and update.message.sender_chat)
        ):
            # Telegram supplies the user's identity and the originating group context.
            # This explicit policy trusts participants within this configured group only.
            authorized = True
        logger.info(
            "Telegram interaction received: chat_type=%s authorized=%s anonymous_sender=%s",
            getattr(chat, "type", "unknown"),
            authorized,
            bool(update.message and update.message.sender_chat),
        )
        if not authorized:
            logger.warning(
                "Unauthorized Telegram interaction — chat=%s user=%s",
                str(chat_id)[:4] + "***",
                str(user.id)[:3] + "***",
            )
        return authorized

    async def _check_rate_limit(self, update: Update) -> bool:
        user = None
        if update.message is not None:
            user = update.message.from_user
        elif update.callback_query is not None:
            user = update.callback_query.from_user

        if user is None:
            return True

        now = time.monotonic()
        history = self._rate_limit_history.get(user.id, [])
        # Keep only timestamps from the last 60 seconds
        history = [ts for ts in history if now - ts < 60.0]
        self._rate_limit_history[user.id] = history

        if len(history) >= 5:
            logger.warning("Telegram user %d is rate limited", user.id)
            if update.message is not None:
                await update.message.reply_text(
                    "⚠️ Slow down! Maximum 5 commands per minute allowed.",
                    reply_markup=self._reply_keyboard(update),
                )
            elif update.callback_query is not None:
                await update.callback_query.answer(
                    "⚠️ Slow down! Maximum 5 commands per minute allowed.",
                    show_alert=True,
                )
            return False

        self._rate_limit_history[user.id].append(now)
        return True

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def cmd_help(self, update: Update, context: Any) -> None:
        if not self._authorized(update):
            return
        if not await self._check_rate_limit(update):
            return
        assert update.message is not None
        await update.message.reply_text(
            "/status — watcher state + last detection\n"
            "/snapshot — current camera frame\n"
            "/pause — pause the print\n"
            "/resume — resume the print\n"
            "/stop — cancel the print (requires /confirm within 30 s)\n"
            "/confirm — confirm /stop\n"
            "/enable — enable failure detection\n"
            "/disable — disable failure detection\n"
            "/detection — pause detection for 10 min, 1 hour, or 2 hours\n"
            "/help — this message",
            reply_markup=self._reply_keyboard(update),
        )

    async def cmd_status(self, update: Update, context: Any) -> None:
        if not self._authorized(update):
            return
        if not await self._check_rate_limit(update):
            return
        assert update.message is not None
        detection_enabled = await self._db.get_setting("detection_enabled", "true")
        recent = await self._db.get_recent_detections(limit=1)
        last_det = recent[0] if recent else None

        # Expose printer state and elapsed print time
        p_status = await self._watcher.get_fresh_status()
        caption = format_status_caption(
            p_status,
            watcher_state=self._watcher.state.name,
            detection_enabled=(detection_enabled == "true"),
            last_detection=last_det,
            snooze_until=await self._snooze_until(),
        )

        try:
            async with asyncio.timeout(10):
                jpeg = await self._camera.grab()
        except Exception:
            logger.exception("Camera capture failed for Telegram status")
            jpeg = None
            caption += "\n\n⚠️ Chamber feed unavailable."

        try:
            if jpeg is not None:
                await update.message.reply_photo(
                    photo=jpeg,
                    caption=caption,
                    reply_markup=self._reply_keyboard(update),
                    do_quote=False,
                    connect_timeout=10,
                    read_timeout=10,
                    write_timeout=30,
                )
                return
        except TelegramError:
            logger.exception("Telegram status photo delivery failed")
        try:
            await update.message.reply_text(
                caption,
                reply_markup=self._reply_keyboard(update),
                do_quote=False,
                connect_timeout=10,
                read_timeout=10,
                write_timeout=10,
            )
        except TelegramError:
            logger.exception("Telegram status text delivery failed")

    async def cmd_snapshot(self, update: Update, context: Any) -> None:
        if not self._authorized(update):
            return
        if not await self._check_rate_limit(update):
            return
        assert update.message is not None
        try:
            jpeg = await self._camera.grab()
            await update.message.reply_photo(
                photo=jpeg, reply_markup=self._reply_keyboard(update), do_quote=False
            )
        except Exception:
            logger.exception("Failed to grab snapshot for Telegram")
            await update.message.reply_text(
                "Camera unavailable.", reply_markup=self._reply_keyboard(update), do_quote=False
            )

    async def cmd_pause(self, update: Update, context: Any) -> None:
        if not self._authorized(update):
            return
        if not await self._check_rate_limit(update):
            return
        assert update.message is not None
        from sentinel.printer.errors import PauseDebouncedError

        try:
            await self._printer.pause()
        except PauseDebouncedError:
            # Debounce fired — check whether the printer is genuinely paused.
            try:
                live = await self._printer.status()
                if live.print_state == "paused":
                    await self._db.record_pause(source="telegram", result="ok")
                    await self._watcher.get_fresh_status(force=True)
                    await update.message.reply_text(
                        "Print paused.", reply_markup=self._reply_keyboard(update)
                    )
                    return
            except Exception:
                pass
            await self._db.record_pause(
                source="telegram",
                result="error",
                error_message="Pause suppressed by debounce; printer status unclear",
            )
            await update.message.reply_text(
                "Pause request suppressed — a pause was already sent recently. "
                "If the printer is still printing, please try /pause again in a moment.",
                reply_markup=self._reply_keyboard(update),
            )
            return
        except Exception as exc:
            logger.exception("Pause failed via Telegram command")
            await self._db.record_pause(source="telegram", result="error", error_message=str(exc))
            await update.message.reply_text(
                "Pause failed — check the printer.", reply_markup=self._reply_keyboard(update)
            )
            return
        await self._db.record_pause(source="telegram", result="ok")
        await self._watcher.get_fresh_status(force=True)
        await update.message.reply_text("Print paused.", reply_markup=self._reply_keyboard(update))

    async def cmd_resume(self, update: Update, context: Any) -> None:
        if not self._authorized(update):
            return
        if not await self._check_rate_limit(update):
            return
        assert update.message is not None
        await self._begin_resume(update, context)

    async def _begin_resume(self, update: Update, context: Any) -> None:
        """Acknowledge quickly; reconcile the original message asynchronously."""
        cq = update.callback_query
        message: Any = cq.message if cq is not None else update.message
        chat = update.effective_chat
        if message is None or chat is None:
            return
        control = self._watcher.resume_control
        op = control.request(self._watcher.last_printer_status)
        op.origin_chat_ids.add(str(chat.id))
        key = (op.requested_at, chat.id, message.message_id)
        if key in self._resume_deliveries:
            return

        async def send(text: str, *, final: bool = False) -> None:
            # Delivery failures never alter the physical action's outcome.
            for attempt in range(2):
                try:
                    if cq is not None:
                        if getattr(cq.message, "photo", None):
                            await cq.edit_message_caption(
                                caption=text,
                                reply_markup=print_keyboard(),
                                connect_timeout=5,
                                read_timeout=10,
                                write_timeout=10,
                            )
                        else:
                            await cq.edit_message_text(
                                text,
                                reply_markup=print_keyboard(),
                                connect_timeout=5,
                                read_timeout=10,
                                write_timeout=10,
                            )
                    else:
                        await message.reply_text(
                            text,
                            reply_markup=self._reply_keyboard(update),
                            connect_timeout=5,
                            read_timeout=10,
                            write_timeout=10,
                        )
                    return
                except TelegramError as exc:
                    if "not modified" in str(exc).lower():
                        return
                    if attempt == 1:
                        logger.exception("Telegram resume result delivery failed")
                    else:
                        await asyncio.sleep(1)

        async def deliver() -> None:
            if not op.result.done():
                await send("Resume requested — waiting for printer confirmation.")
            try:
                async with asyncio.timeout(_RESUME_CONFIRMATION_WAIT_S):
                    confirmed = await asyncio.shield(op.result)
            except TimeoutError:
                await send(
                    "Resume is not confirmed yet — checking printer status in the background."
                )
                confirmed = await asyncio.shield(op.result)
            if confirmed:
                try:
                    await self._watcher.external_transition(
                        WatcherState.ARMED, from_states=(WatcherState.PAUSED, WatcherState.STALLED)
                    )
                except Exception:
                    logger.exception("Could not synchronize watcher after confirmed resume")
                text = "Print resumed — confirmed by printer."
                until = await self._snooze_until()
                if until > time.time():
                    remaining = max(1, int((until - time.time() + 59) // 60))
                    text += f"\nDetection remains paused ({remaining} min remaining)."
                await send(text, final=True)
            else:
                await send(
                    "Resume could not be confirmed. Check the printer or connection.", final=True
                )

        # Keep this task owned by the application when a real PTB context exists.
        task = (
            context.application.create_task(deliver())
            if context is not None
            else asyncio.create_task(deliver())
        )
        self._resume_deliveries[key] = task
        for old_key, old_task in list(self._resume_deliveries.items()):
            if len(self._resume_deliveries) > 32 and old_task.done():
                del self._resume_deliveries[old_key]
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    async def _snooze_until(self) -> float:
        try:
            return float(await self._db.get_setting("snooze_until_utc", "0") or "0")
        except (TypeError, ValueError):
            return 0.0

    async def cmd_detection(self, update: Update, context: Any) -> None:
        if not self._authorized(update) or not await self._check_rate_limit(update):
            return
        assert update.message is not None
        await update.message.reply_text(
            "Pause failure detection for:", reply_markup=detection_keyboard()
        )

    async def cmd_stop(self, update: Update, context: Any) -> None:
        if not self._authorized(update):
            return
        if not await self._check_rate_limit(update):
            return
        assert update.message is not None
        user = update.message.from_user
        assert user is not None
        self._pending_stops[user.id] = time.monotonic()
        await update.message.reply_text(
            "Reply /confirm within 30 s to cancel the print.",
            reply_markup=self._reply_keyboard(update),
        )

    async def cmd_confirm(self, update: Update, context: Any) -> None:
        if not self._authorized(update):
            return
        if not await self._check_rate_limit(update):
            return
        assert update.message is not None
        user = update.message.from_user
        assert user is not None

        ts = self._pending_stops.get(user.id)
        if ts is None or (time.monotonic() - ts) > _STOP_CONFIRM_WINDOW:
            self._pending_stops.pop(user.id, None)
            await update.message.reply_text(
                "No active /stop request (or it expired). Use /stop first.",
                reply_markup=self._reply_keyboard(update),
            )
            return

        self._pending_stops.pop(user.id)
        try:
            await self._printer.stop()
            await self._watcher.get_fresh_status(force=True)
            await update.message.reply_text(
                "Print cancelled.", reply_markup=self._reply_keyboard(update)
            )
        except Exception:
            logger.exception("Stop failed via Telegram /confirm")
            await update.message.reply_text(
                "Stop failed — check the printer.", reply_markup=self._reply_keyboard(update)
            )

    async def cmd_enable(self, update: Update, context: Any) -> None:
        if not self._authorized(update):
            return
        if not await self._check_rate_limit(update):
            return
        assert update.message is not None
        self._watcher.cancel_snooze()
        await self._db.set_setting("snooze_until_utc", "0")
        await self._db.set_setting("detection_enabled", "true")
        await update.message.reply_text(
            "Failure detection enabled.", reply_markup=self._reply_keyboard(update)
        )

    async def cmd_disable(self, update: Update, context: Any) -> None:
        if not self._authorized(update):
            return
        if not await self._check_rate_limit(update):
            return
        assert update.message is not None
        self._watcher.cancel_snooze()
        await self._db.set_setting("snooze_until_utc", "0")
        await self._db.set_setting("detection_enabled", "false")
        await update.message.reply_text(
            "Failure detection disabled.", reply_markup=self._reply_keyboard(update)
        )

    # ------------------------------------------------------------------
    # Inline keyboard callbacks
    # ------------------------------------------------------------------

    async def _deliver_text_or_caption(self, cq: Any, text: str, reply_markup: Any = None) -> None:
        """Helper to edit text or caption depending on message type."""
        try:
            if cq.message and getattr(cq.message, "photo", None):
                if reply_markup is not None:
                    await cq.edit_message_caption(caption=text, reply_markup=reply_markup)
                else:
                    await cq.edit_message_caption(caption=text)
            else:
                if reply_markup is not None:
                    await cq.edit_message_text(text, reply_markup=reply_markup)
                else:
                    await cq.edit_message_text(text)
        except TelegramError:
            logger.exception("Telegram callback result delivery failed")

    async def _edit_text_or_caption(
        self, context: Any, cq: Any, text: str, reply_markup: Any = None
    ) -> None:
        delivery = self._deliver_text_or_caption(cq, text, reply_markup)
        if context is not None:
            context.application.create_task(delivery)
        else:
            await delivery

    async def _edit_markup(self, context: Any, cq: Any, keyboard: Any) -> None:
        async def deliver() -> None:
            try:
                await cq.edit_message_reply_markup(reply_markup=keyboard)
            except TelegramError:
                logger.exception("Telegram callback keyboard delivery failed")

        if context is not None:
            context.application.create_task(deliver())
        else:
            await deliver()

    async def handle_callback(self, update: Update, context: Any) -> None:
        """Dispatch inline keyboard button presses from alert messages."""
        cq = update.callback_query
        if cq is None:
            return
        if not self._authorized(update):
            await cq.answer()
            return
        if not await self._check_rate_limit(update):
            return

        try:
            async with asyncio.timeout(3):
                await cq.answer(connect_timeout=3, read_timeout=3, write_timeout=3)
        except (TelegramError, TimeoutError):
            logger.warning(
                "Telegram callback acknowledgement failed; processing authorized action",
                exc_info=True,
            )
        data: str = cq.data or ""

        if data == "resume":
            await self._begin_resume(update, context)

        elif data == "detection_menu":
            await self._edit_markup(context, cq, detection_keyboard())

        elif data == "controls":
            await self._edit_markup(context, cq, print_keyboard())

        elif data == "stop":
            user = cq.from_user
            self._pending_stops[user.id] = time.monotonic()

            from telegram import InlineKeyboardButton, InlineKeyboardMarkup

            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("Confirm Stop", callback_data="confirm_stop"),
                        InlineKeyboardButton("Cancel", callback_data="cancel_stop"),
                    ]
                ]
            )
            await self._edit_markup(context, cq, keyboard)

        elif data == "cancel_stop":
            user = cq.from_user
            self._pending_stops.pop(user.id, None)
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup

            keyboard = print_keyboard()
            await self._edit_markup(context, cq, keyboard)

        elif data == "confirm_stop":
            user = cq.from_user
            ts = self._pending_stops.get(user.id)
            if ts is None or (time.monotonic() - ts) > _STOP_CONFIRM_WINDOW:
                self._pending_stops.pop(user.id, None)
                await self._edit_text_or_caption(
                    context, cq, "Stop request expired. Use the Stop button again."
                )
                return

            self._pending_stops.pop(user.id)
            try:
                await self._printer.stop()
                await self._watcher.get_fresh_status(force=True)
                await self._edit_text_or_caption(context, cq, "Print cancelled.")
            except Exception:
                logger.exception("Stop failed via inline confirm")
                await self._edit_text_or_caption(context, cq, "Stop failed — check the printer.")

        elif data == "snooze" or data in SNOOZE_DURATIONS:
            seconds = SNOOZE_DURATIONS.get(data, self._snooze_seconds)
            await self._watcher.snooze(seconds)
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup

            keyboard = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("Resume print", callback_data="resume")],
                    [
                        InlineKeyboardButton("Resume detection", callback_data="enable"),
                        InlineKeyboardButton("Change duration", callback_data="detection_menu"),
                    ],
                ]
            )
            snooze_mins = int(seconds // 60)
            from datetime import datetime
            from zoneinfo import ZoneInfo

            expiry = datetime.fromtimestamp(
                time.time() + seconds, ZoneInfo("Europe/Tallinn")
            ).strftime("%H:%M %Z")
            await self._edit_text_or_caption(
                context,
                cq,
                f"Detection snoozed for {snooze_mins} minutes (until {expiry}). "
                "This does not resume a paused print.",
                reply_markup=keyboard,
            )

        elif data == "enable":
            self._watcher.cancel_snooze()
            await self._db.set_setting("snooze_until_utc", "0")
            await self._db.set_setting("detection_enabled", "true")
            await self._edit_text_or_caption(
                context, cq, "Detection re-enabled.", reply_markup=print_keyboard()
            )
