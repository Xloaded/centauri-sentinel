# Sentinel fixes: publication and deployment record

## Problems and confirmed causes

* Telegram status photos were handled in the sequential update processor. Slow
  outgoing API requests could hold incoming controls behind them.
* Callback acknowledgement was awaited before the action without isolating errors.
  Telegram timeout exceptions could abandon authorized Resume/Stop/Snooze actions.
* Notification loops could stop at the first failed destination.
* ML request errors retained positive confirmation counts, allowing nonconsecutive
  observations to contribute to automatic pause.
* The bot's getMe response reported can_read_all_group_messages=false: group
  privacy mode was enabled. Telegram can filter ordinary group text/keyboard
  messages and unaddressed commands before Sentinel receives them. This does not
  prove every historical missing command had that cause. Source authorization
  already supports configured negative group IDs and separately allowed users;
  no private-only authorization defect was found.
* PTB already supports /command@BotUsername routing. New tests verify correct
  addressed and bare commands route, while commands addressed to another bot do not.

## Changes and deployment status

Telegram reliability and ML confirmation changes are deployed:
image sha256:124aef9140b9704f90293253f04aeab8bf8c91d615a46c84eb1ab21ba86c3d2a
(local tag centauri-sentinel:audit-fix).
Running container source was compared with the working source: commands.py,
runner.py, notify/telegram.py and watcher/loop.py match after excluding the
new group keyboard and diagnostic additions.

Group/supergroup reply keyboards now emit commands addressed to the bot username.
Private keyboards are unchanged. Diagnostic logs distinguish chat type,
authorization result and anonymous sender without logging command text or secrets.
Startup reports privacy-mode implications. These additions are NOT currently
deployed. A short earlier deployment attempt was rolled back by an overly strict
database-setting equality check. The operator subsequently requested no further
deployments. Publishing this branch does not deploy or restart anything.

Operator confirmed private /status, screenshots and notifications, and later both
private/group commands, before final group keyboard deployment. Therefore this
confirmation cannot establish production validation of the new group keyboard.
Physical Resume/Stop/Snooze outcomes have not been tested by the audit agent.
No authentication, firmware MQTT, database schema or threshold change is included.

## detection_enabled false -> true investigation

Read-only inspection of the pre-group SQLite backup found:
detection_enabled=false; snooze_until_utc=1791328991.7414181,
corresponding to 2026-10-06 23:23:11.741418 UTC.
Later live settings were detection_enabled=true and snooze_until_utc=0.
The attempted group deployment started at approximately 23:25:04 UTC,
after that Snooze expiry. Rollback polling started at 23:25:44 UTC.

Existing WatcherLoop.run_forever startup recovery (sentinel/watcher/loop.py,
lines 151-160 in this branch) explicitly writes true and clears Snooze when
a persisted positive deadline has expired. This logic is present in base
commit 22bfeac and was not introduced by these fixes. Its timed recovery
_re_enable_after also writes the same values. Telegram /enable, its inline
callback and authorized web settings updates are other possible writers.

Finding: an expired persisted Snooze explains the observed transition through
existing application startup behavior; deployment did not directly write the
database. It was incorrect to treat this transition alone as a configuration-loss
regression. Historical logs do not audit every setting write and startup recovery
does not log that write, so the evidence cannot prove which writer executed first
or exclude a concurrent authorized user action. There is no evidence implicating
an unrelated service. New tests exercise the real startup path for expired Snooze
and explicit disable. Production database was inspected read-only, never changed
or restored during this publication task.

## Validation

Python 3.12.8 isolated Docker test image uses uv.lock with frozen dependencies.
Reproducible commands (test containers only, no production volumes or network):
    docker build -f Dockerfile.audit-test -t centauri-sentinel:pr-test .
    docker run --rm --network none centauri-sentinel:pr-test
    docker run --rm --network none --user 65534:65534 centauri-sentinel:pr-test python -m pytest -o addopts="" -p no:cacheprovider -q tests/test_ml_client.py::test_token_unreadable_file_logs_warning

The root run skips a filesystem permission test; it must pass separately as a
non-root user. Final publication run: 667 passed, one skipped in 78.81 seconds; the skipped
permission test passed separately non-root. All 668 tests passed across both
runs. Coverage: 90.65%. Seven dependency deprecation warnings, no failures.
Earlier group revision: 665 passed, one skipped; skipped test passed non-root.
All 666 earlier tests passed across both runs; coverage 90.52%.
Earlier deployed revision: 660 passed, one skipped; skipped test passed non-root.
New startup regressions are included in the final publication run.

## Preservation and rollback considerations

No production configuration or data is included in this branch. Authentication,
environment values, persistent volumes, printer settings and Portainer stored
variables must remain unchanged. The prior deployment verified exact runtime
environment equality, preserved mounts/networks/ports and authenticated API access;
unauthenticated access remained rejected.

Before a future separately approved deployment, retain the current image, root-only
configuration backup and SQLite online backup. backup_sentinel_db.py creates an
exclusive mode-0600 online copy and validates integrity. Never copy only a live
SQLite main file while ignoring WAL. Do not publish backup contents or credentials.

Build the reviewed production Dockerfile with its frozen lockfile, change only
Sentinel's image in the existing Portainer stack, and recreate only that service.
Use the existing deployment's configuration-loading mechanism; do not invent a
new .env or replace authentication. Verify health, authenticated/unauthenticated
access, polling, preserved database and expected time-sensitive Snooze transitions.

To roll back code, select the retained previous image and recreate only Sentinel
with the same configuration and volumes. There is no schema migration. Do not
restore an older database during normal code rollback: it discards newer events.
Do not run compose down or remove volumes. Existing startup reconciliation can
mark stale tracked jobs interrupted; printer actions are not part of deployment.
Image override examples use local tags and are not turnkey Portainer instructions.

## Known limits

Persistent tree-support positives can still meet threshold 0.4 and three
confirmations. The fix breaks stale confirmation streaks; it does not retrain
Obico or establish lower false-positive rates. No physical-printer tests occurred.
Group privacy delivery depends on Telegram; server-side code cannot receive
updates Telegram filters. Addressed keyboard commands avoid requiring BotFather
changes; bare commands may still be filtered. Anonymous admins/unlisted users
remain unauthorized. Outer retries may duplicate already successful notifications.
Existing HTTP client/access logs can contain credential-bearing URLs; raw logs
must never be copied to GitHub. No raw logs, runtime environment, credentials,
databases, backups or deployed Compose configuration are included.
