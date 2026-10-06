# Telegram reliability fixes

Outgoing status photos previously held the sequential update processor; a failed
callback acknowledgement prevented the authorized printer action from executing.
Notification failures for one configured destination could skip later chats.

Status/snapshot handlers now use PTB block=False. Printer controls remain sequential.
Camera capture for /status has a ten-second deadline. Photo delivery errors fall
back to text without misreporting a camera failure. Telegram connection, read,
write and media timeouts are explicit. Callback acknowledgements have a three-second
deadline; authorized actions still execute after Telegram acknowledgement errors.
Callback message edits are Application-managed background tasks. Each configured
notification destination is attempted even when another fails; the original error
is then propagated to existing retry logic.

Authorization and Stop confirmation remain required. No firmware MQTT code changed.
These fixes are deployed in the audit-fix image. Private/group command responses
and private screenshots/notifications were confirmed by the operator; physical
Resume/Stop/Snooze outcomes have not been verified end to end.

Regression tests cover acknowledgement timeouts, delayed/failed result delivery,
status photo/text errors, handler concurrency, timeout configuration and multi-chat
failure isolation. Python 3.12 isolated test tooling is included. Image override
files are examples, not runtime configuration. Do not apply them automatically.
The SQLite helper makes an online backup, checks integrity, creates mode 0600,
and refuses overwrite. Backups and credentials must remain outside Git.

Remaining issues: network failures still occur; retries can duplicate successful
chat deliveries. Snapshot capture itself has no new deadline. Existing INFO-level
HTTP client/access logs can contain credential-bearing URLs; do not publish raw logs.
