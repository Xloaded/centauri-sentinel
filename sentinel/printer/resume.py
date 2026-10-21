"""Reconcile one resume intent with fresh observed printer state."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sentinel.printer.types import PrinterStatus

logger = logging.getLogger(__name__)


@dataclass
class ResumeOperation:
    requested_at: float
    filename: str | None
    result: asyncio.Future[bool]
    origin_chat_ids: set[str] = field(default_factory=set)
    confirmation_dispatched: bool = False
    confirmed_at: float = 0.0
    check_before_send: bool = False
    status_event: asyncio.Event | None = None
    elapsed_seconds: float = 0.0


class ResumeCoordinator:
    """Coalesce repeated requests; an ack alone never confirms printing."""

    def __init__(self, printer: Any, timeout: float = 300.0) -> None:
        self._printer = printer
        self._timeout = timeout
        self._send_lock = asyncio.Lock()
        self.operation: ResumeOperation | None = None
        self._task: asyncio.Task[None] | None = None

    def request(self, status: PrinterStatus | None) -> ResumeOperation:
        op = self.operation
        if op is not None and not op.result.done():
            return op
        now = time.monotonic()
        if (
            op is not None
            and op.result.done()
            and op.result.result()
            and now - op.confirmed_at < 30
            and (status is None or status.print_state != "paused")
        ):
            return op
        if self._task is not None and not self._task.done():
            self._task.cancel()
        op = ResumeOperation(
            now, status.filename if status else None, asyncio.get_running_loop().create_future()
        )
        self.operation = op
        op.check_before_send = status is not None and status.print_state == "printing"
        op.status_event = asyncio.Event()
        op.elapsed_seconds = status.elapsed_seconds if status else 0.0
        self._task = asyncio.create_task(self._run(op))
        return op

    async def _run(self, op: ResumeOperation) -> None:
        try:
            if op.check_before_send and op.status_event is not None:
                try:
                    async with asyncio.timeout(12):
                        await op.status_event.wait()
                except TimeoutError:
                    pass
            if op.result.done():
                return

            async def send_command() -> None:
                try:
                    async with self._send_lock, asyncio.timeout(45):
                        await self._printer.resume()
                except Exception:
                    logger.warning(
                        "Resume command returned an error; awaiting observed state", exc_info=True
                    )

            command = asyncio.create_task(send_command())
            try:
                waiters: set[asyncio.Future[Any]] = {command, op.result}
                await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            finally:
                if not command.done():
                    command.cancel()
                await asyncio.gather(command, return_exceptions=True)
            try:
                async with asyncio.timeout(self._timeout):
                    await asyncio.shield(op.result)
            except TimeoutError:
                if not op.result.done():
                    op.result.set_result(False)
        except asyncio.CancelledError:
            if not op.result.done():
                op.result.set_result(False)
            raise

    def observe(self, status: PrinterStatus) -> None:
        op = self.operation
        if (
            op is None
            or op.result.done()
            or status.stale
            or status.received_at_monotonic <= op.requested_at
        ):
            return
        if op.status_event is not None:
            op.status_event.set()
        if (
            op.filename is not None and status.filename != op.filename
        ) or status.elapsed_seconds + 5 < op.elapsed_seconds:
            op.result.set_result(False)
            return
        if status.print_state == "printing" and status.printing:
            op.confirmed_at = time.monotonic()
            op.result.set_result(True)

    def covers(self, status: PrinterStatus) -> bool:
        op = self.operation
        return bool(
            op is not None
            and op.result.done()
            and op.result.result()
            and time.monotonic() - op.confirmed_at < 30
            and (op.filename is None or op.filename == status.filename)
        )

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self.operation is not None and not self.operation.result.done():
            self.operation.result.set_result(False)
