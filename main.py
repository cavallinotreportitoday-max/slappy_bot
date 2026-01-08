"""
Slappy Bot - Entry Point
Convertito da workflow n8n SLAPPY_v47_LOCK
"""
import logging
import sys
import asyncio
import traceback
from datetime import datetime
from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, CallbackQueryHandler, CommandHandler, filters
import pytz
from datetime import time as dt_time
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

from config import TELEGRAM_BOT_TOKEN, WEBHOOK_URL, PORT, LOG_LEVEL, SENTRY_DSN, ADMIN_CHAT_ID
from handlers import handle_update, handle_morning, send_morning_briefing_to_all, handle_stats, handle_test_briefing, set_bot_start_time, set_last_error

# Configurazione logging JSON
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Inizializza Sentry (se configurato)
if SENTRY_DSN:
    sentry_logging = LoggingIntegration(
        level=logging.INFO,
        event_level=logging.ERROR
    )
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[sentry_logging],
        traces_sample_rate=0.1,
        environment="production" if WEBHOOK_URL else "development",
        send_default_pii=False
    )
    logger.info("Sentry inizializzato per monitoraggio errori")
else:
    logger.warning("SENTRY_DSN non configurato - monitoraggio errori disattivato")


async def start_handler(update: Update, context):
    """Handler per comando /start"""
    await handle_update(update, context)


async def message_handler(update: Update, context):
    """Handler per messaggi di testo"""
    await handle_update(update, context)


async def callback_handler(update: Update, context):
    """Handler per callback query (bottoni)"""
    await handle_update(update, context)


async def notify_admin_error(bot: Bot, error_msg: str, error_type: str = "ERROR"):
    """
    Invia notifica all'admin quando si verifica un errore grave.
    """
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        text = (
            f"🚨 <b>Slappy Bot - {error_type}</b>\n\n"
            f"📅 {now}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<pre>{error_msg[:3500]}</pre>"
        )
        await bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=text,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Impossibile notificare admin: {e}")


async def error_handler(update: object, context) -> None:
    """
    Error handler globale per python-telegram-bot.
    Logga l'errore, lo invia a Sentry e notifica l'admin.
    """
    # Log completo dell'errore
    logger.error("Eccezione durante la gestione di un update:", exc_info=context.error)

    # Cattura con Sentry
    if SENTRY_DSN:
        sentry_sdk.capture_exception(context.error)

    # Prepara messaggio per admin
    tb_string = "".join(traceback.format_exception(None, context.error, context.error.__traceback__))

    # Info sull'update che ha causato l'errore
    update_str = ""
    if isinstance(update, Update):
        if update.effective_user:
            update_str = f"User: {update.effective_user.id}\n"
        if update.effective_chat:
            update_str += f"Chat: {update.effective_chat.id}\n"

    error_msg = f"{update_str}\n{tb_string}"

    # Salva ultimo errore per /stats
    set_last_error(str(context.error))

    # Notifica admin
    await notify_admin_error(context.bot, error_msg, "EXCEPTION")


async def on_startup(application: Application) -> None:
    """Callback eseguito all'avvio del bot."""
    set_bot_start_time()
    logger.info("Bot avviato correttamente")
    await notify_admin_error(application.bot, "Bot avviato correttamente", "STARTUP")


async def on_shutdown(application: Application) -> None:
    """Callback eseguito allo shutdown del bot."""
    logger.info("Bot in arresto...")
    try:
        await notify_admin_error(application.bot, "Bot in arresto (shutdown)", "SHUTDOWN")
    except Exception:
        pass


def main():
    """Avvia il bot"""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN non configurato!")
        sys.exit(1)

    logger.info("Avvio Slappy Bot...")

    # Crea applicazione
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Registra handlers
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("morning", handle_morning))
    application.add_handler(CommandHandler("stats", handle_stats))
    application.add_handler(CommandHandler("testbriefing", handle_test_briefing))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    application.add_handler(CallbackQueryHandler(callback_handler))

    # Error handler globale
    application.add_error_handler(error_handler)

    # Callback startup/shutdown per notifiche admin
    application.post_init = on_startup
    application.post_shutdown = on_shutdown

    # Configura job per morning briefing usando JobQueue integrato
    async def scheduled_morning_briefing(context):
        """Wrapper per chiamare il morning briefing"""
        logger.info("Scheduler: avvio morning briefing alle 8:00")
        await send_morning_briefing_to_all(context.bot)

    # Ogni giorno alle 8:00 ora italiana
    rome_tz = pytz.timezone("Europe/Rome")
    morning_time = dt_time(hour=8, minute=0, second=0, tzinfo=rome_tz)

    job_queue = application.job_queue
    job_queue.run_daily(
        scheduled_morning_briefing,
        time=morning_time,
        name="morning_briefing"
    )
    logger.info("Job morning briefing attivato (ogni giorno alle 8:00)")

    # Modalità webhook (produzione) o polling (sviluppo)
    if WEBHOOK_URL:
        logger.info(f"Avvio in modalità WEBHOOK su porta {PORT}")
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TELEGRAM_BOT_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_BOT_TOKEN}"
        )
    else:
        logger.info("Avvio in modalità POLLING (sviluppo)")
        application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
