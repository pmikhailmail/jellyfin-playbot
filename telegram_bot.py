#!/usr/bin/env python3
from __future__ import annotations

import logging
import os
import subprocess
import sys
import traceback
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

PROJECT_ROOT = Path(__file__).resolve().parent
PLAYBACK_SCRIPT = PROJECT_ROOT / "jellyfin_request_to_playback.py"
LOG_LEVEL = os.getenv("BOT_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("telegram_bot")


def _extract_request_text(raw_text: str) -> str:
    # Accept both /play text and /play "text with spaces".
    payload = raw_text.strip().removeprefix("/play").strip()
    if payload.startswith('"') and payload.endswith('"') and len(payload) >= 2:
        payload = payload[1:-1].strip()
    return payload


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    logger.info("/start from chat_id=%s user_id=%s", update.effective_chat.id if update.effective_chat else None, update.effective_user.id if update.effective_user else None)
    await update.message.reply_text(
        "Ready. Just send your request directly.\n"
        "Example: включи серию клиники про рождество"
    )


async def _run_playback(update: Update, request_text: str) -> None:
    """Shared playback logic used by both /play command and plain text messages."""
    if not PLAYBACK_SCRIPT.exists():
        logger.error("Playback script missing: %s", PLAYBACK_SCRIPT)
        await update.message.reply_text(f"ERROR: script not found: {PLAYBACK_SCRIPT}")
        return

    await update.message.reply_text(f"Starting playback for: {request_text}")

    cmd = [sys.executable, str(PLAYBACK_SCRIPT), "--request", request_text]
    logger.info("Running command: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PROJECT_ROOT))
    logger.info("Playback command finished with returncode=%s", proc.returncode)

    output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    output = output[-3500:] if output else "<no output>"

    if proc.returncode == 0:
        await update.message.reply_text("Done.\n\n" + output)
    else:
        await update.message.reply_text(f"Failed (code {proc.returncode}).\n\n{output}")


async def play_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    logger.info(
        "/play received: chat_id=%s user_id=%s text=%r",
        update.effective_chat.id if update.effective_chat else None,
        update.effective_user.id if update.effective_user else None,
        update.message.text,
    )

    request_text = _extract_request_text(update.message.text or "")
    if not request_text:
        logger.warning("/play without payload")
        await update.message.reply_text("Usage: /play <request>")
        return

    await _run_playback(update, request_text)


async def text_probe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    request_text = (update.message.text or "").strip()
    if not request_text:
        return

    logger.info(
        "Text message: chat_id=%s user_id=%s text=%r",
        update.effective_chat.id if update.effective_chat else None,
        update.effective_user.id if update.effective_user else None,
        request_text,
    )

    await _run_playback(update, request_text)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled exception in telegram bot: %s", context.error)
    logger.error("Traceback:\n%s", "".join(traceback.format_exception(None, context.error, context.error.__traceback__)) if context.error else "<no traceback>")


def main() -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN is required.", file=sys.stderr)
        return 2

    logger.info("Starting Telegram bot. playback_script=%s log_level=%s", PLAYBACK_SCRIPT, LOG_LEVEL)

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("play", play_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_probe_handler))
    app.add_error_handler(error_handler)

    logger.info("Handlers registered. Entering polling loop.")
    app.run_polling(drop_pending_updates=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
