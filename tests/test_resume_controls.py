"""State-confirmed resume behavior and timed detection menu regressions."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from sentinel.bot.keyboards import detection_keyboard
from sentinel.bot.status_format import format_status_caption
from sentinel.printer.errors import PrinterCommandError
from sentinel.printer.resume import ResumeCoordinator
from sentinel.printer.types import PrinterStatus
from tests.test_bot import _confirm_resume, _make_handler, _make_update


def status(state="paused", filename="job.gcode", received=None):
    return PrinterStatus(
        state == "printing",
        100,
        20,
        100,
        filename,
        print_state=state,
        received_at_monotonic=received or time.monotonic(),
    )


async def test_rejected_ack_then_printing_confirms_same_operation():
    printer = AsyncMock()
    printer.resume.side_effect = PrinterCommandError("error_code=1010")
    control = ResumeCoordinator(printer)
    try:
        op = control.request(status())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not op.result.done()
        control.observe(status("printing"))
        assert await op.result is True
        printer.resume.assert_awaited_once()
    finally:
        await control.close()


async def test_ack_does_not_confirm_and_old_or_stale_state_is_rejected():
    control = ResumeCoordinator(AsyncMock())
    try:
        op = control.request(status())
        await asyncio.sleep(0)
        control.observe(status("printing", received=op.requested_at - 1))
        control.observe(replace(status("printing"), stale=True))
        control.observe(status("paused"))
        assert not op.result.done()
        control.observe(status("printing"))
        assert await op.result is True
    finally:
        await control.close()


@pytest.mark.parametrize(
    "next_status",
    [status("printing", filename="another.gcode"), replace(status("printing"), elapsed_seconds=1)],
)
async def test_a_different_or_restarted_job_cannot_confirm(next_status):
    control = ResumeCoordinator(AsyncMock())
    try:
        op = control.request(status())
        control.observe(replace(next_status, received_at_monotonic=time.monotonic()))
        assert await op.result is False
    finally:
        await control.close()


async def test_duplicate_requests_share_one_command_and_shutdown_resolves_waiters():
    printer = AsyncMock()
    control = ResumeCoordinator(printer)
    op = control.request(status())
    assert control.request(status()) is op
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    printer.resume.assert_awaited_once()
    await control.close()
    assert await op.result is False


async def test_already_printing_is_confirmed_by_new_packet_without_command():
    printer = AsyncMock()
    control = ResumeCoordinator(printer)
    try:
        op = control.request(status("printing"))
        await asyncio.sleep(0)
        assert not op.result.done()
        control.observe(status("printing"))
        assert await op.result is True
        await asyncio.sleep(0)
        printer.resume.assert_not_awaited()
    finally:
        await control.close()


async def test_timeout_remains_unconfirmed():
    control = ResumeCoordinator(AsyncMock(), timeout=0.01)
    try:
        op = control.request(status())
        assert await asyncio.wait_for(asyncio.shield(op.result), 1) is False
    finally:
        await control.close()


@pytest.mark.parametrize(
    "callback,seconds", [("snooze:600", 600), ("snooze:3600", 3600), ("snooze:7200", 7200)]
)
async def test_duration_selection_snoozes_detection_without_printer_control(callback, seconds):
    handler = _make_handler()
    handler._watcher.snooze = AsyncMock()
    update = _make_update(callback_data=callback)
    await handler.handle_callback(update, None)
    handler._watcher.snooze.assert_awaited_once_with(seconds)
    handler._printer.resume.assert_not_awaited()
    handler._printer.pause.assert_not_awaited()
    text = update.callback_query.edit_message_text.await_args.args[0]
    assert str(seconds // 60) in text
    assert "until" in text


async def test_invalid_duration_is_ignored_and_cannot_disable_detection():
    handler = _make_handler()
    handler._watcher.snooze = AsyncMock()
    await handler.handle_callback(_make_update(callback_data="snooze:-1"), None)
    handler._watcher.snooze.assert_not_awaited()
    handler._db.set_setting.assert_not_awaited()


async def test_resume_print_preserves_detection_snooze():
    handler = _make_handler()
    await handler.handle_callback(_make_update(callback_data="resume"), None)
    await _confirm_resume(handler)
    handler._watcher.cancel_snooze.assert_not_called()
    handler._db.set_setting.assert_not_awaited()


async def test_resume_detection_cancels_timer_without_resuming_print():
    handler = _make_handler()
    await handler.handle_callback(_make_update(callback_data="enable"), None)
    handler._watcher.cancel_snooze.assert_called_once()
    handler._db.set_setting.assert_any_await("snooze_until_utc", "0")
    handler._db.set_setting.assert_any_await("detection_enabled", "true")
    handler._printer.resume.assert_not_awaited()


async def test_repeated_same_message_delivers_one_final_confirmation():
    handler = _make_handler()
    update = _make_update(callback_data="resume")
    await handler.handle_callback(update, None)
    # Do not let the generic rate limiter obscure action coalescing.
    handler._rate_limit_history.clear()
    await handler.handle_callback(update, None)
    await _confirm_resume(handler)
    handler._printer.resume.assert_awaited_once()
    messages = [c.args[0] for c in update.callback_query.edit_message_text.await_args_list]
    assert sum("confirmed by printer" in m for m in messages) == 1


def test_menu_has_all_durations_and_resume_detection():
    buttons = [b for row in detection_keyboard().inline_keyboard for b in row]
    assert {b.callback_data for b in buttons} >= {
        "snooze:600",
        "snooze:3600",
        "snooze:7200",
        "enable",
    }


def test_status_shows_remaining_detection_pause():
    text = format_status_caption(
        status(), watcher_state="PAUSED", detection_enabled=False, snooze_until=time.time() + 3600
    )
    assert "Detection: paused (60 min remaining)" in text


async def test_late_confirmation_corrects_pending_message_without_status(monkeypatch):
    monkeypatch.setattr("sentinel.bot.commands._RESUME_CONFIRMATION_WAIT_S", 0.01)
    handler = _make_handler()
    update = _make_update(callback_data="resume")
    await handler.handle_callback(update, None)
    await asyncio.sleep(0.03)
    assert "not confirmed yet" in update.callback_query.edit_message_text.await_args.args[0]
    await _confirm_resume(handler)
    assert "confirmed by printer" in update.callback_query.edit_message_text.await_args.args[0]
    handler._printer.resume.assert_awaited_once()


async def test_confirmed_printing_cancels_an_inflight_command_wait():
    printer = AsyncMock()
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def send():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    printer.resume.side_effect = send
    control = ResumeCoordinator(printer)
    try:
        op = control.request(status())
        await asyncio.wait_for(entered.wait(), 1)
        control.observe(status("printing"))
        assert await op.result is True
        await asyncio.wait_for(cancelled.wait(), 1)
        printer.resume.assert_awaited_once()
    finally:
        await control.close()


def test_temperature_and_metadata_cannot_refresh_cached_print_state():
    from sentinel.printer.client import PrinterClient
    from tests.test_bot import _settings

    client = PrinterClient(_settings())
    client._record_print_status({"print_status": {"state": "printing"}})
    received = client._last_print_status_time
    assert received > 0
    client._record_print_status({"machine_status": {"temperature": 210}})
    client._record_print_status({"print_status": {"layer": 5}})
    client._record_print_status({"file_metadata": {"filename": "other.gcode"}})
    assert client._last_print_status_time == received


@pytest.mark.parametrize("fresh,expected_count", [(True, 1), (False, 0)])
async def test_external_resume_notification_requires_state_received_after_pause(
    fresh, expected_count
):
    from datetime import UTC, datetime, timedelta
    from unittest.mock import MagicMock

    from sentinel.watcher.loop import WatcherLoop
    from sentinel.watcher.state import WatcherState
    from tests.test_bot import _settings

    db = AsyncMock()
    db.get_setting.return_value = "0"
    dispatcher = MagicMock()
    printer = AsyncMock()
    printer.clear_pause_debounce = MagicMock()
    watcher = WatcherLoop(_settings(), printer, AsyncMock(), AsyncMock(), db, dispatcher)
    watcher.state = WatcherState.PAUSED
    watcher._print_start = datetime.now(UTC) - timedelta(seconds=100)
    watcher._current_job_id = 1
    watcher._current_filename = "job.gcode"
    watcher._prev_print_state = "paused"
    watcher._paused_since = datetime.now(UTC) - timedelta(seconds=30)
    observed = status("printing", received=time.monotonic() - (0 if fresh else 60))
    await watcher._update_state(observed)
    assert dispatcher.dispatch_text.call_count == expected_count
    await watcher.resume_control.close()


@pytest.mark.parametrize("origin_ids", [{"99"}, {"-100123"}, {"99", "-100123"}])
async def test_bot_resume_broadcast_survives_state_transition_race_without_duplicates(origin_ids):
    from datetime import UTC, datetime, timedelta
    from unittest.mock import MagicMock

    from sentinel.watcher.loop import WatcherLoop
    from sentinel.watcher.state import WatcherState
    from tests.test_bot import _settings

    db = AsyncMock()
    db.get_setting.return_value = "0"
    dispatcher = MagicMock()
    printer = AsyncMock()
    printer.clear_pause_debounce = MagicMock()
    printer.resume.side_effect = PrinterCommandError("error_code=1010")
    watcher = WatcherLoop(_settings(), printer, AsyncMock(), AsyncMock(), db, dispatcher)
    watcher.state = WatcherState.PAUSED
    watcher._print_start = datetime.now(UTC) - timedelta(seconds=100)
    watcher._current_job_id = 1
    watcher._current_filename = "job.gcode"
    watcher._prev_print_state = "paused"
    watcher._paused_since = datetime.now(UTC) - timedelta(seconds=30)
    op = watcher.resume_control.request(status())
    op.origin_chat_ids.update(origin_ids)
    try:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await watcher._update_state(status("printing"))
        # The command handler may also synchronize watcher state before the next tick.
        watcher.state = WatcherState.ARMED
        await watcher._update_state(status("printing"))
        dispatcher.dispatch_resume_confirmed.assert_called_once_with(
            "Print resumed — confirmed by printer.", frozenset(origin_ids)
        )
        dispatcher.dispatch_text.assert_not_called()
        assert op.result.result() is True
    finally:
        await watcher.resume_control.close()


async def test_resume_registers_origin_chat_for_broadcast_exclusion():
    handler = _make_handler()
    update = _make_update(callback_data="resume")
    await handler.handle_callback(update, None)
    assert handler._watcher.resume_control.operation.origin_chat_ids == {
        str(update.effective_chat.id)
    }
    await _confirm_resume(handler)
