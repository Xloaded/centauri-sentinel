# Deployed source and recovery — 2026-10-21

This revision records the source currently deployed in the owner's Sentinel container. All 40 application files were compared byte-for-byte by SHA-256 against the running container. The manifest is `DEPLOYED-SOURCE-20261021.json`.

The snapshot includes state-confirmed resume handling, trusted family-group authorization, addressed group reply-keyboard commands, timed detection controls and a fresh updating status card after confirmed resume, together with previously deployed camera and authentication fixes. This synchronization does not change production.

## Source recovery

Clone this repository, check out the deployment tag, copy `.env.example` to a private `.env` file and supply your own secrets and deployment settings. Build with the repository Dockerfile and frozen `uv.lock`, then deploy using your saved Compose/Portainer definition with the original persistent data volume. Follow the existing README for the application and optional ML service. The production image was built incrementally; a clean build is a source recovery path and is not claimed to reproduce its image digest.

## Exact deployment recovery

An exact copy of the deployed Docker image, a consistent SQLite backup, snapshots, the private Portainer stack and its environment were saved separately in a protected local recovery archive. The public repository intentionally contains none of those credentials or private runtime data.

For exact recovery: import the saved image with `docker image load`, restore the saved stack definition and environment through Portainer, and restore the data backup while Sentinel is stopped. Keep the original volume names, restore file ownership to UID/GID 1000, and start only one Sentinel instance. Preserve the existing ML token volume or use the stack's documented token initialization. Avoid running a second bot poller against the same Telegram bot.

The private stack can contain the printer access code, web credentials and Telegram credentials. Never commit it or the runtime database to a public repository. Back up the separate ML authentication volume as well as the application data before a complete host recovery.

## Validation and known limitations

The existing deployed application suite passed 713 tests with 91.04% coverage on 2026-10-16; the full suite rerun for this synchronization also passed all 713 tests with 91.04% coverage on Python 3.13.16. The owner's 2026-10-21 Telegram test confirmed physical pause and resume, and server logs show a recorded Telegram pause and subsequent state-confirmed resume notifications.

Two observed limitations are preserved, not silently fixed by this snapshot: a transient externally reported paused state can produce pause/resume notifications without a user-observed physical pause; printer registration can intermittently reject a command because too many clients are connected. Resume handling can still confirm a subsequently observed printing state. These require separate investigation.
