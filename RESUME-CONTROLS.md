# Confirmed resume and timed detection controls

The Telegram bot acknowledges resume requests immediately, then resolves them using a new printer-state packet for the same job. A rejected command acknowledgement followed by observed printing is confirmed correctly. Old cached state, metadata-only updates, stale data, or a different/restarted job cannot confirm the operation. Repeated actions share an operation; notification delivery errors cannot change the printer outcome into a failure.

An initial confirmation window is 60 seconds. If still uncertain, the originating message says verification continues in the background. Verification is bounded; uncertainty is reported honestly if no matching state arrives. Watcher-observed external resumes also produce a confirmation. Pending operations and their command waits are cancelled on shutdown.

Use `/detection` or **🛡 Detection** to choose **10 min**, **1 hour**, or **2 hours**, or **Resume detection**. Detection-alert keyboards expose the same chooser. **Resume print** controls the printer job and preserves an independently selected detection pause. Existing `resume`, `snooze`, `enable`, and the old Resume reply-keyboard text remain compatible.

Timers use the existing persisted `snooze_until_utc` setting and automatic restoration logic. Status and progress captions show remaining paused minutes; snooze confirmations show the expiry in Europe/Tallinn time. ML score threshold, confirmation count, and printer stop behavior are unchanged. Group/private authorization and the deployed shared reply-keyboard behavior are preserved. In privacy-restricted Telegram groups, use addressed slash commands such as `/detection@BotUsername` if plain keyboard text is not delivered.

The Web API resume contract is unchanged; the new state-confirmed action resolver currently applies to Telegram.

Validation on macOS/Python 3.13: 690 tests passed in the full suite (90.40% coverage), plus two later focused regressions for fresh/stale external-resume notifications. New controls and bot modules pass Ruff. Existing unrelated lint/type issues remain in baseline modules (including the untyped progress-dispatch argument); no new type diagnostics remain in the changed control flow. The baseline camera/auth regression tests were synchronized with the already deployed camera/auth changes before the full run. No real printer control commands or Telegram test messages were used.
