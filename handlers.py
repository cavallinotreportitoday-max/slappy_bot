"""
Handler Telegram - logica identica al workflow n8n SLAPPY_v47_LOCK
"""
import asyncio
import json
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import TelegramError

API_TIMEOUT = 5  # Timeout per chiamate API esterne in secondi

import database as db
from config import ADMIN_CHAT_ID
from validators import validate_name, validate_dob
from meteo_api import get_meteo_forecast, get_weather_emoji, get_weather_description

logger = logging.getLogger(__name__)

# Variabili globali per tracking stats
_bot_start_time = None
_last_error = None


def set_bot_start_time():
    """Chiamato all'avvio del bot per tracciare uptime."""
    global _bot_start_time
    _bot_start_time = datetime.now()


def set_last_error(error_msg: str):
    """Chiamato quando si verifica un errore per tracciarlo."""
    global _last_error
    _last_error = {
        "time": datetime.now(),
        "message": error_msg[:500]
    }


def log_action(chat_id: int, stato: str, action: str, extra: dict = None):
    """Logging JSON per debug"""
    log_data = {"chat_id": chat_id, "stato": stato, "action": action}
    if extra:
        log_data.update(extra)
    logger.info(json.dumps(log_data))


def get_action(
    is_start: bool,
    is_callback: bool,
    callback_data: str,
    message_text: str,
    user: dict,
    config: dict
) -> str:
    """
    Determina l'azione da eseguire - LOGICA IDENTICA a 04_Prepara_Contesto n8n
    """
    exists = user is not None
    stato = user.get("stato_onboarding", "new") if user else "new"

    max_utenti = int(config.get("max_utenti", "99999"))
    utenti_count = int(config.get("utenti_count", "0"))

    cb = callback_data or ""
    is_text = not is_callback and not is_start and bool(message_text)

    # Check limite utenti per nuovi utenti
    if is_start and not exists and utenti_count >= max_utenti:
        return "limite_raggiunto"

    # /start
    if is_start:
        if not exists:
            return "new_user"
        elif stato == "completo":
            return "returning"
        elif stato == "uscito":
            return "new_user"
        # FIX: Resume onboarding per utenti a metà
        elif stato == "lingua_ok":
            return "resume_privacy"
        elif stato == "privacy_ok":
            return "resume_nome"
        elif stato == "nome_ok":
            return "resume_data"
        else:
            return "new_user"

    # Callback bottoni
    if cb.startswith("lang_"):
        return "set_lang"
    if cb == "privacy_accept":
        return "privacy_yes"
    if cb == "privacy_reject":
        return "privacy_no"
    if cb.startswith("menu_"):
        return "menu"

    # Input testo durante onboarding
    if is_text and stato == "privacy_ok":
        return "input_name"
    if is_text and stato == "nome_ok":
        return "input_dob"

    # Utente completo che scrive
    if stato == "completo":
        return "menu"

    # FIX: Utenti a metà onboarding che mandano testo → riprendi da dove erano
    if is_text and stato == "lingua_ok":
        return "resume_privacy"
    if is_text and stato == "new":
        return "new_user"

    return "fallback"


async def delete_message_safe(context: ContextTypes.DEFAULT_TYPE, chat_id: int, msg_id: int):
    """Cancella messaggio in modo sicuro (ignora errori)"""
    if not msg_id:
        return
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except TelegramError as e:
        logger.debug(f"Impossibile cancellare messaggio {msg_id}: {e}")


async def answer_callback_safe(callback_query):
    """Risponde al callback in modo sicuro"""
    try:
        await callback_query.answer()
    except TelegramError:
        pass


async def handle_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler principale - elabora ogni update"""

    # Estrai dati (nodo 02_Estrai_Dati)
    chat_id = None
    callback_data = ""
    message_text = ""
    is_start = False
    is_callback = False
    user_msg_id = None
    callback_msg_id = None
    update_id = update.update_id

    if update.callback_query:
        is_callback = True
        chat_id = update.callback_query.message.chat.id
        callback_data = update.callback_query.data or ""
        callback_msg_id = update.callback_query.message.message_id
    elif update.message:
        chat_id = update.message.chat.id
        message_text = update.message.text or ""
        user_msg_id = update.message.message_id
        is_start = message_text.strip().lower().startswith("/start")

    if not chat_id:
        return

    # Cerca utente (nodo 03_DB_Cerca_Utente)
    user = db.get_user(chat_id)

    # Check duplicato update_id (FIX scalabilità)
    if user and user.get("last_update_id", 0) >= update_id:
        logger.info(f"Update duplicato ignorato: {chat_id}/{update_id}")
        if is_callback:
            await answer_callback_safe(update.callback_query)
        return

    # Carica testi e config (nodi 03B, 03C - con cache)
    config = db.get_config()
    lingua = user.get("lingua", "it") if user else "it"
    nome = user.get("nome", "") if user else ""
    last_bot_msg_id = user.get("last_bot_msg_id") if user else None
    last_bot_msg_step = user.get("last_bot_msg_step") if user else None
    stato = user.get("stato_onboarding", "new") if user else "new"

    # Determina azione (nodo 04_Prepara_Contesto)
    action = get_action(
        is_start=is_start,
        is_callback=is_callback,
        callback_data=callback_data,
        message_text=message_text,
        user=user,
        config=config
    )

    log_action(chat_id, stato, action, {"update_id": update_id})

    # Gestione cancellazione messaggi (nodi 05-11)
    if is_callback:
        # I callback handler gestiranno query.answer() e edit_message_text()
        pass
    elif user_msg_id and not is_start:
        # Cancella messaggio utente durante onboarding
        await delete_message_safe(context, chat_id, user_msg_id)
        # Cancella messaggio bot precedente se stesso step
        if last_bot_msg_id:
            await delete_message_safe(context, chat_id, last_bot_msg_id)

    # Esegui azione (nodo 12_Smista_Azione)
    callback_query = update.callback_query if is_callback else None
    await execute_action(action, update, context, chat_id, update_id, user, lingua, nome, config, callback_data, message_text, callback_query)


async def execute_action(
    action: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    update_id: int,
    user: dict,
    lingua: str,
    nome: str,
    config: dict,
    callback_data: str,
    message_text: str,
    callback_query=None
):
    """Esegue l'azione determinata"""

    if action == "new_user":
        await action_new_user(context, chat_id, update_id)

    elif action == "set_lang":
        await action_set_lang(context, chat_id, update_id, callback_data, callback_query)

    elif action == "privacy_yes":
        await action_privacy_yes(context, chat_id, update_id, lingua, callback_query)

    elif action == "privacy_no":
        await action_privacy_no(context, chat_id, update_id, lingua, callback_query)

    elif action == "resume_privacy":
        await action_resume_privacy(context, chat_id, update_id, lingua)

    elif action == "resume_nome":
        await action_resume_nome(context, chat_id, update_id, lingua)

    elif action == "resume_data":
        await action_resume_data(context, chat_id, update_id, lingua, nome)

    elif action == "input_name":
        await action_input_name(context, chat_id, update_id, message_text, lingua)

    elif action == "input_dob":
        await action_input_dob(context, chat_id, update_id, message_text, lingua, user)

    elif action == "returning":
        await action_returning(context, chat_id, update_id, nome, lingua)

    elif action == "menu":
        await action_menu(context, chat_id, update_id, callback_data, nome, lingua, callback_query)

    elif action == "limite_raggiunto":
        await action_limite_raggiunto(context, chat_id, config, lingua)

    else:  # fallback
        await action_fallback(context, chat_id, update_id, lingua)


# ============================================================
# AZIONI SPECIFICHE
# ============================================================

async def action_new_user(context: ContextTypes.DEFAULT_TYPE, chat_id: int, update_id: int):
    """Nuovo utente - crea record e mostra scelta lingua (nodi 13, 13B, 14)"""
    db.create_user(chat_id)
    db.increment_utenti_count()

    # Messaggio scelta lingua
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🇮🇹", callback_data="lang_it"),
            InlineKeyboardButton("🇬🇧", callback_data="lang_en"),
            InlineKeyboardButton("🇩🇪", callback_data="lang_de")
        ]
    ])

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text="🏖️ Cavallino-Treporti",
        reply_markup=keyboard
    )

    db.save_last_bot_msg(chat_id, msg.message_id, "lingua")
    db.update_user(chat_id, {"last_update_id": update_id})


async def action_set_lang(context: ContextTypes.DEFAULT_TYPE, chat_id: int, update_id: int, callback_data: str, query=None):
    """Imposta lingua e mostra privacy (nodi 15, 16, 17)"""
    # Rispondi al callback SUBITO
    if query:
        await query.answer()

    lingua = callback_data.replace("lang_", "")
    if lingua not in ("it", "en", "de"):
        lingua = "it"

    db.save_lingua(chat_id, lingua, update_id)

    # Prepara messaggio privacy
    text = db.get_text("step2_testo", lingua)
    btn_si = db.get_text("btn_privacy_si", lingua)
    btn_no = db.get_text("btn_privacy_no", lingua)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(btn_si, callback_data="privacy_accept"),
            InlineKeyboardButton(btn_no, callback_data="privacy_reject")
        ]
    ])

    # Edita messaggio esistente invece di mandarne uno nuovo
    if query:
        await query.edit_message_text(
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        db.save_last_bot_msg(chat_id, query.message.message_id, "privacy")
    else:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        db.save_last_bot_msg(chat_id, msg.message_id, "privacy")


async def action_privacy_yes(context: ContextTypes.DEFAULT_TYPE, chat_id: int, update_id: int, lingua: str, query=None):
    """Privacy accettata - chiedi nome (nodi 18, 19, 20)"""
    # Rispondi al callback SUBITO
    if query:
        await query.answer()

    db.save_privacy_ok(chat_id, update_id)

    text = db.get_text("step3_chiedi_nome", lingua)

    # Edita messaggio esistente invece di mandarne uno nuovo
    if query:
        await query.edit_message_text(
            text=text,
            parse_mode="HTML"
        )
        db.save_last_bot_msg(chat_id, query.message.message_id, "nome")
    else:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML"
        )
        db.save_last_bot_msg(chat_id, msg.message_id, "nome")


async def action_privacy_no(context: ContextTypes.DEFAULT_TYPE, chat_id: int, update_id: int, lingua: str, query=None):
    """Privacy rifiutata - messaggio uscita (nodi 21, 22)"""
    # Rispondi al callback SUBITO
    if query:
        await query.answer()

    db.save_privacy_no(chat_id, update_id)

    text = db.get_text("msg_uscita", lingua)

    # Edita messaggio esistente invece di mandarne uno nuovo
    if query:
        await query.edit_message_text(
            text=text,
            parse_mode="HTML"
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML"
        )


async def action_resume_privacy(context: ContextTypes.DEFAULT_TYPE, chat_id: int, update_id: int, lingua: str):
    """Resume onboarding - mostra privacy"""
    text = db.get_text("step2_testo", lingua)
    btn_si = db.get_text("btn_privacy_si", lingua)
    btn_no = db.get_text("btn_privacy_no", lingua)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(btn_si, callback_data="privacy_accept"),
            InlineKeyboardButton(btn_no, callback_data="privacy_reject")
        ]
    ])

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )

    db.save_last_bot_msg(chat_id, msg.message_id, "privacy")
    db.update_user(chat_id, {"last_update_id": update_id})


async def action_resume_nome(context: ContextTypes.DEFAULT_TYPE, chat_id: int, update_id: int, lingua: str):
    """Resume onboarding - chiedi nome"""
    text = db.get_text("step3_chiedi_nome", lingua)

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="HTML"
    )

    db.save_last_bot_msg(chat_id, msg.message_id, "nome")
    db.update_user(chat_id, {"last_update_id": update_id})


async def action_resume_data(context: ContextTypes.DEFAULT_TYPE, chat_id: int, update_id: int, lingua: str, nome: str):
    """Resume onboarding - chiedi data nascita"""
    text = db.get_text("step4_chiedi_data", lingua)
    if nome:
        text = text.replace("{nome}", nome)

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="HTML"
    )

    db.save_last_bot_msg(chat_id, msg.message_id, "data")
    db.update_user(chat_id, {"last_update_id": update_id})


async def action_input_name(context: ContextTypes.DEFAULT_TYPE, chat_id: int, update_id: int, message_text: str, lingua: str):
    """Valida e salva nome (nodi 23-29)"""
    is_valid, nome_pulito = validate_name(message_text)

    if is_valid:
        # Nome OK - salva e chiedi data
        db.save_nome(chat_id, nome_pulito, update_id)

        text = db.get_text("step4_chiedi_data", lingua)
        text = text.replace("{nome}", nome_pulito)

        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML"
        )

        db.save_last_bot_msg(chat_id, msg.message_id, "data")

    else:
        # Nome non valido
        text = db.get_text("msg_errore_nome", lingua)

        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML"
        )

        db.save_last_bot_msg(chat_id, msg.message_id, "nome")
        db.update_user(chat_id, {"last_update_id": update_id})


async def action_input_dob(context: ContextTypes.DEFAULT_TYPE, chat_id: int, update_id: int, message_text: str, lingua: str, user: dict):
    """Valida e salva data nascita (nodi 30-35)"""
    is_valid, data_str, is_minorenne = validate_dob(message_text)
    nome = user.get("nome", "") if user else ""

    if is_valid:
        # Data OK - salva e mostra completamento
        db.save_data_nascita(chat_id, data_str, is_minorenne, update_id)

        keyboard = get_menu_keyboard(lingua)

        text = f"✅ Tutto pronto, {nome}!\nOra esplora il menu 👇"

        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    else:
        # Data non valida
        error_count = db.increment_dob_error(chat_id, update_id)

        text = db.get_text("msg_errore_data", lingua)

        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML"
        )

        db.save_last_bot_msg(chat_id, msg.message_id, "data")


async def action_returning(context: ContextTypes.DEFAULT_TYPE, chat_id: int, update_id: int, nome: str, lingua: str):
    """Utente che ritorna - mostra menu (nodo 36)"""
    keyboard = get_menu_keyboard(lingua)

    # Data formattata per lingua
    now = datetime.now()
    giorni = {
        "it": ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"],
        "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
        "de": ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    }
    mesi = {
        "it": ["Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"],
        "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
        "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
    }
    giorno_nome = giorni.get(lingua, giorni["it"])[now.weekday()]
    mese_nome = mesi.get(lingua, mesi["it"])[now.month - 1]
    data_str = f"📅 {giorno_nome} {now.day} {mese_nome}"

    # Meteo attuale (con timeout breve)
    meteo_str = ""
    try:
        meteo = await asyncio.wait_for(get_meteo_forecast(), timeout=3)
        if meteo and meteo.get("current"):
            current = meteo["current"]
            temp = current.get("temperature", "")
            weather_code = current.get("weather_code", 0)
            emoji = get_weather_emoji(weather_code)
            desc = get_weather_description(weather_code, lingua)
            if temp:
                meteo_str = f"{emoji} {temp}°C - {desc}"
    except Exception:
        pass  # Ignora errori meteo, mostra solo data

    # Costruisci messaggio
    welcome = {"it": "Cosa posso fare per te?", "en": "What can I do for you?", "de": "Was kann ich für dich tun?"}.get(lingua, "Cosa posso fare per te?")

    text = f"{data_str}\n"
    if meteo_str:
        text += f"{meteo_str}\n"
    evento_str = get_evento_oggi(lingua)
    if evento_str:
        text += f"{evento_str}\n"
    text += f"\n👋 Bentornato, {nome}!\n{welcome}"

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )

    db.update_user(chat_id, {"last_update_id": update_id})


async def action_menu(context: ContextTypes.DEFAULT_TYPE, chat_id: int, update_id: int, callback_data: str, nome: str, lingua: str, query=None):
    """Gestisce click su menu"""
    menu_key = callback_data.replace("menu_", "") if callback_data.startswith("menu_") else ""

    # Handler speciali per meteo/mare/maree/attività
    if menu_key == "meteo":
        await handle_meteo(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if menu_key == "mare":
        await handle_mare(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if menu_key == "maree":
        await handle_maree(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if menu_key in ("idee", "cosa_fare"):
        await handle_cosa_fare(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if menu_key == "pioggia":
        await handle_pioggia(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if menu_key == "spiagge":
        await handle_spiagge(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if menu_key == "fortini":
        await handle_fortini(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if menu_key == "attivita":
        await handle_attivita(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if menu_key == "eventi":
        await handle_eventi(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if menu_key == "trasporti":
        await handle_trasporti(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if menu_key == "ristoranti":
        await handle_ristoranti(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if menu_key == "sos":
        await handle_sos(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if menu_key == "sos_emergenza":
        await handle_sos_emergenza(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if menu_key == "sos_guardia_medica":
        await handle_sos_guardia_medica(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if menu_key == "sos_ospedali":
        await handle_sos_ospedali(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if menu_key == "sos_farmacie":
        await handle_sos_farmacie(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if menu_key == "sos_numeri":
        await handle_sos_numeri(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if menu_key == "back":
        # Rispondi al callback SUBITO
        if query:
            await query.answer()

        # Torna al menu principale
        keyboard = get_menu_keyboard(lingua)

        # Data formattata per lingua
        now = datetime.now()
        giorni = {
            "it": ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"],
            "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
            "de": ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
        }
        mesi = {
            "it": ["Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"],
            "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
            "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
        }
        giorno_nome = giorni.get(lingua, giorni["it"])[now.weekday()]
        mese_nome = mesi.get(lingua, mesi["it"])[now.month - 1]
        data_str = f"📅 {giorno_nome} {now.day} {mese_nome}"

        # Meteo attuale (con timeout breve)
        meteo_str = ""
        try:
            meteo = await asyncio.wait_for(get_meteo_forecast(), timeout=3)
            if meteo and meteo.get("current"):
                current = meteo["current"]
                temp = current.get("temperature", "")
                weather_code = current.get("weather_code", 0)
                emoji = get_weather_emoji(weather_code)
                desc = get_weather_description(weather_code, lingua)
                if temp:
                    meteo_str = f"{emoji} {temp}°C - {desc}"
        except Exception:
            pass

        # Costruisci messaggio
        welcome = {"it": "Cosa posso fare per te?", "en": "What can I do for you?", "de": "Was kann ich für dich tun?"}.get(lingua, "Cosa posso fare per te?")

        text = f"{data_str}\n"
        if meteo_str:
            text += f"{meteo_str}\n"
        evento_str = get_evento_oggi(lingua)
        if evento_str:
            text += f"{evento_str}\n"
        text += f"\n👋 Bentornato, {nome}!\n{welcome}"

        # Edita messaggio esistente invece di mandarne uno nuovo
        if query:
            await query.edit_message_text(
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    # Mappa callback -> chiave testo (menu legacy)
    menu_responses = {
    }

    # Rispondi al callback SUBITO
    if query:
        await query.answer()

    if menu_key in menu_responses:
        text = db.get_text(menu_responses[menu_key], lingua)
        if "{nome}" in text:
            text = text.replace("{nome}", nome)

        keyboard = get_menu_keyboard(lingua)

        # Edita messaggio esistente invece di mandarne uno nuovo
        if query:
            await query.edit_message_text(
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
    else:
        # Menu generico (utente scrive qualcosa)
        keyboard = get_menu_keyboard(lingua)

        # Data formattata per lingua
        now = datetime.now()
        giorni = {
            "it": ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"],
            "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
            "de": ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
        }
        mesi = {
            "it": ["Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"],
            "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
            "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
        }
        giorno_nome = giorni.get(lingua, giorni["it"])[now.weekday()]
        mese_nome = mesi.get(lingua, mesi["it"])[now.month - 1]
        data_str = f"📅 {giorno_nome} {now.day} {mese_nome}"

        # Meteo attuale (con timeout breve)
        meteo_str = ""
        try:
            meteo = await asyncio.wait_for(get_meteo_forecast(), timeout=3)
            if meteo and meteo.get("current"):
                current = meteo["current"]
                temp = current.get("temperature", "")
                weather_code = current.get("weather_code", 0)
                emoji = get_weather_emoji(weather_code)
                desc = get_weather_description(weather_code, lingua)
                if temp:
                    meteo_str = f"{emoji} {temp}°C - {desc}"
        except Exception:
            pass  # Ignora errori meteo, mostra solo data

        # Costruisci messaggio
        welcome = {"it": "Cosa posso fare per te?", "en": "What can I do for you?", "de": "Was kann ich für dich tun?"}.get(lingua, "Cosa posso fare per te?")

        text = f"{data_str}\n"
        if meteo_str:
            text += f"{meteo_str}\n"
        evento_str = get_evento_oggi(lingua)
        if evento_str:
            text += f"{evento_str}\n"
        text += f"\n👋 Bentornato, {nome}!\n{welcome}"

        # Edita messaggio esistente invece di mandarne uno nuovo
        if query:
            await query.edit_message_text(
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )

    db.update_user(chat_id, {"last_update_id": update_id})


async def action_limite_raggiunto(context: ContextTypes.DEFAULT_TYPE, chat_id: int, config: dict, lingua: str):
    """Limite utenti raggiunto (nodo 38)"""
    canale_link = config.get("canale_telegram", "https://t.me/tuocanale")
    text = db.get_text("msg_limite", lingua)
    text = text.replace("{canale}", canale_link)

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="HTML"
    )


async def action_fallback(context: ContextTypes.DEFAULT_TYPE, chat_id: int, update_id: int, lingua: str):
    """Messaggio non capito (nodo 37)"""
    text = db.get_text("msg_fallback", lingua)

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="HTML"
    )

    db.update_user(chat_id, {"last_update_id": update_id})


def get_evento_oggi(lingua: str) -> str:
    """
    Restituisce l'evento del giorno se presente, altrimenti stringa vuota.
    Legge dalla tabella 'eventi' in Supabase.
    """
    evento_oggi = db.get_evento_oggi(lingua)

    if evento_oggi:
        labels = {
            "it": "Oggi",
            "en": "Today",
            "de": "Heute"
        }
        label = labels.get(lingua, labels["it"])
        return f"🎪 {label}: {evento_oggi}"

    return ""


def get_menu_keyboard(lingua: str) -> InlineKeyboardMarkup:
    """Genera tastiera menu principale"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("☀️ Meteo", callback_data="menu_meteo"),
            InlineKeyboardButton("🎪 Eventi", callback_data="menu_eventi")
        ],
        [
            InlineKeyboardButton("📍 Cosa fare", callback_data="menu_cosa_fare"),
            InlineKeyboardButton("🚌 Trasporti", callback_data="menu_trasporti")
        ],
        [
            InlineKeyboardButton("🍽️ Ristoranti", callback_data="menu_ristoranti"),
            InlineKeyboardButton("🆘 Emergenza", callback_data="menu_sos")
        ]
    ])


# ============================================================
# HANDLER METEO E ATTIVITA'
# ============================================================

async def handle_meteo(context, chat_id: int, lingua: str, query=None):
    """
    Mostra meteo atmosferico con previsioni.
    """
    # Rispondi al callback SUBITO
    if query:
        await query.answer()

    from meteo_api import get_meteo_forecast, get_weather_emoji, get_weather_description, get_wind_direction_text

    # Chiama API con timeout di 5 secondi
    try:
        meteo = await asyncio.wait_for(get_meteo_forecast(), timeout=API_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("Timeout chiamata API meteo")
        meteo = None
    except Exception as e:
        logger.error(f"Errore API meteo: {e}")
        meteo = None

    if not meteo:
        text = db.get_text("meteo_errore", lingua)
        if text == "meteo_errore":
            text = "⚠️ Impossibile ottenere dati meteo. Riprova più tardi."
    else:
        current = meteo["current"]
        daily = meteo["daily"]

        weather_code = current.get("weather_code", 0)
        emoji = get_weather_emoji(weather_code)
        desc = get_weather_description(weather_code, lingua)

        temp = current.get("temperature", "N/D")
        feels = current.get("feels_like", "N/D")
        humidity = current.get("humidity", "N/D")
        wind = current.get("wind_speed", "N/D")
        wind_dir = get_wind_direction_text(current.get("wind_direction"), lingua)

        # Intestazione
        header = {
            "it": "Meteo Cavallino-Treporti",
            "en": "Weather Cavallino-Treporti",
            "de": "Wetter Cavallino-Treporti"
        }.get(lingua, "Meteo Cavallino-Treporti")

        now_label = {"it": "Ora", "en": "Now", "de": "Jetzt"}.get(lingua, "Ora")
        feels_label = {"it": "Percepita", "en": "Feels like", "de": "Gefühlt"}.get(lingua, "Percepita")
        humidity_label = {"it": "Umidità", "en": "Humidity", "de": "Feuchtigkeit"}.get(lingua, "Umidità")
        wind_label = {"it": "Vento", "en": "Wind", "de": "Wind"}.get(lingua, "Vento")
        forecast_label = {"it": "Prossimi giorni", "en": "Next days", "de": "Nächste Tage"}.get(lingua, "Prossimi giorni")

        text = f"{emoji} <b>{header}</b>\n\n"
        text += f"<b>{now_label}:</b> {desc}\n"
        text += f"🌡️ {temp}°C ({feels_label}: {feels}°C)\n"
        text += f"💧 {humidity_label}: {humidity}%\n"
        text += f"💨 {wind_label}: {wind} km/h {wind_dir}\n\n"

        text += f"<b>📅 {forecast_label}:</b>\n"
        for i in range(min(3, len(daily["dates"]))):
            date_str = daily["dates"][i]
            code = daily["weather_codes"][i] if i < len(daily["weather_codes"]) else 0
            tmax = daily["temp_max"][i] if i < len(daily["temp_max"]) else "N/D"
            tmin = daily["temp_min"][i] if i < len(daily["temp_min"]) else "N/D"
            emoji_day = get_weather_emoji(code)
            text += f"{emoji_day} {date_str}: {tmin}°/{tmax}°\n"

        # Consiglio meteo
        condizione = "sole" if weather_code in (0, 1, 2) else "pioggia" if weather_code >= 51 else "nuvole"
        consiglio = db.get_consiglio_meteo(condizione, lingua)
        if consiglio:
            text += f"\n💡 {consiglio}"

        text += "\n\n🦭 <i>SLAPPY</i>"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌊 Mare", callback_data="menu_mare"),
            InlineKeyboardButton("🌊 Maree", callback_data="menu_maree")
        ],
        [InlineKeyboardButton("◀️ Menu", callback_data="menu_back")]
    ])

    # Edita messaggio esistente invece di mandarne uno nuovo
    if query:
        await query.edit_message_text(
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )


async def handle_mare(context, chat_id: int, lingua: str, query=None):
    """
    Mostra condizioni mare (onde, direzione, periodo).
    """
    # Rispondi al callback SUBITO
    if query:
        await query.answer()

    from meteo_api import get_marine_conditions, get_wave_condition, get_wind_direction_text

    # Chiama API con timeout di 5 secondi
    try:
        marine = await asyncio.wait_for(get_marine_conditions(), timeout=API_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("Timeout chiamata API mare")
        marine = None
    except Exception as e:
        logger.error(f"Errore API mare: {e}")
        marine = None

    if not marine:
        text = db.get_text("mare_errore", lingua)
        if text == "mare_errore":
            text = "⚠️ Impossibile ottenere dati mare. Riprova più tardi."
    else:
        current = marine["current"]
        daily = marine["daily"]

        wave_height = current.get("wave_height")
        wave_dir = current.get("wave_direction")
        wave_period = current.get("wave_period")

        condition = get_wave_condition(wave_height, lingua)
        dir_text = get_wind_direction_text(wave_dir, lingua)

        # Intestazione
        header = {
            "it": "CONDIZIONI MARE",
            "en": "SEA CONDITIONS",
            "de": "MEERESBEDINGUNGEN"
        }.get(lingua, "CONDIZIONI MARE")

        height_label = {"it": "Altezza onde", "en": "Wave height", "de": "Wellenhöhe"}.get(lingua, "Altezza onde")
        direction_label = {"it": "Direzione", "en": "Direction", "de": "Richtung"}.get(lingua, "Direzione")
        period_label = {"it": "Periodo", "en": "Period", "de": "Periode"}.get(lingua, "Periodo")
        forecast_label = {"it": "Prossimi giorni", "en": "Next days", "de": "Nächste Tage"}.get(lingua, "Prossimi giorni")

        text = f"🌊 <b>{header}</b>\n\n"
        text += f"<b>{condition}</b>\n"
        text += f"📏 {height_label}: {wave_height or 'N/D'} m\n"
        text += f"🧭 {direction_label}: {dir_text}\n"
        text += f"⏱️ {period_label}: {wave_period or 'N/D'} s\n\n"

        text += f"<b>📅 {forecast_label}:</b>\n"
        for i in range(min(3, len(daily["dates"]))):
            date_str = daily["dates"][i]
            max_h = daily["wave_height_max"][i] if i < len(daily["wave_height_max"]) else "N/D"
            text += f"🌊 {date_str}: max {max_h} m\n"

        # Consiglio
        if wave_height and wave_height > 1.0:
            consiglio = db.get_consiglio_meteo("mare_mosso", lingua)
            if consiglio:
                text += f"\n⚠️ {consiglio}"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("☀️ Meteo", callback_data="menu_meteo"),
            InlineKeyboardButton("🌊 Maree", callback_data="menu_maree")
        ],
        [InlineKeyboardButton("◀️ Menu", callback_data="menu_back")]
    ])

    # Edita messaggio esistente invece di mandarne uno nuovo
    if query:
        await query.edit_message_text(
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )


async def handle_maree(context, chat_id: int, lingua: str, query=None):
    """
    Mostra orari maree (alta/bassa marea).
    """
    # Rispondi al callback SUBITO
    if query:
        await query.answer()

    from meteo_api import get_tides

    # Chiama API con timeout di 5 secondi
    try:
        tides = await asyncio.wait_for(get_tides(), timeout=API_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("Timeout chiamata API maree")
        tides = None
    except Exception as e:
        logger.error(f"Errore API maree: {e}")
        tides = None

    if not tides or not tides.get("extremes"):
        text = db.get_text("maree_errore", lingua)
        if text == "maree_errore":
            text = "⚠️ Impossibile ottenere dati maree. Riprova più tardi."
    else:
        header = {
            "it": "Maree Cavallino-Treporti",
            "en": "Tides Cavallino-Treporti",
            "de": "Gezeiten Cavallino-Treporti"
        }.get(lingua, "Maree Cavallino-Treporti")

        high_label = {"it": "Alta", "en": "High", "de": "Hoch"}.get(lingua, "Alta")
        low_label = {"it": "Bassa", "en": "Low", "de": "Niedrig"}.get(lingua, "Bassa")

        text = f"🌊 <b>{header}</b>\n\n"

        current_date = None
        for tide in tides["extremes"]:
            tide_date = tide.get("date")
            if tide_date != current_date:
                text += f"\n<b>📅 {tide_date}</b>\n"
                current_date = tide_date

            tide_type = tide.get("type")
            tide_time = tide.get("time")
            tide_height = tide.get("height")

            if tide_type == "high":
                emoji = "⬆️"
                label = high_label
            else:
                emoji = "⬇️"
                label = low_label

            height_str = f" ({tide_height:.2f}m)" if tide_height else ""
            text += f"{emoji} {tide_time} - {label}{height_str}\n"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("☀️ Meteo", callback_data="menu_meteo"),
            InlineKeyboardButton("🌊 Mare", callback_data="menu_mare")
        ],
        [InlineKeyboardButton("◀️ Menu", callback_data="menu_back")]
    ])

    # Edita messaggio esistente invece di mandarne uno nuovo
    if query:
        await query.edit_message_text(
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )


async def handle_cosa_fare(context, chat_id: int, lingua: str, query=None):
    """
    Sottomenu con opzioni: spiagge, fortini, attività, pioggia.
    """
    # Rispondi al callback SUBITO
    if query:
        await query.answer()

    header = {
        "it": "COSA FARE OGGI?",
        "en": "WHAT TO DO TODAY?",
        "de": "WAS TUN HEUTE?"
    }.get(lingua, "COSA FARE OGGI?")

    subtitle = {
        "it": "Scegli una categoria:",
        "en": "Choose a category:",
        "de": "Wähle eine Kategorie:"
    }.get(lingua, "Scegli una categoria:")

    text = f"🧭 <b>{header}</b>\n\n{subtitle}"

    btn_spiagge = {"it": "🏖️ Spiagge", "en": "🏖️ Beaches", "de": "🏖️ Strände"}.get(lingua, "🏖️ Spiagge")
    btn_fortini = {"it": "🏰 Fortini", "en": "🏰 Forts", "de": "🏰 Festungen"}.get(lingua, "🏰 Fortini")
    btn_attivita = {"it": "🎯 Attività", "en": "🎯 Activities", "de": "🎯 Aktivitäten"}.get(lingua, "🎯 Attività")
    btn_pioggia = {"it": "🌧️ Pioggia", "en": "🌧️ Rainy day", "de": "🌧️ Regentag"}.get(lingua, "🌧️ Pioggia")

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(btn_spiagge, callback_data="menu_spiagge"),
            InlineKeyboardButton(btn_fortini, callback_data="menu_fortini")
        ],
        [
            InlineKeyboardButton(btn_attivita, callback_data="menu_attivita"),
            InlineKeyboardButton(btn_pioggia, callback_data="menu_pioggia")
        ],
        [InlineKeyboardButton("◀️ Menu", callback_data="menu_back")]
    ])

    # Edita messaggio esistente invece di mandarne uno nuovo
    if query:
        await query.edit_message_text(
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )


async def handle_pioggia(context, chat_id: int, lingua: str, query=None):
    """
    Mostra idee per giornate di pioggia.
    """
    # Rispondi al callback SUBITO
    if query:
        await query.answer()

    text = db.get_text("idee_pioggia", lingua)

    if text == "idee_pioggia":
        # Fallback se testo non trovato in DB
        fallback = {
            "it": """🌧️ <b>IDEE PER GIORNATE DI PIOGGIA</b>

Quando piove a Cavallino-Treporti:

🛍️ <b>Shopping</b>
• Centro commerciale Valecenter (Marcon)
• Outlet Noventa di Piave

🎳 <b>Divertimento</b>
• Bowling e sale giochi
• Cinema multisala

🏛️ <b>Cultura</b>
• Musei di Venezia
• Basilica di San Marco

🍕 <b>Gastronomia</b>
• Corso di cucina
• Degustazione vini locali

💆 <b>Relax</b>
• Spa e centri benessere
• Terme di Jesolo""",
            "en": """🌧️ <b>RAINY DAY IDEAS</b>

When it rains in Cavallino-Treporti:

🛍️ <b>Shopping</b>
• Valecenter shopping mall (Marcon)
• Noventa di Piave Outlet

🎳 <b>Entertainment</b>
• Bowling and arcades
• Multiplex cinema

🏛️ <b>Culture</b>
• Venice museums
• St. Mark's Basilica

🍕 <b>Gastronomy</b>
• Cooking classes
• Local wine tasting

💆 <b>Relax</b>
• Spa and wellness centers
• Jesolo thermal baths""",
            "de": """🌧️ <b>IDEEN FÜR REGENTAGE</b>

Wenn es in Cavallino-Treporti regnet:

🛍️ <b>Einkaufen</b>
• Einkaufszentrum Valecenter (Marcon)
• Outlet Noventa di Piave

🎳 <b>Unterhaltung</b>
• Bowling und Spielhallen
• Multiplex-Kino

🏛️ <b>Kultur</b>
• Museen von Venedig
• Markusdom

🍕 <b>Gastronomie</b>
• Kochkurse
• Lokale Weinverkostung

💆 <b>Entspannung</b>
• Spa und Wellnesszentren
• Therme Jesolo"""
        }
        text = fallback.get(lingua, fallback["it"])

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧭 Altre idee", callback_data="menu_cosa_fare")],
        [InlineKeyboardButton("◀️ Menu", callback_data="menu_back")]
    ])

    # Edita messaggio esistente invece di mandarne uno nuovo
    if query:
        await query.edit_message_text(
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )


async def handle_spiagge(context, chat_id: int, lingua: str, query=None):
    """
    Mostra informazioni sulle spiagge di Cavallino-Treporti.
    """
    # Rispondi al callback SUBITO
    if query:
        await query.answer()

    text = db.get_text("info_spiagge", lingua)

    if text == "info_spiagge":
        fallback = {
            "it": """🏖️ <b>Spiagge di Cavallino-Treporti</b>

15 km di litorale sabbioso tra la laguna e il mare!

🏖️ <b>Spiaggia di Punta Sabbioni</b>
• Sabbia fine, acque basse
• Ideale per famiglie con bambini
• Vaporetti per Venezia nelle vicinanze

🏖️ <b>Spiaggia di Cavallino</b>
• Ampia e ben attrezzata
• Stabilimenti balneari e spiaggia libera
• Sport acquatici disponibili

🏖️ <b>Spiaggia di Ca' Savio</b>
• Tranquilla e rilassante
• Pineta alle spalle
• Perfetta per passeggiate

🏖️ <b>Spiaggia di Treporti</b>
• Vista sulla laguna
• Tramonti spettacolari
• Ristoranti di pesce

🐚 <b>Consigli:</b>
• Bandiera Blu per qualità delle acque
• Spiagge dog-friendly disponibili
• Noleggio lettini e ombrelloni""",
            "en": """🏖️ <b>Beaches of Cavallino-Treporti</b>

15 km of sandy coastline between the lagoon and the sea!

🏖️ <b>Punta Sabbioni Beach</b>
• Fine sand, shallow waters
• Ideal for families with children
• Ferries to Venice nearby

🏖️ <b>Cavallino Beach</b>
• Wide and well-equipped
• Beach clubs and free beach
• Water sports available

🏖️ <b>Ca' Savio Beach</b>
• Quiet and relaxing
• Pine forest behind
• Perfect for walks

🏖️ <b>Treporti Beach</b>
• Lagoon view
• Spectacular sunsets
• Seafood restaurants

🐚 <b>Tips:</b>
• Blue Flag for water quality
• Dog-friendly beaches available
• Sunbeds and umbrellas rental""",
            "de": """🏖️ <b>Strände von Cavallino-Treporti</b>

15 km Sandküste zwischen Lagune und Meer!

🏖️ <b>Strand Punta Sabbioni</b>
• Feiner Sand, flaches Wasser
• Ideal für Familien mit Kindern
• Fähren nach Venedig in der Nähe

🏖️ <b>Strand Cavallino</b>
• Breit und gut ausgestattet
• Strandbäder und freier Strand
• Wassersport verfügbar

🏖️ <b>Strand Ca' Savio</b>
• Ruhig und entspannend
• Pinienwald dahinter
• Perfekt für Spaziergänge

🏖️ <b>Strand Treporti</b>
• Blick auf die Lagune
• Spektakuläre Sonnenuntergänge
• Fischrestaurants

🐚 <b>Tipps:</b>
• Blaue Flagge für Wasserqualität
• Hundefreundliche Strände verfügbar
• Liegen- und Sonnenschirmverleih"""
        }
        text = fallback.get(lingua, fallback["it"])

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧭 Altre idee", callback_data="menu_cosa_fare")],
        [InlineKeyboardButton("◀️ Menu", callback_data="menu_back")]
    ])

    # Edita messaggio esistente invece di mandarne uno nuovo
    if query:
        await query.edit_message_text(
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )


async def handle_fortini(context, chat_id: int, lingua: str, query=None):
    """
    Mostra informazioni sui fortini storici.
    """
    # Rispondi al callback SUBITO
    if query:
        await query.answer()

    text = db.get_text("info_fortini", lingua)

    if text == "info_fortini":
        fallback = {
            "it": """🏰 <b>Fortini di Cavallino-Treporti</b>

Sistema difensivo storico della Serenissima e dell'era moderna.

🏰 <b>Batteria Amalfi</b>
• Costruita nel 1917
• Museo all'aperto visitabile
• Vista panoramica sulla bocca di porto

🏰 <b>Batteria Pisani</b>
• Fortificazione austro-ungarica
• Ben conservata
• Percorsi guidati disponibili

🏰 <b>Batteria Vettor Pisani</b>
• Struttura della Grande Guerra
• Torrette e casematte originali
• Interessante per appassionati di storia

🏰 <b>Forte Treporti</b>
• Epoca napoleonica
• Recentemente restaurato
• Eventi culturali estivi

📍 <b>Come visitare:</b>
• Percorso ciclabile collega tutti i fortini
• Visite guidate su prenotazione
• Ingresso gratuito o a offerta libera

🚴 Consiglio: noleggia una bici e fai il "Giro dei Fortini"!""",
            "en": """🏰 <b>Forts of Cavallino-Treporti</b>

Historic defense system from the Serenissima and modern era.

🏰 <b>Amalfi Battery</b>
• Built in 1917
• Open-air museum
• Panoramic view of the port entrance

🏰 <b>Pisani Battery</b>
• Austro-Hungarian fortification
• Well preserved
• Guided tours available

🏰 <b>Vettor Pisani Battery</b>
• Great War structure
• Original turrets and casemates
• Interesting for history enthusiasts

🏰 <b>Treporti Fort</b>
• Napoleonic era
• Recently restored
• Summer cultural events

📍 <b>How to visit:</b>
• Cycle path connects all forts
• Guided tours on reservation
• Free entry or donation

🚴 Tip: rent a bike and do the "Fort Tour"!""",
            "de": """🏰 <b>Festungen von Cavallino-Treporti</b>

Historisches Verteidigungssystem der Serenissima und der modernen Ära.

🏰 <b>Batterie Amalfi</b>
• Erbaut 1917
• Freiluftmuseum
• Panoramablick auf die Hafeneinfahrt

🏰 <b>Batterie Pisani</b>
• Österreichisch-ungarische Festung
• Gut erhalten
• Führungen verfügbar

🏰 <b>Batterie Vettor Pisani</b>
• Struktur aus dem Ersten Weltkrieg
• Originale Türme und Kasematten
• Interessant für Geschichtsliebhaber

🏰 <b>Fort Treporti</b>
• Napoleonische Ära
• Kürzlich restauriert
• Sommerliche Kulturveranstaltungen

📍 <b>So besuchen Sie:</b>
• Radweg verbindet alle Festungen
• Führungen auf Reservierung
• Freier Eintritt oder Spende

🚴 Tipp: Mieten Sie ein Fahrrad und machen Sie die "Festungstour"!"""
        }
        text = fallback.get(lingua, fallback["it"])

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧭 Altre idee", callback_data="menu_cosa_fare")],
        [InlineKeyboardButton("◀️ Menu", callback_data="menu_back")]
    ])

    # Edita messaggio esistente invece di mandarne uno nuovo
    if query:
        await query.edit_message_text(
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )


async def handle_attivita(context, chat_id: int, lingua: str, query=None):
    """
    Mostra attività disponibili nella zona.
    """
    # Rispondi al callback SUBITO
    if query:
        await query.answer()

    text = db.get_text("info_attivita", lingua)

    if text == "info_attivita":
        fallback = {
            "it": """🎯 <b>Attività a Cavallino-Treporti</b>

Divertimento per tutti i gusti!

🚴 <b>Ciclismo</b>
• 40+ km di piste ciclabili
• Noleggio bici ovunque
• Percorsi panoramici laguna-mare

🚣 <b>Sport acquatici</b>
• Windsurf e kitesurf
• SUP (Stand Up Paddle)
• Canoa e kayak in laguna

🎾 <b>Sport</b>
• Campi da tennis
• Beach volley
• Calcetto e minigolf

🐴 <b>Escursioni</b>
• Passeggiate a cavallo
• Birdwatching in laguna
• Tour in barca a Venezia

👨‍👩‍👧‍👦 <b>Per famiglie</b>
• Parchi giochi attrezzati
• Aquapark (zona Jesolo)
• Fattorie didattiche

🌅 <b>Relax</b>
• Yoga sulla spiaggia
• Pescaturismo
• Aperitivo al tramonto

📍 Chiedi alla reception del tuo campeggio per prenotazioni!""",
            "en": """🎯 <b>Activities in Cavallino-Treporti</b>

Fun for all tastes!

🚴 <b>Cycling</b>
• 40+ km of bike paths
• Bike rental everywhere
• Scenic lagoon-sea routes

🚣 <b>Water sports</b>
• Windsurfing and kitesurfing
• SUP (Stand Up Paddle)
• Canoeing and kayaking in the lagoon

🎾 <b>Sports</b>
• Tennis courts
• Beach volleyball
• Five-a-side football and mini golf

🐴 <b>Excursions</b>
• Horseback riding
• Birdwatching in the lagoon
• Boat tours to Venice

👨‍👩‍👧‍👦 <b>For families</b>
• Equipped playgrounds
• Aquapark (Jesolo area)
• Educational farms

🌅 <b>Relax</b>
• Beach yoga
• Fishing tourism
• Sunset aperitif

📍 Ask your campsite reception for bookings!""",
            "de": """🎯 <b>Aktivitäten in Cavallino-Treporti</b>

Spaß für jeden Geschmack!

🚴 <b>Radfahren</b>
• 40+ km Radwege
• Fahrradverleih überall
• Malerische Lagune-Meer-Routen

🚣 <b>Wassersport</b>
• Windsurfen und Kitesurfen
• SUP (Stand Up Paddle)
• Kanu und Kajak in der Lagune

🎾 <b>Sport</b>
• Tennisplätze
• Beachvolleyball
• Fußball und Minigolf

🐴 <b>Ausflüge</b>
• Reiten
• Vogelbeobachtung in der Lagune
• Bootstouren nach Venedig

👨‍👩‍👧‍👦 <b>Für Familien</b>
• Ausgestattete Spielplätze
• Aquapark (Gegend Jesolo)
• Lernbauernhöfe

🌅 <b>Entspannung</b>
• Yoga am Strand
• Angeltourismus
• Aperitif bei Sonnenuntergang

📍 Fragen Sie an der Rezeption Ihres Campingplatzes für Buchungen!"""
        }
        text = fallback.get(lingua, fallback["it"])

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧭 Altre idee", callback_data="menu_cosa_fare")],
        [InlineKeyboardButton("◀️ Menu", callback_data="menu_back")]
    ])

    # Edita messaggio esistente invece di mandarne uno nuovo
    if query:
        await query.edit_message_text(
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )


async def handle_eventi(context, chat_id: int, lingua: str, query=None):
    """
    Mostra eventi e manifestazioni nella zona.
    """
    # Rispondi al callback SUBITO
    if query:
        await query.answer()

    text = db.get_text("info_eventi", lingua)

    if text == "info_eventi":
        fallback = {
            "it": """🎪 <b>Eventi a Cavallino-Treporti</b>

Scopri cosa succede nella zona!

🎆 <b>Estate</b>
• Fuochi d'artificio sulla spiaggia
• Concerti e spettacoli serali
• Sagre e feste paesane
• Cinema all'aperto

🎭 <b>Mercati</b>
• Mercato settimanale (martedì)
• Mercatini dell'artigianato
• Mercato del pesce fresco

🏃 <b>Sport</b>
• Gare di beach volley
• Tornei di calcetto
• Regate in laguna
• Corse podistiche

🎨 <b>Cultura</b>
• Mostre nei fortini
• Visite guidate storiche
• Laboratori per bambini

🍷 <b>Enogastronomia</b>
• Festa del pesce
• Degustazioni vini DOC
• Sagra delle castraure

📱 <b>Per info aggiornate:</b>
• Pro Loco Cavallino-Treporti
• Sito del Comune
• Reception del tuo campeggio""",
            "en": """🎪 <b>Events in Cavallino-Treporti</b>

Discover what's happening in the area!

🎆 <b>Summer</b>
• Fireworks on the beach
• Evening concerts and shows
• Local festivals and fairs
• Open-air cinema

🎭 <b>Markets</b>
• Weekly market (Tuesday)
• Craft markets
• Fresh fish market

🏃 <b>Sports</b>
• Beach volleyball competitions
• Football tournaments
• Lagoon regattas
• Running races

🎨 <b>Culture</b>
• Exhibitions in the forts
• Historical guided tours
• Workshops for children

🍷 <b>Food & Wine</b>
• Fish festival
• DOC wine tastings
• Castraure artichoke festival

📱 <b>For updated info:</b>
• Pro Loco Cavallino-Treporti
• Municipality website
• Your campsite reception""",
            "de": """🎪 <b>Veranstaltungen in Cavallino-Treporti</b>

Entdecken Sie, was in der Gegend passiert!

🎆 <b>Sommer</b>
• Feuerwerk am Strand
• Abendkonzerte und Shows
• Lokale Feste und Jahrmärkte
• Freiluftkino

🎭 <b>Märkte</b>
• Wochenmarkt (Dienstag)
• Handwerksmärkte
• Frischfischmarkt

🏃 <b>Sport</b>
• Beachvolleyball-Turniere
• Fußballturniere
• Lagunen-Regatten
• Laufwettbewerbe

🎨 <b>Kultur</b>
• Ausstellungen in den Festungen
• Historische Führungen
• Workshops für Kinder

🍷 <b>Essen & Wein</b>
• Fischfest
• DOC-Weinverkostungen
• Castraure-Artischockenfest

📱 <b>Für aktuelle Infos:</b>
• Pro Loco Cavallino-Treporti
• Website der Gemeinde
• Rezeption Ihres Campingplatzes"""
        }
        text = fallback.get(lingua, fallback["it"])

    text += "\n\n🦭 <i>SLAPPY</i>"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Menu", callback_data="menu_back")]
    ])

    # Edita messaggio esistente invece di mandarne uno nuovo
    if query:
        await query.edit_message_text(
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )


async def handle_trasporti(context, chat_id: int, lingua: str, query=None):
    """
    Mostra informazioni su trasporti e collegamenti.
    """
    # Rispondi al callback SUBITO
    if query:
        await query.answer()

    text = db.get_text("info_trasporti", lingua)

    if text == "info_trasporti":
        fallback = {
            "it": """🚌 <b>TRASPORTI E COLLEGAMENTI</b>

Come muoversi a Cavallino-Treporti e dintorni.

🚢 <b>Vaporetti per Venezia</b>
• Da Punta Sabbioni: linea 14
• Frequenza: ogni 30 min circa
• Durata: 30 min fino a San Marco
• Biglietti: ACTV o Venezia Unica

🚌 <b>Autobus ATVO</b>
• Linea 23: Punta Sabbioni - Jesolo
• Fermate lungo tutto il litorale
• Collegamento con stazione treni

🚗 <b>In auto</b>
• Parcheggi a Punta Sabbioni (a pagamento)
• Parcheggi nei campeggi
• Zona a traffico limitato in estate

🚴 <b>Bicicletta</b>
• Mezzo ideale per la zona!
• Piste ciclabili ovunque
• Noleggio in ogni campeggio

🚕 <b>Taxi e NCC</b>
• Servizio taxi locale
• Transfer aeroporto Marco Polo
• Noleggio con conducente

✈️ <b>Aeroporti vicini</b>
• Venezia Marco Polo: 40 min
• Treviso Canova: 50 min

🔗 <b>Link utili:</b>
• actv.avmspa.it (vaporetti)
• atvo.it (autobus)""",
            "en": """🚌 <b>TRANSPORT AND CONNECTIONS</b>

How to get around Cavallino-Treporti and surroundings.

🚢 <b>Ferries to Venice</b>
• From Punta Sabbioni: line 14
• Frequency: every 30 min approx
• Duration: 30 min to San Marco
• Tickets: ACTV or Venezia Unica

🚌 <b>ATVO Buses</b>
• Line 23: Punta Sabbioni - Jesolo
• Stops along the entire coast
• Connection with train station

🚗 <b>By car</b>
• Parking at Punta Sabbioni (paid)
• Parking at campsites
• Limited traffic zone in summer

🚴 <b>Bicycle</b>
• Ideal transport for the area!
• Bike paths everywhere
• Rental at every campsite

🚕 <b>Taxi and car service</b>
• Local taxi service
• Marco Polo airport transfer
• Chauffeur service

✈️ <b>Nearby airports</b>
• Venice Marco Polo: 40 min
• Treviso Canova: 50 min

🔗 <b>Useful links:</b>
• actv.avmspa.it (ferries)
• atvo.it (buses)""",
            "de": """🚌 <b>VERKEHR UND VERBINDUNGEN</b>

Wie man sich in Cavallino-Treporti und Umgebung fortbewegt.

🚢 <b>Fähren nach Venedig</b>
• Ab Punta Sabbioni: Linie 14
• Frequenz: ca. alle 30 Min
• Dauer: 30 Min bis San Marco
• Tickets: ACTV oder Venezia Unica

🚌 <b>ATVO Busse</b>
• Linie 23: Punta Sabbioni - Jesolo
• Haltestellen entlang der Küste
• Verbindung zum Bahnhof

🚗 <b>Mit dem Auto</b>
• Parkplätze in Punta Sabbioni (kostenpflichtig)
• Parkplätze auf Campingplätzen
• Verkehrsberuhigte Zone im Sommer

🚴 <b>Fahrrad</b>
• Ideales Verkehrsmittel für die Gegend!
• Radwege überall
• Verleih auf jedem Campingplatz

🚕 <b>Taxi und Fahrservice</b>
• Lokaler Taxiservice
• Transfer Flughafen Marco Polo
• Chauffeurservice

✈️ <b>Nahe Flughäfen</b>
• Venedig Marco Polo: 40 Min
• Treviso Canova: 50 Min

🔗 <b>Nützliche Links:</b>
• actv.avmspa.it (Fähren)
• atvo.it (Busse)"""
        }
        text = fallback.get(lingua, fallback["it"])

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Menu", callback_data="menu_back")]
    ])

    # Edita messaggio esistente invece di mandarne uno nuovo
    if query:
        await query.edit_message_text(
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )


async def handle_ristoranti(context, chat_id: int, lingua: str, query=None):
    """
    Mostra ristoranti e locali consigliati nella zona.
    """
    # Rispondi al callback SUBITO
    if query:
        await query.answer()

    text = db.get_text("info_ristoranti", lingua)

    if text == "info_ristoranti":
        fallback = {
            "it": """🍽️ <b>RISTORANTI E LOCALI</b>

Dove mangiare a Cavallino-Treporti e dintorni.

🐟 <b>Pesce fresco</b>
• Trattorie tipiche con pesce di laguna
• Fritture e grigliate di Adriatico
• Sarde in saor, moleche, schie

🍕 <b>Pizzerie</b>
• Pizza napoletana e romana
• Locali per famiglie
• Consegna a domicilio disponibile

🍝 <b>Cucina veneta</b>
• Risotto al nero di seppia
• Bigoli in salsa
• Fegato alla veneziana

🥗 <b>Per tutti i gusti</b>
• Ristoranti vegetariani/vegani
• Opzioni senza glutine
• Cucina internazionale

🍦 <b>Bar e gelaterie</b>
• Gelato artigianale
• Aperitivo al tramonto
• Caffetterie sulla spiaggia

📍 <b>Zone consigliate:</b>
• Punta Sabbioni - vista laguna
• Cavallino centro - tipico
• Ca' Savio - tranquillo
• Treporti - romantico

💡 Chiedi alla reception del campeggio per consigli personalizzati!""",
            "en": """🍽️ <b>RESTAURANTS AND BARS</b>

Where to eat in Cavallino-Treporti and surroundings.

🐟 <b>Fresh fish</b>
• Typical trattorias with lagoon fish
• Fried and grilled Adriatic seafood
• Sarde in saor, moleche, schie

🍕 <b>Pizzerias</b>
• Neapolitan and Roman pizza
• Family-friendly venues
• Delivery available

🍝 <b>Venetian cuisine</b>
• Squid ink risotto
• Bigoli in salsa
• Venetian-style liver

🥗 <b>For all tastes</b>
• Vegetarian/vegan restaurants
• Gluten-free options
• International cuisine

🍦 <b>Bars and ice cream</b>
• Artisan gelato
• Sunset aperitif
• Beach cafés

📍 <b>Recommended areas:</b>
• Punta Sabbioni - lagoon view
• Cavallino center - traditional
• Ca' Savio - quiet
• Treporti - romantic

💡 Ask your campsite reception for personalized tips!""",
            "de": """🍽️ <b>RESTAURANTS UND LOKALE</b>

Wo man in Cavallino-Treporti und Umgebung essen kann.

🐟 <b>Frischer Fisch</b>
• Typische Trattorien mit Lagunenfisch
• Frittiertes und gegrilltes aus der Adria
• Sarde in saor, Moleche, Schie

🍕 <b>Pizzerien</b>
• Neapolitanische und römische Pizza
• Familienfreundliche Lokale
• Lieferung verfügbar

🍝 <b>Venezianische Küche</b>
• Risotto mit Tintenfisch
• Bigoli in Salsa
• Leber auf venezianische Art

🥗 <b>Für jeden Geschmack</b>
• Vegetarische/vegane Restaurants
• Glutenfreie Optionen
• Internationale Küche

🍦 <b>Bars und Eisdielen</b>
• Handwerkliches Eis
• Aperitif bei Sonnenuntergang
• Strandcafés

📍 <b>Empfohlene Gebiete:</b>
• Punta Sabbioni - Lagunenblick
• Cavallino Zentrum - traditionell
• Ca' Savio - ruhig
• Treporti - romantisch

💡 Fragen Sie an der Campingplatz-Rezeption nach persönlichen Tipps!"""
        }
        text = fallback.get(lingua, fallback["it"])

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Menu", callback_data="menu_back")]
    ])

    # Edita messaggio esistente invece di mandarne uno nuovo
    if query:
        await query.edit_message_text(
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )


# ============================================================
# HANDLER SOS E EMERGENZE
# ============================================================

async def handle_sos(context, chat_id: int, lingua: str, query=None):
    """
    Menu principale SOS - Salute & Emergenze.
    """
    # Rispondi al callback SUBITO
    if query:
        await query.answer()

    header = {
        "it": "Salute & Emergenze",
        "en": "Health & Emergencies",
        "de": "Gesundheit & Notfälle"
    }.get(lingua, "Salute & Emergenze")

    subtitle = {
        "it": "Scegli un'opzione:",
        "en": "Choose an option:",
        "de": "Wähle eine Option:"
    }.get(lingua, "Scegli un'opzione:")

    text = f"🚑 <b>{header}</b>\n\n{subtitle}\n\n🦭 <i>SLAPPY</i>"

    btn_emergenza = {"it": "🆘 Emergenza", "en": "🆘 Emergency", "de": "🆘 Notfall"}.get(lingua, "🆘 Emergenza")
    btn_guardia = {"it": "🩺 Guardia Medica", "en": "🩺 Medical Guard", "de": "🩺 Bereitschaftsarzt"}.get(lingua, "🩺 Guardia Medica")
    btn_ospedali = {"it": "🏥 Ospedali/PPI", "en": "🏥 Hospitals/ER", "de": "🏥 Krankenhäuser"}.get(lingua, "🏥 Ospedali/PPI")
    btn_farmacie = {"it": "💊 Farmacia turno", "en": "💊 Pharmacy on duty", "de": "💊 Notdienst-Apotheke"}.get(lingua, "💊 Farmacia turno")
    btn_numeri = {"it": "📞 Numeri utili", "en": "📞 Useful numbers", "de": "📞 Nützliche Nummern"}.get(lingua, "📞 Numeri utili")

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(btn_emergenza, callback_data="menu_sos_emergenza"),
            InlineKeyboardButton(btn_guardia, callback_data="menu_sos_guardia_medica")
        ],
        [
            InlineKeyboardButton(btn_ospedali, callback_data="menu_sos_ospedali"),
            InlineKeyboardButton(btn_farmacie, callback_data="menu_sos_farmacie")
        ],
        [
            InlineKeyboardButton(btn_numeri, callback_data="menu_sos_numeri")
        ],
        [InlineKeyboardButton("◀️ Menu", callback_data="menu_back")]
    ])

    # Edita messaggio esistente invece di mandarne uno nuovo
    if query:
        await query.edit_message_text(
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )


async def handle_sos_emergenza(context, chat_id: int, lingua: str, query=None):
    """
    Numeri di emergenza - testo copiabile.
    """
    # Rispondi al callback SUBITO
    if query:
        await query.answer()

    texts = {
        "it": """🆘 <b>EMERGENZA</b>

📞 <code>112</code> — Numero Unico Europeo
📞 <code>118</code> — Ambulanza
📞 <code>115</code> — Vigili del Fuoco
📞 <code>1530</code> — Guardia Costiera

💡 <i>Tocca un numero per copiarlo</i>""",
        "en": """🆘 <b>EMERGENCY</b>

📞 <code>112</code> — European Emergency
📞 <code>118</code> — Ambulance
📞 <code>115</code> — Fire Department
📞 <code>1530</code> — Coast Guard

💡 <i>Tap a number to copy</i>""",
        "de": """🆘 <b>NOTFALL</b>

📞 <code>112</code> — Europäischer Notruf
📞 <code>118</code> — Krankenwagen
📞 <code>115</code> — Feuerwehr
📞 <code>1530</code> — Küstenwache

💡 <i>Nummer antippen zum Kopieren</i>"""
    }
    text = texts.get(lingua, texts["it"])

    btn_back = {"it": "⬅️ Indietro", "en": "⬅️ Back", "de": "⬅️ Zurück"}.get(lingua, "⬅️ Indietro")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(btn_back, callback_data="menu_sos")]
    ])

    # Edita messaggio esistente invece di mandarne uno nuovo
    if query:
        await query.edit_message_text(
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )


async def handle_sos_guardia_medica(context, chat_id: int, lingua: str, query=None):
    """
    Guardia Medica / Continuità Assistenziale.
    """
    # Rispondi al callback SUBITO
    if query:
        await query.answer()

    texts = {
        "it": """🩺 <b>GUARDIA MEDICA</b>

📞 <code>116117</code>

🕐 <b>Quando chiamare:</b>
• Notti feriali: 20:00 - 08:00
• Weekend: sab 10:00 → lun 08:00
• Festivi: tutto il giorno

Per urgenze <b>NON</b> gravi (no 118)

💡 <i>Tocca il numero per copiarlo</i>""",
        "en": """🩺 <b>MEDICAL GUARD</b>

📞 <code>116117</code>

🕐 <b>When to call:</b>
• Weeknights: 8pm - 8am
• Weekends: Sat 10am → Mon 8am
• Holidays: all day

For <b>NON</b>-serious emergencies (not 118)

💡 <i>Tap the number to copy</i>""",
        "de": """🩺 <b>BEREITSCHAFTSARZT</b>

📞 <code>116117</code>

🕐 <b>Wann anrufen:</b>
• Wochennächte: 20:00 - 08:00
• Wochenende: Sa 10:00 → Mo 08:00
• Feiertage: ganztägig

Für <b>NICHT</b> schwere Notfälle (nicht 118)

💡 <i>Nummer antippen zum Kopieren</i>"""
    }
    text = texts.get(lingua, texts["it"])

    btn_back = {"it": "⬅️ Indietro", "en": "⬅️ Back", "de": "⬅️ Zurück"}.get(lingua, "⬅️ Indietro")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(btn_back, callback_data="menu_sos")]
    ])

    # Edita messaggio esistente invece di mandarne uno nuovo
    if query:
        await query.edit_message_text(
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )


async def handle_sos_ospedali(context, chat_id: int, lingua: str, query=None):
    """
    Ospedali e Punti di Primo Intervento.
    """
    # Rispondi al callback SUBITO
    if query:
        await query.answer()

    texts = {
        "it": """🏥 <b>OSPEDALI E PPI</b>

<b>PPI Ca' Savio</b>
📞 <code>0415300214</code>
🕐 Estate H24 • Inverno 8-20

<b>PS Jesolo</b>
📞 <code>0421388111</code>
🕐 Aperto H24

💡 <i>Tocca il numero per copiarlo</i>""",
        "en": """🏥 <b>HOSPITALS</b>

<b>First Aid Ca' Savio</b>
📞 <code>0415300214</code>
🕐 Summer 24/7 • Winter 8am-8pm

<b>ER Jesolo</b>
📞 <code>0421388111</code>
🕐 Open 24/7

💡 <i>Tap the number to copy</i>""",
        "de": """🏥 <b>KRANKENHÄUSER</b>

<b>Erste Hilfe Ca' Savio</b>
📞 <code>0415300214</code>
🕐 Sommer 24h • Winter 8-20

<b>Notaufnahme Jesolo</b>
📞 <code>0421388111</code>
🕐 24h geöffnet

💡 <i>Nummer antippen zum Kopieren</i>"""
    }
    text = texts.get(lingua, texts["it"])

    btn_back = {"it": "⬅️ Indietro", "en": "⬅️ Back", "de": "⬅️ Zurück"}.get(lingua, "⬅️ Indietro")
    btn_nav_ppi = {"it": "📍 Naviga PPI", "en": "📍 Navigate PPI", "de": "📍 Navigation PPI"}.get(lingua, "📍 Naviga PPI")
    btn_nav_ps = {"it": "📍 Naviga PS", "en": "📍 Navigate ER", "de": "📍 Navigation Notaufnahme"}.get(lingua, "📍 Naviga PS")

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(text=btn_nav_ppi, url="https://www.google.com/maps/dir/?api=1&destination=45.4477,12.4847"),
            InlineKeyboardButton(text=btn_nav_ps, url="https://www.google.com/maps/dir/?api=1&destination=45.5089,12.6463")
        ],
        [InlineKeyboardButton(btn_back, callback_data="menu_sos")]
    ])

    # Edita messaggio esistente invece di mandarne uno nuovo
    if query:
        await query.edit_message_text(
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )


async def handle_sos_farmacie(context, chat_id: int, lingua: str, query=None):
    """
    Farmacie di turno - chiama API e mostra risultati.
    """
    # Rispondi al callback SUBITO
    if query:
        await query.answer()

    from farmacie_api import get_farmacie_turno_safe, get_maps_url

    # Headers per lingua
    headers = {
        "it": "Farmacie di Turno",
        "en": "Pharmacies on Duty",
        "de": "Apotheken im Dienst"
    }
    header = headers.get(lingua, headers["it"])

    loading_texts = {
        "it": "Caricamento...",
        "en": "Loading...",
        "de": "Laden..."
    }

    fallback_texts = {
        "it": """💊 <b>Farmacie di Turno</b>

⚠️ Servizio momentaneamente non disponibile.

📞 Chiama il numero verde:
<code>800420707</code>
(Farmacie di turno Regione Veneto)

💡 <i>Tocca il numero per copiarlo</i>""",
        "en": """💊 <b>Pharmacies on Duty</b>

⚠️ Service temporarily unavailable.

📞 Call the toll-free number:
<code>800420707</code>
(Veneto Region pharmacy service)

💡 <i>Tap the number to copy</i>""",
        "de": """💊 <b>Apotheken im Dienst</b>

⚠️ Dienst vorübergehend nicht verfügbar.

📞 Rufen Sie die gebührenfreie Nummer an:
<code>800420707</code>
(Apotheken-Notdienst Region Venetien)

💡 <i>Nummer antippen zum Kopieren</i>"""
    }

    btn_back = {"it": "⬅️ Indietro", "en": "⬅️ Back", "de": "⬅️ Zurück"}.get(lingua, "⬅️ Indietro")

    # Chiama API con timeout
    try:
        farmacie = await asyncio.wait_for(get_farmacie_turno_safe(), timeout=API_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("Timeout API farmacie")
        farmacie = None
    except Exception as e:
        logger.error(f"Errore API farmacie: {e}")
        farmacie = None

    if not farmacie:
        # Fallback: mostra numero verde
        text = fallback_texts.get(lingua, fallback_texts["it"])
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(btn_back, callback_data="menu_sos")]
        ])
    else:
        # Costruisci messaggio con farmacie
        text = f"💊 <b>{header}</b>\n"

        # Limita a max 4 farmacie per non appesantire il messaggio
        farmacie_mostrate = farmacie[:4]

        buttons = []
        for i, f in enumerate(farmacie_mostrate):
            text += f"\n<b>{f.nome}</b>\n"
            if f.indirizzo:
                text += f"📍 {f.indirizzo}\n"
            if f.telefono:
                text += f"📞 <code>{f.telefono}</code>\n"
            if f.orario:
                text += f"🕐 {f.orario}\n"

            # Bottone navigazione per ogni farmacia (nome completo, max 25 char)
            nome_btn = f.nome if len(f.nome) <= 25 else f.nome[:22] + "..."
            btn_label = f"📍 {nome_btn}"
            maps_url = get_maps_url(f)
            buttons.append(InlineKeyboardButton(text=btn_label, url=maps_url))

        hint = {
            "it": "Tocca il numero per copiarlo",
            "en": "Tap number to copy",
            "de": "Nummer antippen zum Kopieren"
        }.get(lingua, "Tocca il numero per copiarlo")
        text += f"\n💡 <i>{hint}</i>"

        # Disponi bottoni: 2 per riga + indietro
        keyboard_rows = []
        for i in range(0, len(buttons), 2):
            row = buttons[i:i+2]
            keyboard_rows.append(row)
        keyboard_rows.append([InlineKeyboardButton(btn_back, callback_data="menu_sos")])

        keyboard = InlineKeyboardMarkup(keyboard_rows)

    # Edita messaggio esistente invece di mandarne uno nuovo
    if query:
        await query.edit_message_text(
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )


async def handle_sos_numeri(context, chat_id: int, lingua: str, query=None):
    """
    Lista numeri utili - testo copiabile.
    """
    # Rispondi al callback SUBITO
    if query:
        await query.answer()

    texts = {
        "it": """📞 <b>NUMERI UTILI</b>

🆘 <code>112</code> — Emergenze
🚑 <code>118</code> — Ambulanza
🚒 <code>115</code> — Vigili del Fuoco
🩺 <code>116117</code> — Guardia Medica
⚓ <code>1530</code> — Guardia Costiera
👮 <code>113</code> — Polizia
🚗 <code>803116</code> — Soccorso ACI
🏥 <code>0415300214</code> — PPI Ca' Savio

💡 <i>Tocca un numero per copiarlo</i>""",
        "en": """📞 <b>USEFUL NUMBERS</b>

🆘 <code>112</code> — Emergency
🚑 <code>118</code> — Ambulance
🚒 <code>115</code> — Fire Department
🩺 <code>116117</code> — Medical Guard
⚓ <code>1530</code> — Coast Guard
👮 <code>113</code> — Police
🚗 <code>803116</code> — ACI Roadside
🏥 <code>0415300214</code> — First Aid Ca' Savio

💡 <i>Tap a number to copy</i>""",
        "de": """📞 <b>NÜTZLICHE NUMMERN</b>

🆘 <code>112</code> — Notruf
🚑 <code>118</code> — Krankenwagen
🚒 <code>115</code> — Feuerwehr
🩺 <code>116117</code> — Bereitschaftsarzt
⚓ <code>1530</code> — Küstenwache
👮 <code>113</code> — Polizei
🚗 <code>803116</code> — ACI Pannenhilfe
🏥 <code>0415300214</code> — Erste Hilfe Ca' Savio

💡 <i>Nummer antippen zum Kopieren</i>"""
    }
    text = texts.get(lingua, texts["it"])

    btn_back = {"it": "⬅️ Indietro", "en": "⬅️ Back", "de": "⬅️ Zurück"}.get(lingua, "⬅️ Indietro")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(btn_back, callback_data="menu_sos")]
    ])

    # Edita messaggio esistente invece di mandarne uno nuovo
    if query:
        await query.edit_message_text(
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )


# ============================================================
# MORNING BRIEFING
# ============================================================

# Path immagine morning card
import os
MORNING_CARD_PATH = os.path.join(os.path.dirname(__file__), "assets", "morning_card.png")


async def handle_morning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler per comando /morning - Invia Morning Briefing con card PNG + bottoni.
    """
    chat_id = update.effective_chat.id
    user = db.get_user(chat_id)

    if not user or user.get("stato_onboarding") != "completo":
        await context.bot.send_message(
            chat_id=chat_id,
            text="Per usare /morning devi prima completare la registrazione con /start",
            parse_mode="HTML"
        )
        return

    lingua = user.get("lingua", "it")

    # Bottoni 3 righe x 2
    btn_labels = {
        "it": {
            "meteo": "☀️ Meteo completo",
            "eventi": "🎉 Altri eventi",
            "trasporti": "🚌 Trasporti",
            "ristoranti": "🍽️ Dove mangiare",
            "fortini": "🏛️ Fortini & Storia",
            "sos": "🆘 Emergenze"
        },
        "en": {
            "meteo": "☀️ Full weather",
            "eventi": "🎉 More events",
            "trasporti": "🚌 Transport",
            "ristoranti": "🍽️ Where to eat",
            "fortini": "🏛️ Forts & History",
            "sos": "🆘 Emergencies"
        },
        "de": {
            "meteo": "☀️ Wetter komplett",
            "eventi": "🎉 Mehr Events",
            "trasporti": "🚌 Transport",
            "ristoranti": "🍽️ Essen gehen",
            "fortini": "🏛️ Forts & Geschichte",
            "sos": "🆘 Notfälle"
        }
    }

    labels = btn_labels.get(lingua, btn_labels["it"])

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(labels["meteo"], callback_data="menu_meteo"),
            InlineKeyboardButton(labels["eventi"], callback_data="menu_eventi")
        ],
        [
            InlineKeyboardButton(labels["trasporti"], callback_data="menu_trasporti"),
            InlineKeyboardButton(labels["ristoranti"], callback_data="menu_ristoranti")
        ],
        [
            InlineKeyboardButton(labels["fortini"], callback_data="menu_fortini"),
            InlineKeyboardButton(labels["sos"], callback_data="menu_sos")
        ]
    ])

    try:
        # Invia immagine statica con bottoni
        with open(MORNING_CARD_PATH, "rb") as photo:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                reply_markup=keyboard
            )
    except FileNotFoundError:
        logger.error(f"Morning card non trovata: {MORNING_CARD_PATH}")
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ Immagine non disponibile. Contatta l'assistenza.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Errore morning briefing: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ Errore nel caricamento. Riprova più tardi.",
            parse_mode="HTML"
        )


async def send_morning_briefing_to_all(bot):
    """
    Invia il morning briefing a tutti gli utenti attivi.
    Chiamato dallo scheduler alle 8:00.
    """
    import asyncio
    from meteo_api import get_meteo_forecast, get_weather_emoji, get_weather_description

    logger.info("Avvio invio morning briefing a tutti gli utenti...")

    utenti = db.get_utenti_attivi()
    if not utenti:
        logger.info("Nessun utente attivo per morning briefing")
        return

    # Data formattata
    now = datetime.now()
    giorni = {
        "it": ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"],
        "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
        "de": ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    }
    mesi = {
        "it": ["Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"],
        "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
        "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
    }

    # Meteo (una sola chiamata per tutti)
    meteo_data = None
    try:
        meteo_data = await asyncio.wait_for(get_meteo_forecast(), timeout=10)
    except Exception as e:
        logger.warning(f"Errore meteo per morning briefing: {e}")

    # Bottoni
    btn_labels = {
        "it": {
            "meteo": "☀️ Meteo completo",
            "eventi": "🎉 Altri eventi",
            "trasporti": "🚌 Trasporti",
            "ristoranti": "🍽️ Dove mangiare",
            "fortini": "🏛️ Fortini & Storia",
            "sos": "🆘 Emergenze"
        },
        "en": {
            "meteo": "☀️ Full weather",
            "eventi": "🎉 More events",
            "trasporti": "🚌 Transport",
            "ristoranti": "🍽️ Where to eat",
            "fortini": "🏛️ Forts & History",
            "sos": "🆘 Emergencies"
        },
        "de": {
            "meteo": "☀️ Wetter komplett",
            "eventi": "🎉 Mehr Events",
            "trasporti": "🚌 Transport",
            "ristoranti": "🍽️ Essen gehen",
            "fortini": "🏛️ Forts & Geschichte",
            "sos": "🆘 Notfälle"
        }
    }

    saluti = {
        "it": "Buongiorno",
        "en": "Good morning",
        "de": "Guten Morgen"
    }

    inviati = 0
    errori = 0

    for utente in utenti:
        chat_id = utente.get("chat_id")
        lingua = utente.get("lingua", "it")
        nome = utente.get("nome", "")

        try:
            # Data per lingua
            giorno_nome = giorni.get(lingua, giorni["it"])[now.weekday()]
            mese_nome = mesi.get(lingua, mesi["it"])[now.month - 1]

            # Costruisci messaggio
            saluto = saluti.get(lingua, saluti["it"])
            text = f"☀️ <b>{saluto}"
            if nome:
                text += f", {nome}"
            text += f"!</b>\n\n"
            text += f"📅 {giorno_nome} {now.day} {mese_nome}\n"

            # Meteo
            if meteo_data and meteo_data.get("current"):
                current = meteo_data["current"]
                temp = current.get("temperature", "")
                weather_code = current.get("weather_code", 0)
                emoji = get_weather_emoji(weather_code)
                desc = get_weather_description(weather_code, lingua)
                if temp:
                    text += f"{emoji} {temp}°C - {desc}\n"

            # Evento del giorno
            evento_str = get_evento_oggi(lingua)
            if evento_str:
                text += f"{evento_str}\n"

            # Keyboard
            labels = btn_labels.get(lingua, btn_labels["it"])
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(labels["meteo"], callback_data="menu_meteo"),
                    InlineKeyboardButton(labels["eventi"], callback_data="menu_eventi")
                ],
                [
                    InlineKeyboardButton(labels["trasporti"], callback_data="menu_trasporti"),
                    InlineKeyboardButton(labels["ristoranti"], callback_data="menu_ristoranti")
                ],
                [
                    InlineKeyboardButton(labels["fortini"], callback_data="menu_fortini"),
                    InlineKeyboardButton(labels["sos"], callback_data="menu_sos")
                ]
            ])

            await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            inviati += 1

            # Rate limiting: pausa tra invii per evitare flood
            await asyncio.sleep(0.05)

        except Exception as e:
            logger.error(f"Errore invio morning briefing a {chat_id}: {e}")
            errori += 1

    logger.info(f"Morning briefing completato: {inviati} inviati, {errori} errori")


# ============================================================
# ADMIN STATS
# ============================================================

async def handle_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Comando /stats - Solo per admin.
    Mostra statistiche del bot.
    """
    chat_id = update.effective_chat.id

    # Verifica admin
    if chat_id != ADMIN_CHAT_ID:
        await context.bot.send_message(
            chat_id=chat_id,
            text="⛔ Comando riservato all'amministratore.",
            parse_mode="HTML"
        )
        return

    # Raccogli statistiche
    stats = db.get_stats()

    # Calcola uptime
    uptime_str = "N/A"
    if _bot_start_time:
        delta = datetime.now() - _bot_start_time
        days = delta.days
        hours, remainder = divmod(delta.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        uptime_str = f"{days}g {hours}h {minutes}m"

    # Ultimo errore
    error_str = "Nessun errore recente"
    if _last_error:
        error_time = _last_error["time"].strftime("%d/%m %H:%M")
        error_msg = _last_error["message"][:200]
        error_str = f"{error_time}\n<code>{error_msg}</code>"

    # Costruisci messaggio
    text = f"""📊 <b>Slappy Bot - Statistiche</b>

👥 <b>Utenti</b>
├ Totali: <code>{stats['utenti_totali']}</code>
├ Registrati: <code>{stats['utenti_completi']}</code>
└ Attivi (7gg): <code>{stats['utenti_attivi_7g']}</code>

🎪 <b>Eventi</b>
├ Totali: <code>{stats['eventi_totali']}</code>
└ Attivi oggi: <code>{stats['eventi_attivi']}</code>

⚙️ <b>Sistema</b>
├ Uptime: <code>{uptime_str}</code>
└ Avviato: <code>{_bot_start_time.strftime('%d/%m/%Y %H:%M') if _bot_start_time else 'N/A'}</code>

🚨 <b>Ultimo errore</b>
{error_str}
"""

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="HTML"
    )


async def handle_test_briefing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Comando /testbriefing - Solo per admin.
    Invia il morning briefing di test all'admin.
    """
    import asyncio
    chat_id = update.effective_chat.id

    # Verifica admin
    if chat_id != ADMIN_CHAT_ID:
        await context.bot.send_message(
            chat_id=chat_id,
            text="⛔ Comando riservato all'amministratore.",
            parse_mode="HTML"
        )
        return

    await context.bot.send_message(
        chat_id=chat_id,
        text="🧪 Invio morning briefing di test...",
        parse_mode="HTML"
    )

    # Recupera dati utente dal database
    user = db.get_user(chat_id)
    nome = user.get("nome", "Admin") if user else "Admin"
    lingua = user.get("lingua", "it") if user else "it"

    # Genera briefing
    now = datetime.now()

    giorni = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
    mesi = ["Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]

    giorno_nome = giorni[now.weekday()]
    mese_nome = mesi[now.month - 1]

    # Meteo
    meteo_str = ""
    try:
        meteo_data = await asyncio.wait_for(get_meteo_forecast(), timeout=10)
        if meteo_data and meteo_data.get("current"):
            current = meteo_data["current"]
            temp = current.get("temperature", "")
            weather_code = current.get("weather_code", 0)
            emoji = get_weather_emoji(weather_code)
            desc = get_weather_description(weather_code, lingua)
            if temp:
                meteo_str = f"{emoji} {temp}°C - {desc}"
    except Exception as e:
        meteo_str = f"⚠️ Errore meteo: {e}"

    # Evento del giorno
    evento_str = get_evento_oggi(lingua)

    # Costruisci messaggio
    saluti = {"it": "Buongiorno", "en": "Good morning", "de": "Guten Morgen"}
    saluto = saluti.get(lingua, saluti["it"])
    text = f"☀️ <b>{saluto}, {nome}!</b>\n\n"
    text += f"📅 {giorno_nome} {now.day} {mese_nome}\n"
    if meteo_str:
        text += f"{meteo_str}\n"
    if evento_str:
        text += f"{evento_str}\n"

    # Keyboard
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("☀️ Meteo completo", callback_data="menu_meteo"),
            InlineKeyboardButton("🎉 Altri eventi", callback_data="menu_eventi")
        ],
        [
            InlineKeyboardButton("🚌 Trasporti", callback_data="menu_trasporti"),
            InlineKeyboardButton("🍽️ Dove mangiare", callback_data="menu_ristoranti")
        ],
        [
            InlineKeyboardButton("🏛️ Fortini & Storia", callback_data="menu_fortini"),
            InlineKeyboardButton("🆘 Emergenze", callback_data="menu_sos")
        ]
    ])

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )
