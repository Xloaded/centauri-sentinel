# Telegram integration

Centauri Sentinel can send printer events, live progress and camera snapshots to Telegram.

## Features

- Print started notification with camera snapshot and filename
- Live print progress card
- Progress percentage and 20-character progress bar
- Current layer and total layers
- Remaining and elapsed print time
- Estimated completion time (ETA)
- Extruder and bed temperatures
- Watcher and ML detection state
- Native Centauri Carbon 2 printer error alerts
- Active printer errors shown in `/status`
- Print paused and completed notifications
- Manual `/status` command with current camera snapshot

## Configuration

Add the Telegram settings to `.env`:

    TELEGRAM_BOT_TOKEN=your_bot_token
    TELEGRAM_CHAT_ID=your_primary_chat_id
    TELEGRAM_CHAT_IDS=your_group_chat_id,another_chat_id
    NOTIFY_ON_PRINT_START=true
    NOTIFY_ON_PRINT_COMPLETED=true
    NOTIFY_ON_PRINT_PAUSED=true
    TELEGRAM_SEND_SNAPSHOTS=true

`TELEGRAM_CHAT_ID` remains backward compatible. `TELEGRAM_CHAT_IDS` can contain additional
comma-separated chats or private groups; notifications are sent to all configured destinations.
Commands are accepted only when both the chat ID and user ID are authorised.

Never commit your real bot token, chat IDs or user IDs to Git.

After changing `.env`, rebuild/restart Sentinel:

    docker compose up -d --build

## Live progress

While printing, Sentinel maintains a Telegram status card containing the latest printer state.

![Telegram live print progress](images/telegram-progress.png)

The status card is updated approximately every 60 seconds. At 1%, 25%, 50% and 75%, Sentinel refreshes the card silently so the latest progress is also visible in the Telegram chat preview without generating another notification. The 1% milestone provides an early status card shortly after the print begins.

![Telegram chat preview](images/telegram-chat-preview.png)

## Print started

When a print begins, Sentinel waits for the actual filename before sending the notification. This is particularly useful with Centauri Carbon 2 firmware 02.x, where the filename may arrive separately from the initial print-state update.

![Telegram print started](images/telegram-print-started.png)

## Print completed

When a print finishes, Sentinel can send the completed filename, total print time and a final camera snapshot.

![Telegram print completed](images/telegram-print-completed.png)

## Printer errors

Firmware 02.x exposes native printer exception codes through `machine_status.exception_status`.

Sentinel translates supported codes into readable alerts. For example:

    ⚠️ Printer Error
    ⚠️ Canvas: Filament Tangling (1263)

An active error is reported once. If it clears and later occurs again, Sentinel can alert again.

Active printer errors are also included in `/status`.

## Manual status

Send:

    /status

to the Sentinel bot to receive the current camera snapshot and printer status.

The status includes print progress, layer, filename, timing, ETA, temperatures, watcher state, detection state and active printer errors.
