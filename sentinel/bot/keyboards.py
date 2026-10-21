"""Shared print and detection controls; legacy callback values remain supported."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

SNOOZE_DURATIONS = {"snooze:600": 600, "snooze:3600": 3600, "snooze:7200": 7200}


def detection_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("10 min", callback_data="snooze:600"),
                InlineKeyboardButton("1 hour", callback_data="snooze:3600"),
                InlineKeyboardButton("2 hours", callback_data="snooze:7200"),
            ],
            [
                InlineKeyboardButton("Resume detection", callback_data="enable"),
                InlineKeyboardButton("Back", callback_data="controls"),
            ],
        ]
    )


def print_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Resume print", callback_data="resume"),
                InlineKeyboardButton("Stop", callback_data="stop"),
            ],
            [InlineKeyboardButton("Pause detection", callback_data="detection_menu")],
        ]
    )


def group_command_keyboard(username: str) -> ReplyKeyboardMarkup:
    """Address every group button explicitly so Telegram privacy mode delivers it."""
    return ReplyKeyboardMarkup(
        [
            [f"/status@{username}", f"/snapshot@{username}"],
            [f"/pause@{username}", f"/resume@{username}", f"/stop@{username}"],
            [f"/detection@{username}"],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )
