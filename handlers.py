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
COMUNE_BASE = "https://www.comune.cavallinotreporti.ve.it"

# ============ TRASPORTI CONSTANTS ============
OPERATORE_EMOJI = {"bus": "🚌", "traghetto": "🚢", "taxi": "🚕", "bike": "🚴"}

TRASPORTI_LABELS = {
    "home_title": {"it": "🚌 <b>TRASPORTI</b>", "en": "🚌 <b>TRANSPORT</b>", "de": "🚌 <b>VERKEHR</b>"},
    "arrivo": {"it": "📍 Raggiungi un luogo", "en": "📍 Reach a destination", "de": "📍 Ziel erreichen"},
    "altra_zona": {"it": "🏘️ Cambia zona / frazione", "en": "🏘️ Change area / district", "de": "🏘️ Gebiet / Ortsteil wechseln"},
    "prezzi": {"it": "🎫 Biglietti e Prezzi", "en": "🎫 Tickets & Prices", "de": "🎫 Tickets & Preise"},
    "back_menu": {"it": "◀️ Menu", "en": "◀️ Menu", "de": "◀️ Menü"},
    "back_trasporti": {"it": "◀️ Trasporti", "en": "◀️ Transport", "de": "◀️ Verkehr"},
    "no_destinations": {"it": "Nessuna destinazione disponibile.", "en": "No destinations available.", "de": "Keine Ziele verfügbar."},
    "no_lines": {"it": "Nessuna linea disponibile.", "en": "No lines available.", "de": "Keine Linien verfügbar."},
    "duration": {"it": "Durata", "en": "Duration", "de": "Dauer"},
    "minutes": {"it": "min", "en": "min", "de": "Min"}
}

# Destinazioni traghetti ACTV
FERRY_DESTINATIONS = {
    "venezia_sm": {
        "nome": {"it": "Venezia San Marco", "en": "Venice San Marco", "de": "Venedig San Marco"},
        "sottotitolo": {"it": "Piazza San Marco e centro storico", "en": "St. Mark's Square and historic center", "de": "Markusplatz und historisches Zentrum"},
        "emoji": "🏛️",
        "linea": "14",
        "partenza": "Punta Sabbioni",
        "arrivo": "San Marco",
        "durata": 30
    },
    "venezia_fn": {
        "nome": {"it": "Venezia Fondamente Nove", "en": "Venice Fondamente Nove", "de": "Venedig Fondamente Nove"},
        "sottotitolo": {"it": "Nord Venezia, vicino a Rialto", "en": "North Venice, near Rialto", "de": "Nord Venedig, nahe Rialto"},
        "emoji": "🌉",
        "linea": "12",
        "partenza": "Punta Sabbioni",
        "arrivo": "Fondamente Nove",
        "durata": 45
    },
    "isole": {
        "nome": {"it": "Isole della Laguna", "en": "Lagoon Islands", "de": "Laguneninseln"},
        "sottotitolo": {"it": "Burano, Murano, Torcello", "en": "Burano, Murano, Torcello", "de": "Burano, Murano, Torcello"},
        "emoji": "🏝️",
        "linea": "12",
        "partenza": "Punta Sabbioni",
        "arrivo": "Burano",
        "durata": 30
    },
    "lido": {
        "nome": {"it": "Lido di Venezia", "en": "Venice Lido", "de": "Lido von Venedig"},
        "sottotitolo": {"it": "Spiagge e Mostra del Cinema", "en": "Beaches and Film Festival", "de": "Strände und Filmfestspiele"},
        "emoji": "🎬",
        "linea": "14",
        "partenza": "Punta Sabbioni",
        "arrivo": "Lido S.M.E.",
        "durata": 15
    }
}

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


async def edit_message_safe(query, text: str, reply_markup=None, parse_mode: str = "HTML"):
    """Edita messaggio ignorando errore 'Message is not modified'"""
    try:
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        if "not modified" not in str(e).lower():
            raise


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
        # Cancella messaggio utente
        await delete_message_safe(context, chat_id, user_msg_id)

        # NON cancellare il messaggio bot se c'è pending_action (stiamo aspettando input orario)
        pending_action = user.get("pending_action") if user else None
        if last_bot_msg_id and not pending_action:
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

    # ============ CHECK PENDING ACTION (input orario trasporti) ============
    if message_text and user and not callback_query:
        pending_action = user.get("pending_action")
        logger.info(f"[PENDING] chat_id={chat_id}, pending_action={pending_action}, message={message_text[:50] if message_text else ''}")

        if pending_action:
            import json
            try:
                pending = json.loads(pending_action)
                logger.info(f"[PENDING] Parsed: {pending}")

                if pending.get("action") == "trasporti_orario":
                    # L'utente sta rispondendo con un orario
                    dest_id = pending.get("dest_id")
                    zona_id = pending.get("zona_id")
                    linea_codice = pending.get("linea_codice", "23A")
                    bot_msg_id = pending.get("bot_msg_id")

                    logger.info(f"[PENDING] Orario input: dest={dest_id}, zona={zona_id}, linea={linea_codice}, bot_msg={bot_msg_id}")

                    # Resetta pending action
                    db.update_user(chat_id, {"pending_action": None, "last_update_id": update_id})

                    # Parse orario - ritorna (ora_esatta, error)
                    ora_esatta, error = parse_time_input(message_text, lingua)

                    if error:
                        # Errore parsing - mostra errore nello stesso messaggio
                        error_text = f"⚠️ {error}\n\n<i>Riprova con formato HH:MM</i>"
                        if bot_msg_id:
                            try:
                                await context.bot.edit_message_text(
                                    chat_id=chat_id,
                                    message_id=bot_msg_id,
                                    text=error_text,
                                    parse_mode="HTML"
                                )
                            except Exception as e:
                                logger.error(f"[PENDING] Edit error msg failed: {e}")
                                await context.bot.send_message(chat_id=chat_id, text=error_text, parse_mode="HTML")
                        else:
                            await context.bot.send_message(chat_id=chat_id, text=error_text, parse_mode="HTML")
                        return

                    # Orario valido - mostra percorso editando il messaggio esistente
                    logger.info(f"[PENDING] Orario esatto: {ora_esatta}")
                    await handle_trasporti_percorso(context, chat_id, lingua, None, dest_id, zona_id, linea_codice, ora_esatta, bot_msg_id)
                    return
            except json.JSONDecodeError as e:
                logger.error(f"[PENDING] JSON decode error: {e}")
    # ============ FINE CHECK PENDING ACTION ============

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
    """Utente che ritorna - mostra menu principale con meteo, mare, suggerimento, evento"""
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

    # BLOCCO 1: Data + Meteo + Mare
    text = f"📅 <b>{giorno_nome} {now.day} {mese_nome}</b>\n"

    # Meteo attuale
    meteo_str = ""
    consiglio_meteo = ""
    weather_code = 3  # Default nuvoloso
    try:
        meteo = await asyncio.wait_for(get_meteo_forecast(), timeout=3)
        if meteo and meteo.get("current"):
            current = meteo["current"]
            temp = current.get("temperature", "")
            weather_code = current.get("weather_code", 0)
            emoji = get_weather_emoji(weather_code)
            desc = get_weather_description(weather_code, lingua)
            if temp:
                # Formato con virgola per italiano
                if isinstance(temp, (int, float)):
                    temp_str = f"{temp:.1f}".replace(".", ",") if lingua == "it" else f"{temp:.1f}"
                else:
                    temp_str = str(temp)
                text += f"🌡️ {temp_str}°C — {emoji} {desc}\n"
    except Exception:
        pass

    # Mare attuale
    try:
        from meteo_api import get_marine_conditions, get_wave_condition
        marine = await asyncio.wait_for(get_marine_conditions(), timeout=3)
        if marine and marine.get("current"):
            wave_height = marine["current"].get("wave_height", 0)
            mare_stato = get_wave_condition(wave_height, lingua)
            mare_label = {"it": "Mare", "en": "Sea", "de": "Meer"}.get(lingua, "Mare")
            text += f"🌊 {mare_label}: {mare_stato}\n"
    except Exception:
        pass

    # BLOCCO 2: Suggerimento
    suggerimento_label = {"it": "Suggerimento", "en": "Tip", "de": "Tipp"}.get(lingua, "Suggerimento")
    if weather_code in (0, 1, 2):  # Sereno / Poco nuvoloso
        consigli = {
            "it": "Giornata perfetta per la spiaggia!",
            "en": "Perfect day for the beach!",
            "de": "Perfekter Tag für den Strand!"
        }
    elif weather_code in (3, 45, 48):  # Coperto / Nebbia
        consigli = {
            "it": "Ottimo per una passeggiata o visitare i Fortini.",
            "en": "Great for a walk or visiting the Forts.",
            "de": "Ideal für einen Spaziergang oder die Festungen."
        }
    elif weather_code >= 51:  # Pioggia / Temporali
        consigli = {
            "it": "Giornata ideale per shopping o musei.",
            "en": "Ideal day for shopping or museums.",
            "de": "Idealer Tag für Shopping oder Museen."
        }
    else:
        consigli = {"it": "Buona giornata!", "en": "Have a nice day!", "de": "Schönen Tag!"}
    consiglio_meteo = consigli.get(lingua, consigli["it"])
    text += f"\n💡 <b>{suggerimento_label}</b>\n{consiglio_meteo}\n"

    # BLOCCO 3: Evento di oggi
    evento_oggi = db.get_evento_oggi(lingua)
    if evento_oggi:
        evento_label = {"it": "Evento di oggi", "en": "Today's event", "de": "Heutiges Event"}.get(lingua, "Evento di oggi")
        text += f"\n🎪 <b>{evento_label}</b>\n{evento_oggi}\n"

    # BLOCCO 4: Bentornato
    welcome = {"it": "Cosa posso fare per te?", "en": "What can I do for you?", "de": "Was kann ich für dich tun?"}.get(lingua, "Cosa posso fare per te?")
    bentornato = {"it": "Bentornato", "en": "Welcome back", "de": "Willkommen zurück"}.get(lingua, "Bentornato")
    text += f"\n👋 {bentornato}, {nome}!\n{welcome}"

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

    if menu_key in ("idee", "cosa_fare", "idee_oggi"):
        await handle_idee_oggi(context, chat_id, lingua, query)
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

    # ============ ROUTING IDEE PER OGGI ============
    if callback_data == "idee_spiagge":
        await handle_idee_spiagge(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if callback_data.startswith("idee_spiaggia_"):
        spiaggia_id = callback_data.replace("idee_spiaggia_", "")
        await handle_idee_spiaggia_dettaglio(context, chat_id, lingua, query, spiaggia_id)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if callback_data == "idee_fortini":
        await handle_idee_fortini(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if callback_data.startswith("idee_fortino_"):
        fortino_id = callback_data.replace("idee_fortino_", "")
        await handle_idee_fortino_dettaglio(context, chat_id, lingua, query, fortino_id)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if callback_data == "idee_attivita":
        await handle_idee_attivita(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if callback_data.startswith("idee_att_"):
        categoria = callback_data.replace("idee_att_", "")
        await handle_idee_attivita_categoria(context, chat_id, lingua, query, categoria)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if callback_data == "idee_pioggia":
        await handle_idee_pioggia(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if callback_data == "idee_laguna":
        await handle_idee_laguna(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if callback_data.startswith("idee_laguna_"):
        luogo_id = callback_data.replace("idee_laguna_", "")
        await handle_idee_laguna_dettaglio(context, chat_id, lingua, query, luogo_id)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    # ============ ROUTING EVENTI COMPLETO ============
    # evt_home - Home eventi
    if callback_data == "evt_home":
        await handle_eventi(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    # evt_oggi, evt_domani, evt_sett_0, evt_sett_1 - Liste per periodo
    if callback_data in ("evt_oggi", "evt_domani", "evt_sett_0", "evt_sett_1"):
        periodo = callback_data.replace("evt_", "")
        await handle_eventi_lista(context, chat_id, lingua, query, periodo=periodo, pagina=0)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    # evt_list_{periodo}_p{N} - Paginazione liste
    if callback_data.startswith("evt_list_"):
        import re
        match = re.match(r"evt_list_(\w+)_p(\d+)", callback_data)
        if match:
            periodo = match.group(1)
            pagina = int(match.group(2))
            await handle_eventi_lista(context, chat_id, lingua, query, periodo=periodo, pagina=pagina)
            db.update_user(chat_id, {"last_update_id": update_id})
            return

    # evt_categoria - Lista categorie
    if callback_data == "evt_categoria":
        await handle_eventi_categorie(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    # evt_cat_{tipo}_p{N} - Eventi per categoria con paginazione
    if callback_data.startswith("evt_cat_"):
        import re
        match = re.match(r"evt_cat_(\w+)_p(\d+)", callback_data)
        if match:
            categoria = match.group(1)
            pagina = int(match.group(2))
            # Per le categorie usiamo un periodo ampio (prossimi 90 giorni)
            from datetime import date, timedelta
            oggi = date.today()
            data_inizio = oggi.isoformat()
            data_fine = (oggi + timedelta(days=90)).isoformat()
            eventi = db.get_eventi_periodo(data_inizio, data_fine, limit=5, offset=pagina*5, categoria=categoria)
            totale = db.get_eventi_count_periodo(data_inizio, data_fine, categoria=categoria)
            # Richiama handle_eventi_lista con categoria
            await handle_eventi_lista(context, chat_id, lingua, query, periodo="sett_0", pagina=pagina, categoria=categoria)
            db.update_user(chat_id, {"last_update_id": update_id})
            return

    # evt_detail_{id} - Dettaglio evento
    if callback_data.startswith("evt_detail_"):
        try:
            evento_id = int(callback_data.replace("evt_detail_", ""))
            await handle_evento_dettaglio(context, chat_id, lingua, query, evento_id)
            db.update_user(chat_id, {"last_update_id": update_id})
            return
        except ValueError:
            pass

    # evt_cal - Calendario mese corrente
    if callback_data == "evt_cal":
        await handle_eventi_calendario(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    # evt_cal_{anno}_{mese} - Calendario mese specifico
    if callback_data.startswith("evt_cal_") and not callback_data.startswith("evt_cal_giorno_"):
        import re
        match = re.match(r"evt_cal_(\d+)_(\d+)", callback_data)
        if match:
            anno = int(match.group(1))
            mese = int(match.group(2))
            await handle_eventi_calendario(context, chat_id, lingua, query, anno=anno, mese=mese)
            db.update_user(chat_id, {"last_update_id": update_id})
            return

    # evt_cal_giorno_{anno}_{mese}_{giorno} - Eventi di un giorno specifico
    if callback_data.startswith("evt_cal_giorno_"):
        import re
        match = re.match(r"evt_cal_giorno_(\d+)_(\d+)_(\d+)", callback_data)
        if match:
            anno = int(match.group(1))
            mese = int(match.group(2))
            giorno = int(match.group(3))
            await handle_eventi_giorno(context, chat_id, lingua, query, anno, mese, giorno)
            db.update_user(chat_id, {"last_update_id": update_id})
            return

    # noop - Bottone placeholder (es. numero pagina)
    if callback_data == "noop":
        if query:
            await query.answer()
        return
    # ============ FINE ROUTING EVENTI ============

    if menu_key == "trasporti":
        await handle_trasporti(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    # ============ ROUTING TRASPORTI ============
    if callback_data == "tras_home":
        await handle_trasporti(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if callback_data == "tras_arrivo":
        await handle_trasporti_arrivo(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if callback_data.startswith("tras_dest_"):
        try:
            dest_id = int(callback_data.replace("tras_dest_", ""))
            await handle_trasporti_zona(context, chat_id, lingua, query, dest_id)
            db.update_user(chat_id, {"last_update_id": update_id})
            return
        except ValueError:
            pass

    if callback_data.startswith("tras_percorso_"):
        try:
            # tras_percorso_{dest}_{zona}_{linea} oppure tras_percorso_{dest}_{zona} (retrocompatibilità)
            parts = callback_data.replace("tras_percorso_", "").split("_")
            dest_id = int(parts[0])
            zona_id = int(parts[1]) if len(parts) > 1 and parts[1] != "0" else None
            linea_codice = parts[2] if len(parts) > 2 else "23A"
            # Mostra selezione orario con linea
            await handle_trasporti_quando(context, chat_id, lingua, query, dest_id, zona_id, linea_codice)
            db.update_user(chat_id, {"last_update_id": update_id})
            return
        except (ValueError, IndexError):
            pass

    if callback_data.startswith("tras_viaggio_"):
        try:
            # tras_viaggio_{dest}_{zona}_{linea}_{ora} dove ora è HH-MM
            parts = callback_data.replace("tras_viaggio_", "").split("_")
            dest_id = int(parts[0])
            zona_id = int(parts[1]) if len(parts) > 1 and parts[1] != "0" else None
            if len(parts) > 3:
                linea_codice = parts[2]
                # Nuovo formato: ora esatta HH-MM
                ora_param = parts[3]
                if "-" in ora_param and len(ora_param) == 5:
                    # Formato HH-MM -> HH:MM
                    ora_partenza = ora_param.replace("-", ":")
                else:
                    # Retrocompatibilità: offset in minuti
                    ora_partenza = None
            elif len(parts) > 2:
                linea_codice = parts[2]
                ora_partenza = None
            else:
                linea_codice = "23A"
                ora_partenza = None
            await handle_trasporti_percorso(context, chat_id, lingua, query, dest_id, zona_id, linea_codice, ora_partenza)
            db.update_user(chat_id, {"last_update_id": update_id})
            return
        except (ValueError, IndexError):
            pass

    if callback_data == "tras_frazione":
        await handle_trasporti_frazione(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    # Handler frazioni - ordine specifico per pattern matching corretto
    if callback_data.startswith("tras_fraz_viaggio_"):
        try:
            # tras_fraz_viaggio_{da}_{a}_{linea}_{offset}
            parts = callback_data.replace("tras_fraz_viaggio_", "").split("_")
            da_zona = int(parts[0])
            a_zona = int(parts[1])
            linea_codice = parts[2]
            offset = int(parts[3]) if len(parts) > 3 else 0
            await handle_trasporti_frazione_viaggio(context, chat_id, lingua, query, da_zona, a_zona, linea_codice, offset)
            db.update_user(chat_id, {"last_update_id": update_id})
            return
        except (ValueError, IndexError):
            pass

    if callback_data.startswith("tras_fraz_quando_"):
        try:
            # tras_fraz_quando_{da}_{a}_{linea}
            parts = callback_data.replace("tras_fraz_quando_", "").split("_")
            da_zona = int(parts[0])
            a_zona = int(parts[1])
            linea_codice = parts[2]
            await handle_trasporti_frazione_quando(context, chat_id, lingua, query, da_zona, a_zona, linea_codice)
            db.update_user(chat_id, {"last_update_id": update_id})
            return
        except (ValueError, IndexError):
            pass

    if callback_data.startswith("tras_fraz_linea_"):
        try:
            # tras_fraz_linea_{da}_{a}
            parts = callback_data.replace("tras_fraz_linea_", "").split("_")
            da_zona = int(parts[0])
            a_zona = int(parts[1])
            await handle_trasporti_frazione_linea(context, chat_id, lingua, query, da_zona, a_zona)
            db.update_user(chat_id, {"last_update_id": update_id})
            return
        except (ValueError, IndexError):
            pass

    if callback_data.startswith("tras_fraz_"):
        try:
            # tras_fraz_{da_zona}_{a_zona}
            parts = callback_data.replace("tras_fraz_", "").split("_")
            da_zona = int(parts[0])
            a_zona = int(parts[1]) if len(parts) > 1 else None
            await handle_trasporti_frazione_percorso(context, chat_id, lingua, query, da_zona, a_zona)
            db.update_user(chat_id, {"last_update_id": update_id})
            return
        except (ValueError, IndexError):
            pass

    if callback_data == "tras_bus":
        await handle_trasporti_bus(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if callback_data.startswith("tras_bus_linea_"):
        try:
            linea_id = int(callback_data.replace("tras_bus_linea_", ""))
            await handle_trasporti_linea(context, chat_id, lingua, query, linea_id, "bus")
            db.update_user(chat_id, {"last_update_id": update_id})
            return
        except ValueError:
            pass

    if callback_data == "tras_ferry":
        await handle_trasporti_ferry(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if callback_data.startswith("tras_ferry_linea_"):
        try:
            linea_id = int(callback_data.replace("tras_ferry_linea_", ""))
            await handle_trasporti_linea(context, chat_id, lingua, query, linea_id, "ferry")
            db.update_user(chat_id, {"last_update_id": update_id})
            return
        except ValueError:
            pass

    # Routing traghetti per destinazione
    if callback_data.startswith("tras_ferry_dest_"):
        dest_key = callback_data.replace("tras_ferry_dest_", "")
        await handle_trasporti_ferry_destinazione(context, chat_id, lingua, query, dest_key)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if callback_data.startswith("tras_ferry_orari_"):
        # tras_ferry_orari_{dest_key}_{direzione}
        parts = callback_data.replace("tras_ferry_orari_", "").rsplit("_", 1)
        if len(parts) == 2:
            dest_key, direzione = parts
            await handle_trasporti_ferry_orari(context, chat_id, lingua, query, dest_key, direzione)
            db.update_user(chat_id, {"last_update_id": update_id})
            return

    if callback_data.startswith("tras_ferry_info_"):
        dest_key = callback_data.replace("tras_ferry_info_", "")
        await handle_trasporti_ferry_info(context, chat_id, lingua, query, dest_key)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if callback_data == "tras_prezzi":
        await handle_trasporti_prezzi(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if callback_data.startswith("tras_prezzi_op_"):
        try:
            op_id = int(callback_data.replace("tras_prezzi_op_", ""))
            await handle_trasporti_prezzi_operatore(context, chat_id, lingua, query, op_id)
            db.update_user(chat_id, {"last_update_id": update_id})
            return
        except ValueError:
            pass

    if callback_data.startswith("tras_orari_"):
        try:
            # tras_orari_{dest}_{zona}_{linea}_{ora} dove ora è HH-MM o "now"
            parts = callback_data.replace("tras_orari_", "").split("_")
            dest_id = int(parts[0])
            zona_id = int(parts[1]) if len(parts) > 1 and parts[1] != "0" else None
            if len(parts) > 3:
                linea_codice = parts[2]
                ora_param = parts[3]
                # Nuovo formato: ora esatta HH-MM o "now"
                if ora_param == "now":
                    ora_partenza = None
                elif "-" in ora_param:
                    ora_partenza = ora_param.replace("-", ":")
                else:
                    ora_partenza = None
            elif len(parts) > 2:
                linea_codice = parts[2]
                ora_partenza = None
            else:
                linea_codice = "23A"
                ora_partenza = None
            await handle_trasporti_orari(context, chat_id, lingua, query, dest_id, zona_id, linea_codice, ora_partenza)
            db.update_user(chat_id, {"last_update_id": update_id})
            return
        except (ValueError, IndexError):
            pass

    # Selezione partenza specifica (1, 2, 3)
    if callback_data.startswith("tras_dep_"):
        try:
            # tras_dep_{dest}_{zona}_{linea}_{index}_{ora} dove ora è HH-MM o "now"
            parts = callback_data.replace("tras_dep_", "").split("_")
            dest_id = int(parts[0])
            zona_id = int(parts[1]) if len(parts) > 1 and parts[1] != "0" else None
            # Controlla nuovo formato con linea e ora
            if len(parts) > 4:
                linea_codice = parts[2]
                dep_index = int(parts[3])
                ora_param = parts[4]
                # Converti HH-MM in HH:MM, "now" diventa None
                if ora_param == "now":
                    ora_partenza = None
                elif "-" in ora_param:
                    ora_partenza = ora_param.replace("-", ":")
                else:
                    ora_partenza = None
            else:
                # Retrocompatibilità
                linea_codice = "23A"
                dep_index = int(parts[2]) if len(parts) > 2 else 0
                ora_partenza = None
            await handle_trasporti_dep_select(context, chat_id, lingua, query, dest_id, zona_id, linea_codice, dep_index, ora_partenza)
            db.update_user(chat_id, {"last_update_id": update_id})
            return
        except (ValueError, IndexError):
            pass

    if callback_data.startswith("tras_ritorno_"):
        try:
            parts = callback_data.replace("tras_ritorno_", "").split("_")
            dest_id = int(parts[0])
            zona_id = int(parts[1]) if len(parts) > 1 and parts[1] != "0" else None
            await handle_trasporti_ritorno(context, chat_id, lingua, query, dest_id, zona_id)
            db.update_user(chat_id, {"last_update_id": update_id})
            return
        except (ValueError, IndexError):
            pass

    if callback_data.startswith("tras_orario_custom_"):
        try:
            parts = callback_data.replace("tras_orario_custom_", "").split("_")
            dest_id = int(parts[0])
            zona_id = int(parts[1]) if len(parts) > 1 and parts[1] != "0" else None
            linea_codice = parts[2] if len(parts) > 2 else "23A"
            await handle_trasporti_orario_custom(context, chat_id, lingua, query, dest_id, zona_id, linea_codice)
            db.update_user(chat_id, {"last_update_id": update_id})
            return
        except (ValueError, IndexError):
            pass

    if callback_data.startswith("tras_fermata_"):
        try:
            # tras_fermata_{dest}_{zona}
            parts = callback_data.replace("tras_fermata_", "").split("_")
            dest_id = int(parts[0])
            zona_id = int(parts[1]) if len(parts) > 1 else None
            await handle_trasporti_fermata(context, chat_id, lingua, query, dest_id, zona_id)
            db.update_user(chat_id, {"last_update_id": update_id})
            return
        except (ValueError, IndexError):
            pass

    if callback_data == "tras_isole":
        await handle_trasporti_isole(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if callback_data == "tras_paese":
        await handle_trasporti_paese(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    # ============ FINE ROUTING TRASPORTI ============

    # ============ ROUTING FORTINI ============
    if callback_data == "menu_fortini":
        await handle_fortini(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if callback_data == "fort_zone":
        await handle_fortini_zone(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if callback_data.startswith("fort_zona_"):
        zona_key = callback_data.replace("fort_zona_", "")
        await handle_fortini_lista(context, chat_id, lingua, query, zona_key)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if callback_data.startswith("fort_detail_"):
        fortino_id = callback_data.replace("fort_detail_", "")
        await handle_fortini_dettaglio(context, chat_id, lingua, query, fortino_id)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if callback_data == "fort_percorsi":
        await handle_percorsi_lista(context, chat_id, lingua, query)
        db.update_user(chat_id, {"last_update_id": update_id})
        return

    if callback_data.startswith("fort_percorso_"):
        percorso_id = callback_data.replace("fort_percorso_", "")
        await handle_percorsi_dettaglio(context, chat_id, lingua, query, percorso_id)
        db.update_user(chat_id, {"last_update_id": update_id})
        return
    # ============ FINE ROUTING FORTINI ============

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
            InlineKeyboardButton("💡 Idee", callback_data="menu_idee"),
            InlineKeyboardButton("🏰 Fortini", callback_data="menu_fortini")
        ],
        [
            InlineKeyboardButton("🚌 Trasporti", callback_data="menu_trasporti"),
            InlineKeyboardButton("🍽️ Ristoranti", callback_data="menu_ristoranti")
        ],
        [
            InlineKeyboardButton("🆘 Emergenza", callback_data="menu_sos")
        ]
    ])


# ============================================================
# HANDLER METEO E ATTIVITA'
# ============================================================

async def handle_meteo(context, chat_id: int, lingua: str, query=None):
    """
    Mostra meteo atmosferico - formato pulito a 3 blocchi.
    """
    # Rispondi al callback SUBITO
    if query:
        await query.answer()

    from meteo_api import get_meteo_forecast, get_weather_emoji, get_weather_description
    from datetime import datetime as dt
    import locale

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
        weather_code = current.get("weather_code", 0)
        emoji = get_weather_emoji(weather_code)
        desc = get_weather_description(weather_code, lingua)
        temp = current.get("temperature", "N/D")

        # Formatta temperatura con virgola (stile italiano)
        if isinstance(temp, (int, float)):
            temp_str = f"{temp:.1f}".replace(".", ",")
        else:
            temp_str = str(temp)

        # BLOCCO 1: Data e meteo
        # Nomi giorni e mesi localizzati
        giorni = {
            "it": ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"],
            "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
            "de": ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
        }
        mesi = {
            "it": ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"],
            "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
            "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
        }

        now = dt.now()
        giorno_nome = giorni.get(lingua, giorni["it"])[now.weekday()]
        mese_nome = mesi.get(lingua, mesi["it"])[now.month - 1]
        data_formattata = f"{giorno_nome} {now.day} {mese_nome}"

        text = f"📅 <b>{data_formattata}</b>\n"
        text += f"🌡️ {temp_str}°C — {emoji} {desc}\n"

        # BLOCCO 2: Suggerimento (separato da riga vuota)
        condizione = "sole" if weather_code in (0, 1, 2) else "pioggia" if weather_code >= 51 else "nuvole"
        consiglio = db.get_consiglio_meteo(condizione, lingua)

        # Fallback suggerimenti hardcoded
        if not consiglio:
            suggerimenti_fallback = {
                "sole": {
                    "it": "Perfetto per la spiaggia o un giro in bici!",
                    "en": "Perfect for the beach or a bike ride!",
                    "de": "Perfekt für den Strand oder eine Radtour!"
                },
                "nuvole": {
                    "it": "Ottimo per una passeggiata o visitare i Fortini.",
                    "en": "Great for a walk or visiting the Forts.",
                    "de": "Ideal für einen Spaziergang oder die Festungen."
                },
                "pioggia": {
                    "it": "Giornata ideale per shopping o musei.",
                    "en": "Ideal day for shopping or museums.",
                    "de": "Idealer Tag für Shopping oder Museen."
                }
            }
            consiglio = suggerimenti_fallback.get(condizione, suggerimenti_fallback["nuvole"]).get(lingua, suggerimenti_fallback[condizione]["it"])

        suggerimento_label = {"it": "Suggerimento", "en": "Tip", "de": "Tipp"}.get(lingua, "Suggerimento")
        text += f"\n💡 <b>{suggerimento_label}</b>\n{consiglio}\n"

        # BLOCCO 3: Evento di oggi (se presente, separato)
        evento_oggi = db.get_evento_oggi(lingua)
        if evento_oggi:
            evento_label = {"it": "Evento di oggi", "en": "Today's event", "de": "Heutiges Event"}.get(lingua, "Evento di oggi")
            text += f"\n🎪 <b>{evento_label}</b>\n{evento_oggi}\n"

        text += "\n🦭 <i>SLAPPY</i>"

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
    Mostra condizioni mare - SOLO info mare, niente suggerimenti o eventi.
    """
    if query:
        await query.answer()

    from meteo_api import get_marine_conditions, get_wave_condition

    try:
        marine = await asyncio.wait_for(get_marine_conditions(), timeout=API_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("Timeout chiamata API mare")
        marine = None
    except Exception as e:
        logger.error(f"Errore API mare: {e}")
        marine = None

    if not marine:
        text = "⚠️ Impossibile ottenere dati mare. Riprova più tardi."
    else:
        current = marine["current"]
        wave_height = current.get("wave_height", 0)
        condition = get_wave_condition(wave_height, lingua)

        # Labels
        header = {"it": "MARE", "en": "SEA", "de": "MEER"}.get(lingua, "MARE")
        stato_label = {"it": "Stato", "en": "Condition", "de": "Zustand"}.get(lingua, "Stato")
        onde_label = {"it": "Altezza onde", "en": "Wave height", "de": "Wellenhöhe"}.get(lingua, "Altezza onde")

        text = f"🌊 <b>{header}</b>\n\n"
        text += f"📊 {stato_label}: <b>{condition}</b>\n"
        text += f"📏 {onde_label}: {wave_height or 'N/D'} m\n"

        # Avviso solo se mare mosso (no suggerimenti generici)
        if wave_height and wave_height > 1.0:
            avviso = {"it": "⚠️ Mare mosso, prestare attenzione", "en": "⚠️ Rough sea, be careful", "de": "⚠️ Unruhige See, Vorsicht"}.get(lingua)
            text += f"\n{avviso}"

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


async def handle_idee_oggi(context, chat_id: int, lingua: str, query=None):
    """
    Menu principale IDEE PER OGGI con 5 macro-categorie.
    """
    if query:
        await query.answer()

    header = {
        "it": "IDEE PER OGGI",
        "en": "IDEAS FOR TODAY",
        "de": "IDEEN FÜR HEUTE"
    }.get(lingua, "IDEE PER OGGI")

    subtitle = {
        "it": "Cosa ti va di fare?",
        "en": "What do you feel like doing?",
        "de": "Was möchtest du machen?"
    }.get(lingua, "Cosa ti va di fare?")

    text = f"💡 <b>{header}</b>\n\n{subtitle}"

    # Labels multilingua
    labels = {
        "it": {"spiagge": "🏖️ Spiagge", "fortini": "🏰 Fortini", "attivita": "🚴 Attività", "pioggia": "🌧️ Cosa fare con la pioggia", "laguna": "🌿 Laguna"},
        "en": {"spiagge": "🏖️ Beaches", "fortini": "🏰 Forts", "attivita": "🚴 Activities", "pioggia": "🌧️ Rainy day ideas", "laguna": "🌿 Lagoon"},
        "de": {"spiagge": "🏖️ Strände", "fortini": "🏰 Festungen", "attivita": "🚴 Aktivitäten", "pioggia": "🌧️ Bei Regen", "laguna": "🌿 Lagune"}
    }
    L = labels.get(lingua, labels["it"])

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(L["spiagge"], callback_data="idee_spiagge")],
        [InlineKeyboardButton(L["fortini"], callback_data="idee_fortini")],
        [InlineKeyboardButton(L["attivita"], callback_data="idee_attivita")],
        [InlineKeyboardButton(L["pioggia"], callback_data="idee_pioggia")],
        [InlineKeyboardButton(L["laguna"], callback_data="idee_laguna")],
        [InlineKeyboardButton("◀️ Menu", callback_data="menu_back")]
    ])

    if query:
        await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML")


# Alias per retrocompatibilità
async def handle_cosa_fare(context, chat_id: int, lingua: str, query=None):
    await handle_idee_oggi(context, chat_id, lingua, query)


# ============================================================
# SEZIONE SPIAGGE
# ============================================================

SPIAGGE_DATA = {
    "punta_sabbioni": {
        "nome": {"it": "Punta Sabbioni", "en": "Punta Sabbioni", "de": "Punta Sabbioni"},
        "tipo": {"it": "Famiglie e relax", "en": "Families and relaxation", "de": "Familien und Entspannung"},
        "punti": {
            "it": ["Spiaggia attrezzata con servizi", "Vicina al terminal traghetti per Venezia", "Ideale per famiglie con bambini"],
            "en": ["Equipped beach with services", "Near the ferry terminal to Venice", "Ideal for families with children"],
            "de": ["Ausgestatteter Strand mit Service", "Nahe dem Fährterminal nach Venedig", "Ideal für Familien mit Kindern"]
        },
        "maps": "https://maps.google.com/?q=45.4389,12.4183"
    },
    "cavallino": {
        "nome": {"it": "Cavallino", "en": "Cavallino", "de": "Cavallino"},
        "tipo": {"it": "Sport e giovani", "en": "Sports and youth", "de": "Sport und Jugend"},
        "punti": {
            "it": ["Spiaggia ampia e ventilata", "Perfetta per windsurf e kitesurf", "Beach volley e sport acquatici"],
            "en": ["Wide and windy beach", "Perfect for windsurfing and kitesurfing", "Beach volleyball and water sports"],
            "de": ["Breiter und windiger Strand", "Perfekt für Windsurfen und Kitesurfen", "Beachvolleyball und Wassersport"]
        },
        "maps": "https://maps.google.com/?q=45.4650,12.5150"
    },
    "ca_savio": {
        "nome": {"it": "Ca' Savio", "en": "Ca' Savio", "de": "Ca' Savio"},
        "tipo": {"it": "Relax e natura", "en": "Relaxation and nature", "de": "Entspannung und Natur"},
        "punti": {
            "it": ["Spiaggia tranquilla e poco affollata", "Dune naturali protette", "Ottima per passeggiate al tramonto"],
            "en": ["Quiet and uncrowded beach", "Protected natural dunes", "Great for sunset walks"],
            "de": ["Ruhiger und wenig überfüllter Strand", "Geschützte Naturdünen", "Ideal für Sonnenuntergangsspaziergänge"]
        },
        "maps": "https://maps.google.com/?q=45.4833,12.5500"
    },
    "treporti": {
        "nome": {"it": "Treporti", "en": "Treporti", "de": "Treporti"},
        "tipo": {"it": "Famiglie e camping", "en": "Families and camping", "de": "Familien und Camping"},
        "punti": {
            "it": ["Vicina ai principali campeggi", "Servizi per famiglie", "Acque basse e sicure per bambini"],
            "en": ["Close to main campsites", "Family services", "Shallow and safe waters for children"],
            "de": ["Nahe den Hauptcampingplätzen", "Familienservice", "Flaches und sicheres Wasser für Kinder"]
        },
        "maps": "https://maps.google.com/?q=45.4550,12.4650"
    }
}


async def handle_idee_spiagge(context, chat_id: int, lingua: str, query=None):
    """Lista spiagge con 4 pulsanti."""
    if query:
        await query.answer()

    header = {"it": "SPIAGGE", "en": "BEACHES", "de": "STRÄNDE"}.get(lingua, "SPIAGGE")
    subtitle = {"it": "Scegli una spiaggia:", "en": "Choose a beach:", "de": "Wähle einen Strand:"}.get(lingua, "Scegli una spiaggia:")

    text = f"🏖️ <b>{header}</b>\n\n{subtitle}"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏖️ Punta Sabbioni", callback_data="idee_spiaggia_punta_sabbioni")],
        [InlineKeyboardButton("🏖️ Cavallino", callback_data="idee_spiaggia_cavallino")],
        [InlineKeyboardButton("🏖️ Ca' Savio", callback_data="idee_spiaggia_ca_savio")],
        [InlineKeyboardButton("🏖️ Treporti", callback_data="idee_spiaggia_treporti")],
        [InlineKeyboardButton("◀️ Indietro", callback_data="menu_idee")]
    ])

    if query:
        await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML")


async def handle_idee_spiaggia_dettaglio(context, chat_id: int, lingua: str, query, spiaggia_id: str):
    """Dettaglio singola spiaggia."""
    if query:
        await query.answer()

    spiaggia = SPIAGGE_DATA.get(spiaggia_id)
    if not spiaggia:
        await handle_idee_spiagge(context, chat_id, lingua, query)
        return

    nome = spiaggia["nome"].get(lingua, spiaggia["nome"]["it"])
    tipo = spiaggia["tipo"].get(lingua, spiaggia["tipo"]["it"])
    punti = spiaggia["punti"].get(lingua, spiaggia["punti"]["it"])
    maps_url = spiaggia["maps"]

    text = f"🏖️ <b>{nome}</b>\n\n"
    text += f"📌 <i>{tipo}</i>\n\n"
    for punto in punti:
        text += f"• {punto}\n"

    btn_naviga = {"it": "📍 Come arrivare", "en": "📍 How to get there", "de": "📍 So kommen Sie hin"}.get(lingua, "📍 Come arrivare")
    btn_indietro = {"it": "◀️ Indietro", "en": "◀️ Back", "de": "◀️ Zurück"}.get(lingua, "◀️ Indietro")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(btn_naviga, url=maps_url)],
        [InlineKeyboardButton(btn_indietro, callback_data="idee_spiagge")]
    ])

    if query:
        await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML")


# ============================================================
# SEZIONE FORTINI
# ============================================================

FORTINI_DATA = {
    "treporti": {
        "nome": {"it": "Forte Treporti", "en": "Treporti Fort", "de": "Festung Treporti"},
        "dove": {"it": "Treporti, lungo Via Fausta", "en": "Treporti, along Via Fausta", "de": "Treporti, entlang der Via Fausta"},
        "come": {"it": "In bici: 10 min da Punta Sabbioni\nA piedi: 25 min", "en": "By bike: 10 min from Punta Sabbioni\nOn foot: 25 min", "de": "Mit dem Fahrrad: 10 min von Punta Sabbioni\nZu Fuß: 25 min"},
        "tempo": {"it": "30-45 min", "en": "30-45 min", "de": "30-45 min"},
        "maps": "https://maps.google.com/?q=45.4480,12.4350"
    },
    "cavallino": {
        "nome": {"it": "Batteria Amalfi", "en": "Amalfi Battery", "de": "Batterie Amalfi"},
        "dove": {"it": "Cavallino, vicino a Ca' Savio", "en": "Cavallino, near Ca' Savio", "de": "Cavallino, nahe Ca' Savio"},
        "come": {"it": "In bici: 15 min da Punta Sabbioni\nA piedi: 40 min", "en": "By bike: 15 min from Punta Sabbioni\nOn foot: 40 min", "de": "Mit dem Fahrrad: 15 min von Punta Sabbioni\nZu Fuß: 40 min"},
        "tempo": {"it": "20-30 min", "en": "20-30 min", "de": "20-30 min"},
        "maps": "https://maps.google.com/?q=45.4750,12.5000"
    },
    "vecchia": {
        "nome": {"it": "Batteria Vecchia", "en": "Old Battery", "de": "Alte Batterie"},
        "dove": {"it": "Punta Sabbioni, area porto", "en": "Punta Sabbioni, port area", "de": "Punta Sabbioni, Hafengebiet"},
        "come": {"it": "A piedi: 5 min dal terminal\nIn bici: 2 min", "en": "On foot: 5 min from terminal\nBy bike: 2 min", "de": "Zu Fuß: 5 min vom Terminal\nMit dem Fahrrad: 2 min"},
        "tempo": {"it": "15-20 min", "en": "15-20 min", "de": "15-20 min"},
        "maps": "https://maps.google.com/?q=45.4400,12.4200"
    },
    "pisani": {
        "nome": {"it": "Forte Ca' Pasquali", "en": "Ca' Pasquali Fort", "de": "Festung Ca' Pasquali"},
        "dove": {"it": "Ca' Pasquali, zona campeggi", "en": "Ca' Pasquali, camping area", "de": "Ca' Pasquali, Campingbereich"},
        "come": {"it": "In bici: 20 min da Punta Sabbioni\nA piedi: 50 min", "en": "By bike: 20 min from Punta Sabbioni\nOn foot: 50 min", "de": "Mit dem Fahrrad: 20 min von Punta Sabbioni\nZu Fuß: 50 min"},
        "tempo": {"it": "30-40 min", "en": "30-40 min", "de": "30-40 min"},
        "maps": "https://maps.google.com/?q=45.4900,12.5350"
    }
}


async def handle_idee_fortini(context, chat_id: int, lingua: str, query=None):
    """Lista fortini."""
    if query:
        await query.answer()

    header = {"it": "FORTINI", "en": "FORTS", "de": "FESTUNGEN"}.get(lingua, "FORTINI")
    subtitle = {"it": "Testimonianze storiche della zona:", "en": "Historical heritage of the area:", "de": "Historisches Erbe der Gegend:"}.get(lingua, "Testimonianze storiche della zona:")

    text = f"🏰 <b>{header}</b>\n\n{subtitle}"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏰 Forte Treporti", callback_data="idee_fortino_treporti")],
        [InlineKeyboardButton("🏰 Batteria Amalfi", callback_data="idee_fortino_cavallino")],
        [InlineKeyboardButton("🏰 Batteria Vecchia", callback_data="idee_fortino_vecchia")],
        [InlineKeyboardButton("🏰 Forte Ca' Pasquali", callback_data="idee_fortino_pisani")],
        [InlineKeyboardButton("◀️ Indietro", callback_data="menu_idee")]
    ])

    if query:
        await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML")


async def handle_idee_fortino_dettaglio(context, chat_id: int, lingua: str, query, fortino_id: str):
    """Dettaglio singolo fortino."""
    if query:
        await query.answer()

    fortino = FORTINI_DATA.get(fortino_id)
    if not fortino:
        await handle_idee_fortini(context, chat_id, lingua, query)
        return

    nome = fortino["nome"].get(lingua, fortino["nome"]["it"])
    dove = fortino["dove"].get(lingua, fortino["dove"]["it"])
    come = fortino["come"].get(lingua, fortino["come"]["it"])
    tempo = fortino["tempo"].get(lingua, fortino["tempo"]["it"])
    maps_url = fortino["maps"]

    dove_label = {"it": "Dove", "en": "Where", "de": "Wo"}.get(lingua, "Dove")
    come_label = {"it": "Come arrivare", "en": "How to get there", "de": "Anfahrt"}.get(lingua, "Come arrivare")
    tempo_label = {"it": "Tempo visita", "en": "Visit time", "de": "Besuchszeit"}.get(lingua, "Tempo visita")

    text = f"🏰 <b>{nome}</b>\n\n"
    text += f"📍 <b>{dove_label}:</b> {dove}\n\n"
    text += f"🚴 <b>{come_label}:</b>\n{come}\n\n"
    text += f"⏱️ <b>{tempo_label}:</b> {tempo}"

    btn_naviga = {"it": "📍 Naviga", "en": "📍 Navigate", "de": "📍 Navigieren"}.get(lingua, "📍 Naviga")
    btn_indietro = {"it": "◀️ Indietro", "en": "◀️ Back", "de": "◀️ Zurück"}.get(lingua, "◀️ Indietro")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(btn_naviga, url=maps_url)],
        [InlineKeyboardButton(btn_indietro, callback_data="idee_fortini")]
    ])

    if query:
        await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML")


# ============================================================
# SEZIONE ATTIVITÀ
# ============================================================

async def handle_idee_attivita(context, chat_id: int, lingua: str, query=None):
    """Menu attività con 3 sottocategorie."""
    if query:
        await query.answer()

    header = {"it": "ATTIVITÀ", "en": "ACTIVITIES", "de": "AKTIVITÄTEN"}.get(lingua, "ATTIVITÀ")
    subtitle = {"it": "Scegli una categoria:", "en": "Choose a category:", "de": "Wähle eine Kategorie:"}.get(lingua, "Scegli una categoria:")

    text = f"🚴 <b>{header}</b>\n\n{subtitle}"

    labels = {
        "it": {"outdoor": "🚴 Outdoor", "natura": "🦅 Natura", "acqua": "🚣 Acqua"},
        "en": {"outdoor": "🚴 Outdoor", "natura": "🦅 Nature", "acqua": "🚣 Water"},
        "de": {"outdoor": "🚴 Outdoor", "natura": "🦅 Natur", "acqua": "🚣 Wasser"}
    }
    L = labels.get(lingua, labels["it"])

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(L["outdoor"], callback_data="idee_att_outdoor")],
        [InlineKeyboardButton(L["natura"], callback_data="idee_att_natura")],
        [InlineKeyboardButton(L["acqua"], callback_data="idee_att_acqua")],
        [InlineKeyboardButton("◀️ Indietro", callback_data="menu_idee")]
    ])

    if query:
        await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML")


ATTIVITA_DATA = {
    "outdoor": {
        "header": {"it": "OUTDOOR", "en": "OUTDOOR", "de": "OUTDOOR"},
        "contenuto": {
            "it": """🚴 <b>Ciclismo</b>
• 40+ km di piste ciclabili
• Noleggio bici in ogni campeggio
• Percorso Punta Sabbioni - Jesolo

⚽ <b>Sport</b>
• Beach volley sulla spiaggia
• Campi da tennis nei campeggi
• Minigolf e calcetto""",
            "en": """🚴 <b>Cycling</b>
• 40+ km of bike paths
• Bike rental at every campsite
• Punta Sabbioni - Jesolo route

⚽ <b>Sports</b>
• Beach volleyball on the beach
• Tennis courts at campsites
• Mini golf and five-a-side""",
            "de": """🚴 <b>Radfahren</b>
• 40+ km Radwege
• Fahrradverleih auf jedem Campingplatz
• Route Punta Sabbioni - Jesolo

⚽ <b>Sport</b>
• Beachvolleyball am Strand
• Tennisplätze auf Campingplätzen
• Minigolf und Fußball"""
        }
    },
    "natura": {
        "header": {"it": "NATURA", "en": "NATURE", "de": "NATUR"},
        "contenuto": {
            "it": """🦅 <b>Birdwatching</b>
• Oasi naturale di Ca' Savio
• Laguna di Venezia
• Oltre 50 specie di uccelli

🚶 <b>Passeggiate</b>
• Sentiero delle dune
• Percorso lagunare Lio Piccolo
• Tramonto sulla laguna""",
            "en": """🦅 <b>Birdwatching</b>
• Ca' Savio natural oasis
• Venice Lagoon
• Over 50 bird species

🚶 <b>Walks</b>
• Dunes trail
• Lio Piccolo lagoon path
• Sunset on the lagoon""",
            "de": """🦅 <b>Vogelbeobachtung</b>
• Naturoase Ca' Savio
• Lagune von Venedig
• Über 50 Vogelarten

🚶 <b>Spaziergänge</b>
• Dünenweg
• Lagunenweg Lio Piccolo
• Sonnenuntergang an der Lagune"""
        }
    },
    "acqua": {
        "header": {"it": "SPORT ACQUATICI", "en": "WATER SPORTS", "de": "WASSERSPORT"},
        "contenuto": {
            "it": """🏄 <b>Vela e vento</b>
• Windsurf e kitesurf
• Scuole certificate
• Noleggio attrezzatura

🚣 <b>Pagaia</b>
• SUP (Stand Up Paddle)
• Kayak in laguna
• Tour guidati in canoa

🎣 <b>Pesca</b>
• Pescaturismo in laguna
• Pesca sportiva""",
            "en": """🏄 <b>Sailing and wind</b>
• Windsurfing and kitesurfing
• Certified schools
• Equipment rental

🚣 <b>Paddling</b>
• SUP (Stand Up Paddle)
• Kayaking in the lagoon
• Guided canoe tours

🎣 <b>Fishing</b>
• Fishing tourism in the lagoon
• Sport fishing""",
            "de": """🏄 <b>Segeln und Wind</b>
• Windsurfen und Kitesurfen
• Zertifizierte Schulen
• Ausrüstungsverleih

🚣 <b>Paddeln</b>
• SUP (Stand Up Paddle)
• Kajakfahren in der Lagune
• Geführte Kanutouren

🎣 <b>Angeln</b>
• Angeltourismus in der Lagune
• Sportfischen"""
        }
    }
}


async def handle_idee_attivita_categoria(context, chat_id: int, lingua: str, query, categoria: str):
    """Mostra attività per categoria."""
    if query:
        await query.answer()

    data = ATTIVITA_DATA.get(categoria)
    if not data:
        await handle_idee_attivita(context, chat_id, lingua, query)
        return

    header = data["header"].get(lingua, data["header"]["it"])
    contenuto = data["contenuto"].get(lingua, data["contenuto"]["it"])

    text = f"🎯 <b>{header}</b>\n\n{contenuto}"

    btn_indietro = {"it": "◀️ Indietro", "en": "◀️ Back", "de": "◀️ Zurück"}.get(lingua, "◀️ Indietro")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(btn_indietro, callback_data="idee_attivita")]
    ])

    if query:
        await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML")


# ============================================================
# SEZIONE PIOGGIA (rinominata)
# ============================================================

async def handle_idee_pioggia(context, chat_id: int, lingua: str, query=None):
    """Cosa fare con la pioggia - 4 categorie."""
    if query:
        await query.answer()

    header = {"it": "COSA FARE CON LA PIOGGIA", "en": "RAINY DAY IDEAS", "de": "IDEEN BEI REGEN"}.get(lingua, "COSA FARE CON LA PIOGGIA")

    content = {
        "it": """Quando piove, ecco le alternative:

🛍️ <b>Shopping</b>
• Valecenter (Marcon) - 30 min
• Outlet Noventa di Piave - 25 min

🎭 <b>Cultura</b>
• Musei di Venezia
• Acquario di Jesolo
• Ca' Rezzonico, Palazzo Ducale

💆 <b>Relax</b>
• SPA nei campeggi
• Terme di Bibione - 40 min
• Piscine coperte

🍽️ <b>Gastronomia</b>
• Cantine del Veneto
• Tour enogastronomici
• Ristoranti tipici""",
        "en": """When it rains, here are the alternatives:

🛍️ <b>Shopping</b>
• Valecenter (Marcon) - 30 min
• Noventa di Piave Outlet - 25 min

🎭 <b>Culture</b>
• Venice Museums
• Jesolo Aquarium
• Ca' Rezzonico, Doge's Palace

💆 <b>Relax</b>
• Campsite SPAs
• Bibione Thermal Baths - 40 min
• Indoor pools

🍽️ <b>Gastronomy</b>
• Veneto wineries
• Food and wine tours
• Traditional restaurants""",
        "de": """Bei Regen gibt es folgende Alternativen:

🛍️ <b>Shopping</b>
• Valecenter (Marcon) - 30 min
• Outlet Noventa di Piave - 25 min

🎭 <b>Kultur</b>
• Museen von Venedig
• Aquarium Jesolo
• Ca' Rezzonico, Dogenpalast

💆 <b>Entspannung</b>
• SPAs auf Campingplätzen
• Thermen von Bibione - 40 min
• Hallenbäder

🍽️ <b>Gastronomie</b>
• Weinkeller des Veneto
• Wein- und Gourmettouren
• Traditionelle Restaurants"""
    }

    text = f"🌧️ <b>{header}</b>\n\n{content.get(lingua, content['it'])}"

    btn_indietro = {"it": "◀️ Indietro", "en": "◀️ Back", "de": "◀️ Zurück"}.get(lingua, "◀️ Indietro")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(btn_indietro, callback_data="menu_idee")]
    ])

    if query:
        await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML")


# ============================================================
# SEZIONE LAGUNA (nuova)
# ============================================================

LAGUNA_DATA = {
    "lio_piccolo": {
        "nome": {"it": "Lio Piccolo", "en": "Lio Piccolo", "de": "Lio Piccolo"},
        "desc": {
            "it": "Antico borgo di pescatori, uno dei luoghi più suggestivi della laguna. Chiesa del '700 e atmosfera fuori dal tempo.",
            "en": "Ancient fishing village, one of the most evocative places in the lagoon. 18th century church and timeless atmosphere.",
            "de": "Altes Fischerdorf, einer der eindrucksvollsten Orte der Lagune. Kirche aus dem 18. Jahrhundert und zeitlose Atmosphäre."
        },
        "come": {
            "it": "In bici da Treporti: 20 min\nIn auto: 10 min, parcheggio gratuito",
            "en": "By bike from Treporti: 20 min\nBy car: 10 min, free parking",
            "de": "Mit dem Fahrrad ab Treporti: 20 min\nMit dem Auto: 10 min, kostenloser Parkplatz"
        },
        "maps": "https://maps.google.com/?q=45.4700,12.4100"
    },
    "saccagnana": {
        "nome": {"it": "Saccagnana", "en": "Saccagnana", "de": "Saccagnana"},
        "desc": {
            "it": "Piccola frazione lagunare con vista mozzafiato sulla laguna. Ideale per birdwatching e foto al tramonto.",
            "en": "Small lagoon hamlet with breathtaking views. Ideal for birdwatching and sunset photos.",
            "de": "Kleiner Lagunenweiler mit atemberaubender Aussicht. Ideal für Vogelbeobachtung und Sonnenuntergangsfotos."
        },
        "come": {
            "it": "In bici da Treporti: 15 min\nA piedi: 35 min",
            "en": "By bike from Treporti: 15 min\nOn foot: 35 min",
            "de": "Mit dem Fahrrad ab Treporti: 15 min\nZu Fuß: 35 min"
        },
        "maps": "https://maps.google.com/?q=45.4600,12.4000"
    },
    "mesole": {
        "nome": {"it": "Mesole", "en": "Mesole", "de": "Mesole"},
        "desc": {
            "it": "Zona agricola e lagunare, perfetta per scoprire la campagna veneta e i prodotti locali.",
            "en": "Agricultural and lagoon area, perfect for discovering the Venetian countryside and local products.",
            "de": "Landwirtschafts- und Lagunengebiet, perfekt um die venezianische Landschaft und lokale Produkte zu entdecken."
        },
        "come": {
            "it": "In bici da Cavallino: 25 min\nIn auto: 15 min",
            "en": "By bike from Cavallino: 25 min\nBy car: 15 min",
            "de": "Mit dem Fahrrad ab Cavallino: 25 min\nMit dem Auto: 15 min"
        },
        "maps": "https://maps.google.com/?q=45.5000,12.5200"
    }
}


async def handle_idee_laguna(context, chat_id: int, lingua: str, query=None):
    """Menu laguna con 3 luoghi."""
    if query:
        await query.answer()

    header = {"it": "LAGUNA", "en": "LAGOON", "de": "LAGUNE"}.get(lingua, "LAGUNA")
    subtitle = {"it": "Scopri i borghi della laguna:", "en": "Discover the lagoon villages:", "de": "Entdecke die Lagunendörfer:"}.get(lingua, "Scopri i borghi della laguna:")

    text = f"🌿 <b>{header}</b>\n\n{subtitle}"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌿 Lio Piccolo", callback_data="idee_laguna_lio_piccolo")],
        [InlineKeyboardButton("🌿 Saccagnana", callback_data="idee_laguna_saccagnana")],
        [InlineKeyboardButton("🌿 Mesole", callback_data="idee_laguna_mesole")],
        [InlineKeyboardButton("◀️ Indietro", callback_data="menu_idee")]
    ])

    if query:
        await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML")


async def handle_idee_laguna_dettaglio(context, chat_id: int, lingua: str, query, luogo_id: str):
    """Dettaglio singolo luogo laguna."""
    if query:
        await query.answer()

    luogo = LAGUNA_DATA.get(luogo_id)
    if not luogo:
        await handle_idee_laguna(context, chat_id, lingua, query)
        return

    nome = luogo["nome"].get(lingua, luogo["nome"]["it"])
    desc = luogo["desc"].get(lingua, luogo["desc"]["it"])
    come = luogo["come"].get(lingua, luogo["come"]["it"])
    maps_url = luogo["maps"]

    come_label = {"it": "Come arrivare", "en": "How to get there", "de": "Anfahrt"}.get(lingua, "Come arrivare")

    text = f"🌿 <b>{nome}</b>\n\n"
    text += f"{desc}\n\n"
    text += f"🚴 <b>{come_label}:</b>\n{come}"

    btn_naviga = {"it": "📍 Naviga", "en": "📍 Navigate", "de": "📍 Navigieren"}.get(lingua, "📍 Naviga")
    btn_indietro = {"it": "◀️ Indietro", "en": "◀️ Back", "de": "◀️ Zurück"}.get(lingua, "◀️ Indietro")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(btn_naviga, url=maps_url)],
        [InlineKeyboardButton(btn_indietro, callback_data="idee_laguna")]
    ])

    if query:
        await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML")


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
        [InlineKeyboardButton("💡 Altre idee", callback_data="menu_idee")],
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
        [InlineKeyboardButton("💡 Altre idee", callback_data="menu_idee")],
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
    Menu principale Fortini: 🏰 Fortini, 🚴 Percorsi, ◀️ Indietro
    """
    if query:
        await query.answer()

    header = {
        "it": "🏰 <b>Fortini di Cavallino-Treporti</b>",
        "en": "🏰 <b>Forts of Cavallino-Treporti</b>",
        "de": "🏰 <b>Festungen von Cavallino-Treporti</b>"
    }

    intro = {
        "it": "Sistema difensivo storico con 11 fortificazioni dalla Serenissima alla Grande Guerra.",
        "en": "Historic defense system with 11 fortifications from the Serenissima to the Great War.",
        "de": "Historisches Verteidigungssystem mit 11 Festungen von der Serenissima bis zum Ersten Weltkrieg."
    }

    text = f"{header.get(lingua, header['it'])}\n\n{intro.get(lingua, intro['it'])}"

    btn_fortini = {"it": "🏰 Esplora per zona", "en": "🏰 Explore by area", "de": "🏰 Nach Gebiet erkunden"}
    btn_percorsi = {"it": "🚴 Percorsi", "en": "🚴 Routes", "de": "🚴 Routen"}
    btn_back = {"it": "◀️ Menu", "en": "◀️ Menu", "de": "◀️ Menü"}

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(btn_fortini.get(lingua, btn_fortini["it"]), callback_data="fort_zone")],
        [InlineKeyboardButton(btn_percorsi.get(lingua, btn_percorsi["it"]), callback_data="fort_percorsi")],
        [InlineKeyboardButton(btn_back.get(lingua, btn_back["it"]), callback_data="menu_back")]
    ])

    if query:
        await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML")


# Zone per i fortini (8 zone)
FORTINI_ZONE = [
    "Cavallino", "Ca' di Valle", "Ca' Ballarin", "Ca' Pasquali",
    "Ca' Vio", "Ca' Savio", "Treporti", "Punta Sabbioni"
]


async def handle_fortini_zone(context, chat_id: int, lingua: str, query=None):
    """
    Lista delle 8 zone con fortini.
    """
    if query:
        await query.answer()

    header = {
        "it": "🏰 <b>Scegli la zona</b>",
        "en": "🏰 <b>Choose the area</b>",
        "de": "🏰 <b>Wählen Sie das Gebiet</b>"
    }

    text = header.get(lingua, header["it"])

    buttons = []
    for zona in FORTINI_ZONE:
        # Creiamo callback-safe key (rimuoviamo apostrofi e spazi)
        zona_key = zona.lower().replace("'", "").replace(" ", "_")
        buttons.append([InlineKeyboardButton(f"📍 {zona}", callback_data=f"fort_zona_{zona_key}")])

    btn_back = {"it": "◀️ Fortini", "en": "◀️ Forts", "de": "◀️ Festungen"}
    buttons.append([InlineKeyboardButton(btn_back.get(lingua, btn_back["it"]), callback_data="menu_fortini")])

    keyboard = InlineKeyboardMarkup(buttons)

    if query:
        await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML")


def _zona_key_to_nome(zona_key: str) -> str:
    """Converte zona_key in nome zona originale."""
    mapping = {
        "cavallino": "Cavallino",
        "ca_di_valle": "Ca' di Valle",
        "ca_ballarin": "Ca' Ballarin",
        "ca_pasquali": "Ca' Pasquali",
        "ca_vio": "Ca' Vio",
        "ca_savio": "Ca' Savio",
        "treporti": "Treporti",
        "punta_sabbioni": "Punta Sabbioni"
    }
    return mapping.get(zona_key, zona_key)


async def handle_fortini_lista(context, chat_id: int, lingua: str, query, zona_key: str):
    """
    Lista fortini di una zona specifica.
    """
    if query:
        await query.answer()

    zona_nome = _zona_key_to_nome(zona_key)
    fortini = db.get_fortini_by_zona(zona_nome)

    header = {
        "it": f"🏰 <b>Fortini a {zona_nome}</b>",
        "en": f"🏰 <b>Forts in {zona_nome}</b>",
        "de": f"🏰 <b>Festungen in {zona_nome}</b>"
    }

    if not fortini:
        no_fortini = {
            "it": "Nessun fortino in questa zona.",
            "en": "No forts in this area.",
            "de": "Keine Festungen in diesem Gebiet."
        }
        text = f"{header.get(lingua, header['it'])}\n\n{no_fortini.get(lingua, no_fortini['it'])}"
    else:
        text = header.get(lingua, header["it"])

    buttons = []
    for fortino in fortini:
        nome = fortino.get("nome", "Fortino")
        fortino_id = fortino.get("id")
        visitabile = fortino.get("visitabile", False)
        emoji = "🏛️" if visitabile else "🏚️"
        buttons.append([InlineKeyboardButton(f"{emoji} {nome}", callback_data=f"fort_detail_{fortino_id}")])

    btn_back = {"it": "◀️ Zone", "en": "◀️ Areas", "de": "◀️ Gebiete"}
    buttons.append([InlineKeyboardButton(btn_back.get(lingua, btn_back["it"]), callback_data="fort_zone")])

    keyboard = InlineKeyboardMarkup(buttons)

    if query:
        await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML")


async def handle_fortini_dettaglio(context, chat_id: int, lingua: str, query, fortino_id: str):
    """
    Scheda dettaglio singolo fortino con link Google Maps.
    """
    if query:
        await query.answer()

    fortino = db.get_fortino_by_id(fortino_id)

    if not fortino:
        error = {"it": "Fortino non trovato.", "en": "Fort not found.", "de": "Festung nicht gefunden."}
        text = f"⚠️ {error.get(lingua, error['it'])}"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Zone", callback_data="fort_zone")]
        ])
        if query:
            await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="HTML")
        return

    nome = fortino.get("nome", "Fortino")
    tipo = fortino.get("tipo", "")
    zona = fortino.get("zona", "")
    lat = fortino.get("lat")
    lng = fortino.get("lng")
    visitabile = fortino.get("visitabile", False)
    descrizione = fortino.get("descrizione_breve", "")
    come_arrivare = fortino.get("come_arrivare_breve", "")

    # Header
    emoji_visit = "✅" if visitabile else "❌"
    visit_label = {
        "it": "Visitabile" if visitabile else "Non visitabile",
        "en": "Visitable" if visitabile else "Not visitable",
        "de": "Besuchbar" if visitabile else "Nicht besuchbar"
    }

    text = f"🏰 <b>{nome}</b>\n"
    if tipo:
        text += f"📋 {tipo}\n"
    text += f"📍 {zona}\n"
    text += f"{emoji_visit} {visit_label.get(lingua, visit_label['it'])}\n\n"

    if descrizione:
        text += f"{descrizione}\n\n"

    if come_arrivare:
        arrive_label = {"it": "🚶 Come arrivare:", "en": "🚶 How to get there:", "de": "🚶 Anfahrt:"}
        text += f"{arrive_label.get(lingua, arrive_label['it'])}\n{come_arrivare}"

    buttons = []

    # Link Google Maps
    if lat and lng:
        maps_label = {"it": "📍 Portami qui", "en": "📍 Take me there", "de": "📍 Bring mich hin"}
        maps_url = f"https://www.google.com/maps/dir/?api=1&destination={lat},{lng}"
        buttons.append([InlineKeyboardButton(maps_label.get(lingua, maps_label["it"]), url=maps_url)])

    # Bottone indietro alla zona
    zona_key = zona.lower().replace("'", "").replace(" ", "_")
    btn_back = {"it": f"◀️ {zona}", "en": f"◀️ {zona}", "de": f"◀️ {zona}"}
    buttons.append([InlineKeyboardButton(btn_back.get(lingua, btn_back["it"]), callback_data=f"fort_zona_{zona_key}")])

    keyboard = InlineKeyboardMarkup(buttons)

    if query:
        await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML")


async def handle_percorsi_lista(context, chat_id: int, lingua: str, query=None):
    """
    Lista dei 3 percorsi fortini.
    """
    if query:
        await query.answer()

    percorsi = db.get_percorsi_fortini_attivi()

    header = {
        "it": "🚴 <b>Percorsi Fortini</b>",
        "en": "🚴 <b>Fort Routes</b>",
        "de": "🚴 <b>Festungsrouten</b>"
    }

    text = header.get(lingua, header["it"])

    buttons = []
    for percorso in percorsi:
        nome = percorso.get("nome", "Percorso")
        percorso_id = percorso.get("id")
        mezzo = percorso.get("mezzo", "bici")
        emoji = "🚴" if mezzo == "bici" else "🚶" if mezzo == "piedi" else "🚗"
        lunghezza = percorso.get("lunghezza_km", 0)
        buttons.append([InlineKeyboardButton(f"{emoji} {nome} ({lunghezza} km)", callback_data=f"fort_percorso_{percorso_id}")])

    btn_back = {"it": "◀️ Fortini", "en": "◀️ Forts", "de": "◀️ Festungen"}
    buttons.append([InlineKeyboardButton(btn_back.get(lingua, btn_back["it"]), callback_data="menu_fortini")])

    keyboard = InlineKeyboardMarkup(buttons)

    if query:
        await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML")


async def handle_percorsi_dettaglio(context, chat_id: int, lingua: str, query, percorso_id: str):
    """
    Scheda dettaglio percorso con km, durata e fortini inclusi.
    """
    if query:
        await query.answer()

    percorso = db.get_percorso_by_id(percorso_id)

    if not percorso:
        error = {"it": "Percorso non trovato.", "en": "Route not found.", "de": "Route nicht gefunden."}
        text = f"⚠️ {error.get(lingua, error['it'])}"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Percorsi", callback_data="fort_percorsi")]
        ])
        if query:
            await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="HTML")
        return

    nome = percorso.get("nome", "Percorso")
    descrizione = percorso.get("descrizione_breve", "")
    mezzo = percorso.get("mezzo", "bici")
    lunghezza = percorso.get("lunghezza_km", 0)
    durata = percorso.get("durata_min", 0)
    panoramico = percorso.get("panoramico", False)

    emoji_mezzo = "🚴" if mezzo == "bici" else "🚶" if mezzo == "piedi" else "🚗"
    mezzo_label = {
        "bici": {"it": "In bici", "en": "By bike", "de": "Mit dem Fahrrad"},
        "piedi": {"it": "A piedi", "en": "On foot", "de": "Zu Fuß"},
        "auto": {"it": "In auto", "en": "By car", "de": "Mit dem Auto"}
    }

    text = f"🚴 <b>{nome}</b>\n\n"
    text += f"{emoji_mezzo} {mezzo_label.get(mezzo, mezzo_label['bici']).get(lingua, mezzo_label['bici']['it'])}\n"
    text += f"📏 {lunghezza} km\n"

    # Durata in ore:minuti
    if durata >= 60:
        ore = durata // 60
        minuti = durata % 60
        durata_str = f"{ore}h {minuti}min" if minuti else f"{ore}h"
    else:
        durata_str = f"{durata} min"
    text += f"⏱️ {durata_str}\n"

    if panoramico:
        panoramico_label = {"it": "🌅 Percorso panoramico", "en": "🌅 Scenic route", "de": "🌅 Panoramaroute"}
        text += f"{panoramico_label.get(lingua, panoramico_label['it'])}\n"

    if descrizione:
        text += f"\n{descrizione}\n"

    # Fortini nel percorso
    fortini_percorso = db.get_fortini_in_percorso(percorso_id)
    if fortini_percorso:
        tappe_label = {"it": "📍 Tappe:", "en": "📍 Stops:", "de": "📍 Stationen:"}
        text += f"\n{tappe_label.get(lingua, tappe_label['it'])}\n"
        for i, fortino in enumerate(fortini_percorso, 1):
            text += f"{i}. {fortino.get('nome', 'Fortino')}\n"

    buttons = []

    # Bottoni per i singoli fortini
    for fortino in fortini_percorso[:3]:  # Max 3 per non ingombrare
        fortino_id = fortino.get("id")
        buttons.append([InlineKeyboardButton(f"🏰 {fortino.get('nome', '')}", callback_data=f"fort_detail_{fortino_id}")])

    btn_back = {"it": "◀️ Percorsi", "en": "◀️ Routes", "de": "◀️ Routen"}
    buttons.append([InlineKeyboardButton(btn_back.get(lingua, btn_back["it"]), callback_data="fort_percorsi")])

    keyboard = InlineKeyboardMarkup(buttons)

    if query:
        await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML")


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
        [InlineKeyboardButton("💡 Altre idee", callback_data="menu_idee")],
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
# SISTEMA EVENTI COMPLETO
# ============================================================

# Emoji per categorie eventi
CATEGORIA_EMOJI = {
    "mercato": "🛒",
    "sagra": "🍝",
    "musica": "🎵",
    "cultura": "🎭",
    "sport": "⚽",
    "famiglia": "👨‍👩‍👧‍👦",
    "altro": "🎪"
}

# Traduzioni categorie
CATEGORIA_LABELS = {
    "mercato": {"it": "Mercati", "en": "Markets", "de": "Märkte"},
    "sagra": {"it": "Sagre", "en": "Festivals", "de": "Feste"},
    "musica": {"it": "Musica", "en": "Music", "de": "Musik"},
    "cultura": {"it": "Cultura", "en": "Culture", "de": "Kultur"},
    "sport": {"it": "Sport", "en": "Sports", "de": "Sport"},
    "famiglia": {"it": "Famiglia", "en": "Family", "de": "Familie"}
}

EVENTI_PER_PAGINA = 5


def _get_periodo_date(periodo: str):
    """Calcola date inizio/fine per un periodo."""
    from datetime import date, timedelta

    oggi = date.today()

    if periodo == "oggi":
        return oggi.isoformat(), oggi.isoformat()
    elif periodo == "domani":
        domani = oggi + timedelta(days=1)
        return domani.isoformat(), domani.isoformat()
    elif periodo == "sett_0":  # Questa settimana
        # Da oggi a domenica
        giorni_a_domenica = 6 - oggi.weekday()
        fine_settimana = oggi + timedelta(days=giorni_a_domenica)
        return oggi.isoformat(), fine_settimana.isoformat()
    elif periodo == "sett_1":  # Prossima settimana
        # Lunedì prossimo a domenica prossima
        giorni_a_lunedi = 7 - oggi.weekday()
        lunedi_prossimo = oggi + timedelta(days=giorni_a_lunedi)
        domenica_prossima = lunedi_prossimo + timedelta(days=6)
        return lunedi_prossimo.isoformat(), domenica_prossima.isoformat()
    else:
        # Default: prossimi 7 giorni
        return oggi.isoformat(), (oggi + timedelta(days=7)).isoformat()


def _format_evento_lista(evento: dict, lingua: str, numero: int = None) -> str:
    """Formatta un evento per la lista con numero progressivo."""
    titolo = evento.get(f"titolo_{lingua}") or evento.get("titolo_it", "Evento")
    luogo = evento.get("luogo", "")

    if numero:
        line = f"<b>{numero}.</b> {titolo}"
    else:
        line = f"<b>{titolo}</b>"
    if luogo:
        line += f"\n   📍 {luogo}"
    return line


async def handle_eventi(context, chat_id: int, lingua: str, query=None):
    """
    HOME EVENTI - Mostra evento imperdibile e bottoni navigazione.
    Callback: evt_home o menu_eventi
    """
    from datetime import date, timedelta

    if query:
        await query.answer()

    oggi = date.today()

    # Titolo
    titoli = {
        "it": "🎪 <b>Eventi a Cavallino-Treporti</b>",
        "en": "🎪 <b>Events in Cavallino-Treporti</b>",
        "de": "🎪 <b>Veranstaltungen in Cavallino-Treporti</b>"
    }

    text = titoli.get(lingua, titoli["it"]) + "\n\n"

    # Evento imperdibile del giorno
    imperdibile = db.get_evento_imperdibile()
    if imperdibile:
        titolo_imp = imperdibile.get(f"titolo_{lingua}") or imperdibile.get("titolo_it", "")
        luogo_imp = imperdibile.get("luogo", "")
        orario_imp = imperdibile.get("orario", "")

        imp_labels = {"it": "DA NON PERDERE OGGI", "en": "DON'T MISS TODAY", "de": "HEUTE NICHT VERPASSEN"}
        text += f"⭐ <b>{imp_labels.get(lingua, imp_labels['it'])}</b>\n"
        text += f"🎪 {titolo_imp}\n"
        if orario_imp:
            text += f"🕐 {orario_imp}"
        if luogo_imp:
            text += f" • 📍 {luogo_imp}"
        text += "\n\n"

    # Conta eventi per periodo
    oggi_count = db.get_eventi_count_periodo(oggi.isoformat(), oggi.isoformat())
    domani_count = db.get_eventi_count_periodo((oggi + timedelta(days=1)).isoformat(), (oggi + timedelta(days=1)).isoformat())

    # Info rapida
    info_labels = {"it": "Cosa vuoi vedere?", "en": "What would you like to see?", "de": "Was möchten Sie sehen?"}
    text += f"<i>{info_labels.get(lingua, info_labels['it'])}</i>"

    # Bottoni periodo
    btn_oggi = {"it": f"📅 Oggi ({oggi_count})", "en": f"📅 Today ({oggi_count})", "de": f"📅 Heute ({oggi_count})"}
    btn_domani = {"it": f"📅 Domani ({domani_count})", "en": f"📅 Tomorrow ({domani_count})", "de": f"📅 Morgen ({domani_count})"}
    btn_sett0 = {"it": "📅 Questa settimana", "en": "📅 This week", "de": "📅 Diese Woche"}
    btn_sett1 = {"it": "📅 Prossima settimana", "en": "📅 Next week", "de": "📅 Nächste Woche"}
    btn_cal = {"it": "🗓️ Calendario", "en": "🗓️ Calendar", "de": "🗓️ Kalender"}
    btn_cat = {"it": "🏷️ Categorie", "en": "🏷️ Categories", "de": "🏷️ Kategorien"}
    btn_back = {"it": "◀️ Menu", "en": "◀️ Menu", "de": "◀️ Menü"}

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(btn_oggi.get(lingua, btn_oggi["it"]), callback_data="evt_oggi"),
            InlineKeyboardButton(btn_domani.get(lingua, btn_domani["it"]), callback_data="evt_domani")
        ],
        [
            InlineKeyboardButton(btn_sett0.get(lingua, btn_sett0["it"]), callback_data="evt_sett_0"),
            InlineKeyboardButton(btn_sett1.get(lingua, btn_sett1["it"]), callback_data="evt_sett_1")
        ],
        [
            InlineKeyboardButton(btn_cal.get(lingua, btn_cal["it"]), callback_data="evt_cal"),
            InlineKeyboardButton(btn_cat.get(lingua, btn_cat["it"]), callback_data="evt_categoria")
        ],
        [InlineKeyboardButton(btn_back.get(lingua, btn_back["it"]), callback_data="menu_home")]
    ])

    if query:
        await edit_message_safe(query, text=text, reply_markup=keyboard)
    else:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML")


async def handle_eventi_lista(context, chat_id: int, lingua: str, query, periodo: str, pagina: int = 0, categoria: str = None):
    """
    LISTA EVENTI con paginazione.
    Callback: evt_{periodo}_p{pagina} o evt_cat_{categoria}_p{pagina}
    """
    from datetime import date, timedelta

    if query:
        await query.answer()

    oggi = date.today()
    data_inizio, data_fine = _get_periodo_date(periodo)

    # Titoli periodo
    titoli_periodo = {
        "oggi": {"it": "Eventi di Oggi", "en": "Today's Events", "de": "Heute"},
        "domani": {"it": "Eventi di Domani", "en": "Tomorrow's Events", "de": "Morgen"},
        "sett_0": {"it": "Questa Settimana", "en": "This Week", "de": "Diese Woche"},
        "sett_1": {"it": "Prossima Settimana", "en": "Next Week", "de": "Nächste Woche"}
    }

    titolo = titoli_periodo.get(periodo, {}).get(lingua, "Eventi")
    if categoria:
        cat_label = CATEGORIA_LABELS.get(categoria, {}).get(lingua, categoria.title())
        titolo = f"{CATEGORIA_EMOJI.get(categoria, '🎪')} {cat_label}"

    # Query eventi
    offset = pagina * EVENTI_PER_PAGINA
    eventi = db.get_eventi_periodo(data_inizio, data_fine, limit=EVENTI_PER_PAGINA, offset=offset, categoria=categoria)
    totale = db.get_eventi_count_periodo(data_inizio, data_fine, categoria=categoria)
    totale_pagine = (totale + EVENTI_PER_PAGINA - 1) // EVENTI_PER_PAGINA

    text = f"🎪 <b>{titolo}</b>\n"
    if totale > 0:
        text += f"<i>{totale} eventi</i>\n\n"
    else:
        nessuno = {"it": "Nessun evento in questo periodo.", "en": "No events in this period.", "de": "Keine Veranstaltungen in diesem Zeitraum."}
        text += nessuno.get(lingua, nessuno["it"])

    # Lista eventi con numeri progressivi
    numero_emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    for i, evento in enumerate(eventi):
        text += _format_evento_lista(evento, lingua, numero=i+1) + "\n\n"

    buttons = []

    # Riga 1: Bottoni numerati per dettagli
    if eventi:
        num_buttons = []
        for i, evento in enumerate(eventi):
            evento_id = evento.get("id")
            num_buttons.append(InlineKeyboardButton(numero_emoji[i], callback_data=f"evt_detail_{evento_id}"))
        buttons.append(num_buttons)

    # Riga 2: Paginazione
    if totale_pagine > 1:
        nav_buttons = []
        if pagina > 0:
            if categoria:
                nav_buttons.append(InlineKeyboardButton("◀️", callback_data=f"evt_cat_{categoria}_p{pagina-1}"))
            else:
                nav_buttons.append(InlineKeyboardButton("◀️", callback_data=f"evt_list_{periodo}_p{pagina-1}"))

        nav_buttons.append(InlineKeyboardButton(f"{pagina+1}/{totale_pagine}", callback_data="noop"))

        if pagina < totale_pagine - 1:
            if categoria:
                nav_buttons.append(InlineKeyboardButton("▶️", callback_data=f"evt_cat_{categoria}_p{pagina+1}"))
            else:
                nav_buttons.append(InlineKeyboardButton("▶️", callback_data=f"evt_list_{periodo}_p{pagina+1}"))

        buttons.append(nav_buttons)

    # Riga 3: Bottone indietro
    btn_back = {"it": "◀️ Eventi", "en": "◀️ Events", "de": "◀️ Events"}
    buttons.append([InlineKeyboardButton(btn_back.get(lingua, btn_back["it"]), callback_data="evt_home")])

    keyboard = InlineKeyboardMarkup(buttons)
    await edit_message_safe(query, text=text, reply_markup=keyboard)


async def handle_eventi_categorie(context, chat_id: int, lingua: str, query):
    """
    CATEGORIE - Mostra categorie con conteggio.
    Callback: evt_categoria
    """
    if query:
        await query.answer()

    titoli = {"it": "🏷️ <b>Categorie Eventi</b>", "en": "🏷️ <b>Event Categories</b>", "de": "🏷️ <b>Veranstaltungskategorien</b>"}
    sottotitoli = {"it": "Scegli una categoria:", "en": "Choose a category:", "de": "Wählen Sie eine Kategorie:"}

    text = titoli.get(lingua, titoli["it"]) + "\n"
    text += f"<i>{sottotitoli.get(lingua, sottotitoli['it'])}</i>\n"

    categorie = db.get_categorie_eventi()

    buttons = []
    for cat_info in categorie:
        cat = cat_info["categoria"]
        count = cat_info["count"]
        emoji = CATEGORIA_EMOJI.get(cat, "🎪")
        label = CATEGORIA_LABELS.get(cat, {}).get(lingua, cat.title())
        buttons.append([InlineKeyboardButton(f"{emoji} {label} ({count})", callback_data=f"evt_cat_{cat}_p0")])

    if not buttons:
        nessuna = {"it": "\nNessuna categoria con eventi attivi.", "en": "\nNo categories with active events.", "de": "\nKeine Kategorien mit aktiven Veranstaltungen."}
        text += nessuna.get(lingua, nessuna["it"])

    btn_back = {"it": "◀️ Eventi", "en": "◀️ Events", "de": "◀️ Events"}
    buttons.append([InlineKeyboardButton(btn_back.get(lingua, btn_back["it"]), callback_data="evt_home")])

    keyboard = InlineKeyboardMarkup(buttons)
    await edit_message_safe(query, text=text, reply_markup=keyboard)


async def handle_evento_dettaglio(context, chat_id: int, lingua: str, query, evento_id: int):
    """
    DETTAGLIO EVENTO - Mostra info complete.
    Callback: evt_detail_{id}
    """
    import urllib.parse

    if query:
        await query.answer()

    evento = db.get_evento_by_id(evento_id)
    if not evento:
        error = {"it": "⚠️ Evento non trovato.", "en": "⚠️ Event not found.", "de": "⚠️ Veranstaltung nicht gefunden."}
        await edit_message_safe(query, text=error.get(lingua, error["it"]))
        return

    titolo = evento.get(f"titolo_{lingua}") or evento.get("titolo_it", "Evento")
    descrizione = evento.get(f"descrizione_{lingua}") or evento.get("descrizione_it", "")
    data_inizio = evento.get("data_inizio", "")
    data_fine = evento.get("data_fine", "")
    luogo = evento.get("luogo", "")
    indirizzo = evento.get("indirizzo", "")
    categoria = evento.get("categoria", "altro")

    # Formatta data
    from datetime import date
    mesi_short = {"it": ["Gen", "Feb", "Mar", "Apr", "Mag", "Giu", "Lug", "Ago", "Set", "Ott", "Nov", "Dic"],
                  "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
                  "de": ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]}
    giorni = {"it": ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"],
              "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
              "de": ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]}

    try:
        d_inizio = date.fromisoformat(data_inizio)
        d_fine = date.fromisoformat(data_fine) if data_fine else d_inizio

        if d_inizio == d_fine:
            giorno_nome = giorni.get(lingua, giorni["it"])[d_inizio.weekday()]
            mese_nome = mesi_short.get(lingua, mesi_short["it"])[d_inizio.month - 1]
            data_str = f"{giorno_nome} {d_inizio.day} {mese_nome} {d_inizio.year}"
        else:
            mese_inizio = mesi_short.get(lingua, mesi_short["it"])[d_inizio.month - 1]
            mese_fine = mesi_short.get(lingua, mesi_short["it"])[d_fine.month - 1]
            dal = {"it": "Dal", "en": "From", "de": "Vom"}
            al = {"it": "al", "en": "to", "de": "bis"}
            data_str = f"{dal.get(lingua, dal['it'])} {d_inizio.day} {mese_inizio} {d_inizio.year} {al.get(lingua, al['it'])} {d_fine.day} {mese_fine} {d_fine.year}"
    except:
        data_str = data_inizio

    emoji = CATEGORIA_EMOJI.get(categoria, "🎪")

    text = f"{emoji} <b>{titolo}</b>\n\n"
    text += f"📅 {data_str}\n"
    if luogo:
        text += f"📍 {luogo}\n"

    # Costruisci URL evento se disponibile
    evento_url = None
    if evento.get('url'):
        evento_url = COMUNE_BASE + evento.get('url')

    if descrizione:
        text += f"\n{descrizione}\n"
    else:
        info_fallback = {"it": "ℹ️ Dettagli completi sul sito del Comune",
                         "en": "ℹ️ Full details on the Municipality website",
                         "de": "ℹ️ Vollständige Details auf der Website der Gemeinde"}
        text += f"\n{info_fallback.get(lingua, info_fallback['it'])}\n"
        if evento_url:
            text += f"🔗 {evento_url}\n"

    text += "\n🦭 <i>SLAPPY</i>"

    # Bottoni su una riga: [Maps] [Condividi]
    buttons = []
    row1 = []

    if indirizzo or luogo:
        maps_query = urllib.parse.quote(indirizzo or luogo)
        maps_url = f"https://www.google.com/maps/search/?api=1&query={maps_query}"
        row1.append(InlineKeyboardButton("🗺️ Maps", url=maps_url))

    share_text = urllib.parse.quote(f"🎪 {titolo}\n📅 {data_str}\n📍 {luogo or ''}")
    share_url = f"https://t.me/share/url?url=&text={share_text}"
    row1.append(InlineKeyboardButton("📤 Condividi", url=share_url))

    if row1:
        buttons.append(row1)

    # Bottone sito Comune (solo se URL esiste)
    if evento_url:
        buttons.append([InlineKeyboardButton("🔗 Sito Comune", url=evento_url)])

    # Indietro
    btn_back = {"it": "◀️ Eventi", "en": "◀️ Events", "de": "◀️ Events"}
    buttons.append([InlineKeyboardButton(btn_back.get(lingua, btn_back["it"]), callback_data="evt_home")])

    keyboard = InlineKeyboardMarkup(buttons)
    await edit_message_safe(query, text=text, reply_markup=keyboard)


async def handle_eventi_calendario(context, chat_id: int, lingua: str, query, anno: int = None, mese: int = None):
    """
    CALENDARIO MESE - Griglia con giorni che hanno eventi.
    Callback: evt_cal o evt_cal_{anno}_{mese}
    """
    from datetime import date
    import calendar

    if query:
        await query.answer()

    oggi = date.today()
    if anno is None:
        anno = oggi.year
    if mese is None:
        mese = oggi.month

    # Nomi mesi
    mesi_nomi = {
        "it": ["Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"],
        "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
        "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
    }

    nome_mese = mesi_nomi.get(lingua, mesi_nomi["it"])[mese - 1]

    titoli = {"it": "🗓️ <b>Calendario Eventi</b>", "en": "🗓️ <b>Events Calendar</b>", "de": "🗓️ <b>Veranstaltungskalender</b>"}

    text = titoli.get(lingua, titoli["it"]) + "\n"
    text += f"<b>{nome_mese} {anno}</b>\n\n"

    # Giorni con eventi
    giorni_eventi = db.get_giorni_con_eventi(anno, mese)

    # Header giorni settimana
    giorni_header = {"it": "L  M  M  G  V  S  D", "en": "M  T  W  T  F  S  S", "de": "M  D  M  D  F  S  S"}
    text += f"<code>{giorni_header.get(lingua, giorni_header['it'])}</code>\n"

    # Griglia calendario
    cal = calendar.monthcalendar(anno, mese)
    for settimana in cal:
        riga = ""
        for giorno in settimana:
            if giorno == 0:
                riga += "   "
            elif giorno in giorni_eventi:
                riga += f"<b>{giorno:2d}</b> "
            elif date(anno, mese, giorno) == oggi:
                riga += f"<u>{giorno:2d}</u> "
            else:
                riga += f"{giorno:2d} "
        text += f"<code>{riga}</code>\n"

    legend = {"it": "\n<b>Grassetto</b> = giorni con eventi", "en": "\n<b>Bold</b> = days with events", "de": "\n<b>Fett</b> = Tage mit Veranstaltungen"}
    text += legend.get(lingua, legend["it"])

    # Bottoni giorni con eventi (max 8)
    buttons = []
    row = []
    for giorno in giorni_eventi[:8]:
        row.append(InlineKeyboardButton(f"{giorno}", callback_data=f"evt_cal_giorno_{anno}_{mese}_{giorno}"))
        if len(row) == 4:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # Navigazione mesi
    nav = []
    # Mese precedente
    if mese == 1:
        prev_anno, prev_mese = anno - 1, 12
    else:
        prev_anno, prev_mese = anno, mese - 1
    nav.append(InlineKeyboardButton("◀️", callback_data=f"evt_cal_{prev_anno}_{prev_mese}"))

    # Mese successivo
    if mese == 12:
        next_anno, next_mese = anno + 1, 1
    else:
        next_anno, next_mese = anno, mese + 1
    nav.append(InlineKeyboardButton("▶️", callback_data=f"evt_cal_{next_anno}_{next_mese}"))
    buttons.append(nav)

    # Indietro
    btn_back = {"it": "◀️ Eventi", "en": "◀️ Events", "de": "◀️ Events"}
    buttons.append([InlineKeyboardButton(btn_back.get(lingua, btn_back["it"]), callback_data="evt_home")])

    keyboard = InlineKeyboardMarkup(buttons)
    await edit_message_safe(query, text=text, reply_markup=keyboard)


async def handle_eventi_giorno(context, chat_id: int, lingua: str, query, anno: int, mese: int, giorno: int):
    """
    EVENTI DI UN GIORNO SPECIFICO dal calendario.
    Callback: evt_cal_giorno_{anno}_{mese}_{giorno}
    """
    from datetime import date

    if query:
        await query.answer()

    data = date(anno, mese, giorno).isoformat()
    eventi = db.get_eventi_giorno(data)

    # Formatta data
    giorni_nomi = {"it": ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"],
                   "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
                   "de": ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]}
    mesi_nomi = {"it": ["Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"],
                 "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
                 "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]}

    d = date(anno, mese, giorno)
    giorno_nome = giorni_nomi.get(lingua, giorni_nomi["it"])[d.weekday()]
    mese_nome = mesi_nomi.get(lingua, mesi_nomi["it"])[mese - 1]

    text = f"🗓️ <b>{giorno_nome} {giorno} {mese_nome}</b>\n\n"

    buttons = []

    if not eventi:
        nessuno = {"it": "Nessun evento in questo giorno.", "en": "No events on this day.", "de": "Keine Veranstaltungen an diesem Tag."}
        text += nessuno.get(lingua, nessuno["it"])
    else:
        text += f"<i>{len(eventi)} eventi</i>\n\n"
        numero_emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        for i, evento in enumerate(eventi[:10]):
            text += _format_evento_lista(evento, lingua, numero=i+1) + "\n\n"

        # Riga 1: Bottoni numerati per dettagli
        num_buttons = []
        for i, evento in enumerate(eventi[:10]):
            evento_id = evento.get("id")
            num_buttons.append(InlineKeyboardButton(numero_emoji[i], callback_data=f"evt_detail_{evento_id}"))
        buttons.append(num_buttons)

    # Bottone indietro al calendario
    btn_back = {"it": "◀️ Calendario", "en": "◀️ Calendar", "de": "◀️ Kalender"}
    buttons.append([InlineKeyboardButton(btn_back.get(lingua, btn_back["it"]), callback_data=f"evt_cal_{anno}_{mese}")])

    keyboard = InlineKeyboardMarkup(buttons)
    await edit_message_safe(query, text=text, reply_markup=keyboard)


async def handle_trasporti(context, chat_id: int, lingua: str, query=None):
    """
    HOME TRASPORTI - Menu principale con 4 opzioni.
    Callback: tras_home o menu_trasporti
    """
    if query:
        await query.answer()

    text = TRASPORTI_LABELS["home_title"].get(lingua, TRASPORTI_LABELS["home_title"]["it"]) + "\n\n"

    intro = {
        "it": "Come vuoi muoverti a Cavallino-Treporti?",
        "en": "How would you like to get around Cavallino-Treporti?",
        "de": "Wie möchten Sie sich in Cavallino-Treporti fortbewegen?"
    }
    text += f"<i>{intro.get(lingua, intro['it'])}</i>"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(TRASPORTI_LABELS["arrivo"].get(lingua, TRASPORTI_LABELS["arrivo"]["it"]), callback_data="tras_arrivo")],
        [InlineKeyboardButton(TRASPORTI_LABELS["altra_zona"].get(lingua, TRASPORTI_LABELS["altra_zona"]["it"]), callback_data="tras_frazione")],
        [InlineKeyboardButton(TRASPORTI_LABELS["prezzi"].get(lingua, TRASPORTI_LABELS["prezzi"]["it"]), callback_data="tras_prezzi")],
        [InlineKeyboardButton(TRASPORTI_LABELS["back_menu"].get(lingua, TRASPORTI_LABELS["back_menu"]["it"]), callback_data="menu_home")]
    ])

    if query:
        await edit_message_safe(query, text=text, reply_markup=keyboard)
    else:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML")


async def handle_trasporti_arrivo(context, chat_id: int, lingua: str, query):
    """
    COME ARRIVO A... - Lista destinazioni organizzata per tipo.
    Callback: tras_arrivo
    """
    if query:
        await query.answer()

    titoli = {
        "it": "📍 <b>Raggiungi un luogo</b>",
        "en": "📍 <b>Reach a destination</b>",
        "de": "📍 <b>Ziel erreichen</b>"
    }

    text = titoli.get(lingua, titoli["it"]) + "\n"

    # Sezione traghetti
    ferry_header = {"it": "🚢 <b>Traghetti ACTV</b>", "en": "🚢 <b>ACTV Ferries</b>", "de": "🚢 <b>ACTV Fähren</b>"}
    text += f"\n{ferry_header.get(lingua, ferry_header['it'])}\n"

    # Sezione bus
    bus_header = {"it": "🚌 <b>Bus ATVO</b>", "en": "🚌 <b>ATVO Buses</b>", "de": "🚌 <b>ATVO Busse</b>"}
    text += f"\n{bus_header.get(lingua, bus_header['it'])}\n"

    buttons = []

    # ===== TRAGHETTI ACTV (per destinazione) =====
    # Venezia San Marco
    buttons.append([InlineKeyboardButton("🏛️ Venezia San Marco", callback_data="tras_ferry_dest_venezia_sm")])

    # Venezia Fondamente Nove
    buttons.append([InlineKeyboardButton("🌉 Venezia F. Nove", callback_data="tras_ferry_dest_venezia_fn")])

    # Isole laguna
    buttons.append([InlineKeyboardButton("🏝️ Burano/Murano/Torcello", callback_data="tras_ferry_dest_isole")])

    # Lido
    buttons.append([InlineKeyboardButton("🎬 Lido di Venezia", callback_data="tras_ferry_dest_lido")])

    # Carica destinazioni per usarle sia per Venezia che per bus
    destinazioni = db.get_destinazioni_attive()
    dest_map = {d.get("codice"): d for d in destinazioni} if destinazioni else {}

    # Venezia journey planner (bus + traghetto)
    if "venezia" in dest_map:
        dest = dest_map["venezia"]
        dest_id = dest.get("id")
        buttons.append([InlineKeyboardButton("🏛️ Venezia (bus+traghetto)", callback_data=f"tras_dest_{dest_id}")])

    # ===== BUS ATVO =====
    if destinazioni:
        # Jesolo
        if "jesolo" in dest_map:
            dest = dest_map["jesolo"]
            dest_id = dest.get("id")
            buttons.append([InlineKeyboardButton("🏖️ Jesolo", callback_data=f"tras_dest_{dest_id}")])

        # Aeroporto
        if "aeroporto" in dest_map:
            dest = dest_map["aeroporto"]
            dest_id = dest.get("id")
            buttons.append([InlineKeyboardButton("✈️ Aeroporto Marco Polo", callback_data=f"tras_dest_{dest_id}")])

    # Spostamenti interni
    interno_label = {"it": "🏘️ Spostamenti interni", "en": "🏘️ Local transport", "de": "🏘️ Lokaler Verkehr"}
    buttons.append([InlineKeyboardButton(interno_label.get(lingua, interno_label["it"]), callback_data="tras_frazione")])

    # Indietro
    buttons.append([InlineKeyboardButton(TRASPORTI_LABELS["back_trasporti"].get(lingua, TRASPORTI_LABELS["back_trasporti"]["it"]), callback_data="tras_home")])

    keyboard = InlineKeyboardMarkup(buttons)
    await edit_message_safe(query, text=text, reply_markup=keyboard)


async def handle_trasporti_frazione(context, chat_id: int, lingua: str, query):
    """
    CAMBIA ZONA / FRAZIONE - Spostamenti interni tra frazioni di Cavallino-Treporti.
    Callback: tras_frazione
    """
    if query:
        await query.answer()

    titoli = {
        "it": "🏘️ <b>Cambia zona / frazione</b>\n\nDa dove parti?",
        "en": "🏘️ <b>Change area / district</b>\n\nWhere are you starting from?",
        "de": "🏘️ <b>Gebiet / Ortsteil wechseln</b>\n\nVon wo starten Sie?"
    }
    text = titoli.get(lingua, titoli["it"])

    zone = db.get_zone_attive()

    # Griglia 2x2 - tutte le 8 frazioni
    grid_order = [
        ["cavallino", "ca_di_valle"],
        ["ca_ballarin", "ca_pasquali"],
        ["ca_vio", "ca_savio"],
        ["treporti", "punta_sabbioni"]
    ]

    zone_map = {z.get("codice"): z for z in zone}

    buttons = []
    for row in grid_order:
        btn_row = []
        for codice in row:
            if codice in zone_map:
                zona = zone_map[codice]
                nome = zona.get(f"nome_{lingua}") or zona.get("nome_it", "")
                zona_id = zona.get("id")
                btn_row.append(InlineKeyboardButton(f"📍 {nome}", callback_data=f"tras_fraz_{zona_id}_0"))
        if btn_row:
            buttons.append(btn_row)

    buttons.append([InlineKeyboardButton(TRASPORTI_LABELS["back_trasporti"].get(lingua, TRASPORTI_LABELS["back_trasporti"]["it"]), callback_data="tras_home")])

    keyboard = InlineKeyboardMarkup(buttons)
    await edit_message_safe(query, text=text, reply_markup=keyboard)


async def handle_trasporti_frazione_percorso(context, chat_id: int, lingua: str, query, da_zona_id: int, a_zona_id: int = None):
    """
    PERCORSO TRA FRAZIONI - Selezione destinazione e linea bus.
    Callback: tras_fraz_{da}_{a}
    """
    if query:
        await query.answer()

    zone = db.get_zone_attive()
    da_zona = next((z for z in zone if z.get("id") == da_zona_id), None)

    if not da_zona:
        error = {"it": "⚠️ Zona non trovata.", "en": "⚠️ Area not found.", "de": "⚠️ Ortsteil nicht gefunden."}
        await edit_message_safe(query, text=error.get(lingua, error["it"]))
        return

    da_zona_nome = da_zona.get(f"nome_{lingua}") or da_zona.get("nome_it", "")
    da_zona_codice = da_zona.get("codice", "")

    if not a_zona_id or a_zona_id == 0:
        # Mostra scelta destinazione (griglia 2x2)
        titoli = {
            "it": f"🏘️ <b>Da {da_zona_nome}</b>\n\nDove vuoi andare?",
            "en": f"🏘️ <b>From {da_zona_nome}</b>\n\nWhere do you want to go?",
            "de": f"🏘️ <b>Von {da_zona_nome}</b>\n\nWohin möchten Sie?"
        }
        text = titoli.get(lingua, titoli["it"])

        # Griglia 2x2
        grid_order = [
            ["cavallino", "ca_di_valle"],
            ["ca_ballarin", "ca_pasquali"],
            ["ca_vio", "ca_savio"],
            ["treporti", "punta_sabbioni"]
        ]

        zone_map = {z.get("codice"): z for z in zone}

        buttons = []
        for row in grid_order:
            btn_row = []
            for codice in row:
                if codice in zone_map and zone_map[codice].get("id") != da_zona_id:
                    zona = zone_map[codice]
                    nome = zona.get(f"nome_{lingua}") or zona.get("nome_it", "")
                    zona_id = zona.get("id")
                    btn_row.append(InlineKeyboardButton(f"📍 {nome}", callback_data=f"tras_fraz_{da_zona_id}_{zona_id}"))
            if btn_row:
                buttons.append(btn_row)

        buttons.append([InlineKeyboardButton(TRASPORTI_LABELS["back_trasporti"].get(lingua, TRASPORTI_LABELS["back_trasporti"]["it"]), callback_data="tras_frazione")])

        keyboard = InlineKeyboardMarkup(buttons)
        await edit_message_safe(query, text=text, reply_markup=keyboard)
    else:
        # Mostra scelta linea bus (come per Venezia)
        a_zona = next((z for z in zone if z.get("id") == a_zona_id), None)
        if not a_zona:
            error = {"it": "⚠️ Zona non trovata.", "en": "⚠️ Area not found.", "de": "⚠️ Ortsteil nicht gefunden."}
            await edit_message_safe(query, text=error.get(lingua, error["it"]))
            return

        a_zona_nome = a_zona.get(f"nome_{lingua}") or a_zona.get("nome_it", "")

        # Redirect a selezione linea per frazione
        await handle_trasporti_frazione_linea(context, chat_id, lingua, query, da_zona_id, a_zona_id)


async def handle_trasporti_frazione_linea(context, chat_id: int, lingua: str, query, da_zona_id: int, a_zona_id: int):
    """
    SELEZIONE LINEA BUS per spostamenti tra frazioni.
    Callback: tras_fraz_linea_{da}_{a}
    """
    if query:
        await query.answer()

    zone = db.get_zone_attive()
    da_zona = next((z for z in zone if z.get("id") == da_zona_id), None)
    a_zona = next((z for z in zone if z.get("id") == a_zona_id), None)

    if not da_zona or not a_zona:
        error = {"it": "⚠️ Zona non trovata.", "en": "⚠️ Area not found.", "de": "⚠️ Ortsteil nicht gefunden."}
        await edit_message_safe(query, text=error.get(lingua, error["it"]))
        return

    da_zona_nome = da_zona.get(f"nome_{lingua}") or da_zona.get("nome_it", "")
    a_zona_nome = a_zona.get(f"nome_{lingua}") or a_zona.get("nome_it", "")
    da_zona_codice = da_zona.get("codice", "")

    # Linee disponibili per zona (stesse di handle_trasporti_linea)
    linee_per_zona = {
        "cavallino": [
            {"codice": "23A", "nome": "Via Fausta", "desc": "principale"},
            {"codice": "96", "nome": "Via Pordelio", "desc": "zona camping"},
        ],
        "ca_di_valle": [
            {"codice": "23A", "nome": "Via Fausta", "desc": "principale"},
        ],
        "ca_ballarin": [
            {"codice": "23A", "nome": "Via Fausta", "desc": "veloce"},
            {"codice": "23B", "nome": "Via Baracca", "desc": "camping interni"},
            {"codice": "96", "nome": "Via Pordelio", "desc": "zona mare"},
        ],
        "ca_pasquali": [
            {"codice": "23A", "nome": "Via Fausta", "desc": "veloce"},
            {"codice": "23B", "nome": "Via Baracca", "desc": "camping interni"},
            {"codice": "96", "nome": "Via Pordelio", "desc": "zona mare"},
        ],
        "ca_vio": [
            {"codice": "23A", "nome": "Via Fausta", "desc": "veloce"},
            {"codice": "23B", "nome": "Via Baracca", "desc": "camping interni"},
            {"codice": "96", "nome": "Via Pordelio", "desc": "zona mare"},
        ],
        "ca_savio": [
            {"codice": "23A", "nome": "Via Fausta", "desc": "principale"},
            {"codice": "23B", "nome": "Via Baracca", "desc": "camping"},
            {"codice": "96", "nome": "Via Treportina", "desc": "spiaggia"},
        ],
        "treporti": [
            {"codice": "96", "nome": "Via Pordelio", "desc": "Ricevitoria"},
        ],
        "punta_sabbioni": [
            {"codice": "23A", "nome": "Via Fausta", "desc": "principale"},
            {"codice": "96", "nome": "Via Pordelio", "desc": "zona camping"},
        ],
    }

    linee = linee_per_zona.get(da_zona_codice, [{"codice": "23A", "nome": "Via Fausta", "desc": "principale"}])

    # Se c'è una sola linea, vai direttamente alla selezione orario
    if len(linee) == 1:
        await handle_trasporti_frazione_quando(context, chat_id, lingua, query, da_zona_id, a_zona_id, linee[0]["codice"])
        return

    # Labels multilingua
    titoli = {
        "it": f"🏘️ <b>{da_zona_nome} → {a_zona_nome}</b>\n━━━━━━━━━━━━━━━━━━━━\n\n🚌 <b>Quale linea vuoi prendere?</b>",
        "en": f"🏘️ <b>{da_zona_nome} → {a_zona_nome}</b>\n━━━━━━━━━━━━━━━━━━━━\n\n🚌 <b>Which line do you want to take?</b>",
        "de": f"🏘️ <b>{da_zona_nome} → {a_zona_nome}</b>\n━━━━━━━━━━━━━━━━━━━━\n\n🚌 <b>Welche Linie möchten Sie nehmen?</b>"
    }

    text = titoli.get(lingua, titoli["it"])

    buttons = []
    for linea in linee:
        btn_text = f"🚌 {linea['codice']} - {linea['nome']} ({linea['desc']})"
        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"tras_fraz_quando_{da_zona_id}_{a_zona_id}_{linea['codice']}")])

    buttons.append([InlineKeyboardButton(TRASPORTI_LABELS["back_trasporti"].get(lingua, TRASPORTI_LABELS["back_trasporti"]["it"]), callback_data=f"tras_fraz_{da_zona_id}_0")])
    keyboard = InlineKeyboardMarkup(buttons)

    await edit_message_safe(query, text=text, reply_markup=keyboard)


async def handle_trasporti_frazione_quando(context, chat_id: int, lingua: str, query, da_zona_id: int, a_zona_id: int, linea_codice: str):
    """
    QUANDO VUOI PARTIRE? per spostamenti tra frazioni - con orari REALI dal database.
    Callback: tras_fraz_quando_{da}_{a}_{linea}
    """
    if query:
        await query.answer()

    zone = db.get_zone_attive()
    da_zona = next((z for z in zone if z.get("id") == da_zona_id), None)
    a_zona = next((z for z in zone if z.get("id") == a_zona_id), None)

    if not da_zona or not a_zona:
        error = {"it": "⚠️ Zona non trovata.", "en": "⚠️ Area not found.", "de": "⚠️ Ortsteil nicht gefunden."}
        await edit_message_safe(query, text=error.get(lingua, error["it"]))
        return

    da_zona_nome = da_zona.get(f"nome_{lingua}") or da_zona.get("nome_it", "")
    a_zona_nome = a_zona.get(f"nome_{lingua}") or a_zona.get("nome_it", "")
    da_zona_codice = da_zona.get("codice", "")

    # Mappa zona -> fermata per query database
    # NOTA: fermate disponibili nel DB: Cavallino, Ca' Savio, Punta Sabbioni, Ca' Pasquali, Ca' Vio, Ca' Ballarin
    fermate_db = {
        "cavallino": "Cavallino",
        "ca_savio": "Ca' Savio",
        "punta_sabbioni": "Punta Sabbioni",
        "treporti": "Punta Sabbioni",  # Treporti non esiste, usa Punta Sabbioni (vicina)
        "ca_pasquali": "Ca' Pasquali",
        "ca_vio": "Ca' Vio",
        "ca_ballarin": "Ca' Ballarin",
        "ca_di_valle": "Ca' Ballarin",  # Ca' di Valle non esiste, usa Ca' Ballarin (vicina)
    }
    fermata_nome = fermate_db.get(da_zona_codice, "Punta Sabbioni")

    # Labels multilingua
    labels = {
        "it": {"title": "🕐 Quando vuoi partire?", "linea": "Linea", "adesso": "Adesso"},
        "en": {"title": "🕐 When do you want to leave?", "linea": "Line", "adesso": "Now"},
        "de": {"title": "🕐 Wann möchten Sie abfahren?", "linea": "Linie", "adesso": "Jetzt"}
    }
    L = labels.get(lingua, labels["it"])

    # Query orari REALI dal database
    from datetime import timedelta
    import pytz
    rome_tz = pytz.timezone("Europe/Rome")
    now = datetime.now(rome_tz)
    ora_corrente = now.strftime("%H:%M")

    orari_db = db.get_prossimi_orari_bus(
        linea_codice=linea_codice,
        fermata_nome=fermata_nome,
        direzione="andata",
        ora_partenza=ora_corrente,
        limit=3
    )

    # Header
    text = f"🏘️ <b>{da_zona_nome} → {a_zona_nome}</b>\n"
    text += f"<i>🚌 {L['linea']} {linea_codice}</i>\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n\n"
    text += f"<b>{L['title']}</b>"

    # Costruisci bottoni con orari reali
    buttons = []

    if orari_db and len(orari_db) > 0:
        # Usa orari reali dal database
        row = []
        for i, orario in enumerate(orari_db[:3]):
            ora = orario.get("ora", "")
            try:
                ora_dt = datetime.strptime(ora, "%H:%M").replace(year=now.year, month=now.month, day=now.day, tzinfo=rome_tz)
                diff_minuti = int((ora_dt - now).total_seconds() / 60)
                if i == 0 and diff_minuti <= 10:
                    btn_text = L["adesso"]
                else:
                    btn_text = f"🕐 {ora}"
                row.append(InlineKeyboardButton(btn_text, callback_data=f"tras_fraz_viaggio_{da_zona_id}_{a_zona_id}_{linea_codice}_{diff_minuti}"))
            except ValueError:
                continue
        if row:
            buttons.append(row)
    else:
        # Fallback: orari stimati se database vuoto
        ora_1h = (now + timedelta(hours=1)).strftime("%H:%M")
        ora_2h = (now + timedelta(hours=2)).strftime("%H:%M")
        buttons.append([
            InlineKeyboardButton(L["adesso"], callback_data=f"tras_fraz_viaggio_{da_zona_id}_{a_zona_id}_{linea_codice}_0"),
            InlineKeyboardButton(f"🕐 {ora_1h}", callback_data=f"tras_fraz_viaggio_{da_zona_id}_{a_zona_id}_{linea_codice}_60"),
            InlineKeyboardButton(f"🕐 {ora_2h}", callback_data=f"tras_fraz_viaggio_{da_zona_id}_{a_zona_id}_{linea_codice}_120")
        ])

    # Bottone indietro
    buttons.append([InlineKeyboardButton(TRASPORTI_LABELS["back_trasporti"].get(lingua, TRASPORTI_LABELS["back_trasporti"]["it"]), callback_data=f"tras_fraz_{da_zona_id}_{a_zona_id}")])

    keyboard = InlineKeyboardMarkup(buttons)
    await edit_message_safe(query, text=text, reply_markup=keyboard)


async def handle_trasporti_frazione_viaggio(context, chat_id: int, lingua: str, query, da_zona_id: int, a_zona_id: int, linea_codice: str, offset_minuti: int = 0):
    """
    PROSSIME PARTENZE per spostamenti tra frazioni.
    Callback: tras_fraz_viaggio_{da}_{a}_{linea}_{offset}
    """
    if query:
        await query.answer()

    zone = db.get_zone_attive()
    da_zona = next((z for z in zone if z.get("id") == da_zona_id), None)
    a_zona = next((z for z in zone if z.get("id") == a_zona_id), None)

    if not da_zona or not a_zona:
        error = {"it": "⚠️ Zona non trovata.", "en": "⚠️ Area not found.", "de": "⚠️ Ortsteil nicht gefunden."}
        await edit_message_safe(query, text=error.get(lingua, error["it"]))
        return

    da_zona_nome = da_zona.get(f"nome_{lingua}") or da_zona.get("nome_it", "")
    a_zona_nome = a_zona.get(f"nome_{lingua}") or a_zona.get("nome_it", "")
    da_zona_codice = da_zona.get("codice", "")
    a_zona_codice = a_zona.get("codice", "")

    # Labels
    labels = {
        "it": {"title": "Prossime partenze", "linea": "Linea", "partenza": "Partenza", "arrivo": "Arrivo"},
        "en": {"title": "Next departures", "linea": "Line", "partenza": "Departure", "arrivo": "Arrival"},
        "de": {"title": "Nächste Abfahrten", "linea": "Linie", "partenza": "Abfahrt", "arrivo": "Ankunft"}
    }
    L = labels.get(lingua, labels["it"])

    # Header
    text = f"🏘️ <b>{da_zona_nome} → {a_zona_nome}</b>\n"
    text += f"<i>🚌 {L['linea']} {linea_codice}</i>\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n\n"

    # Prossime 3 partenze
    departures = _get_frazione_departures(da_zona_codice, a_zona_codice, linea_codice, lingua, 3, offset_minuti)

    for i, dep in enumerate(departures):
        num_emoji = ["1️⃣", "2️⃣", "3️⃣"][i]
        text += f"{num_emoji} 🚌 {dep['partenza']} → 🎯 {dep['arrivo']}\n"

    if departures:
        text += f"\n⏱️ Durata: ~{departures[0]['durata']} min\n"

    text += "\n🦭 <i>SLAPPY</i>"

    buttons = []

    # Bottone cambia orario
    cambia_orario_label = {"it": "🕐 Cambia orario", "en": "🕐 Change time", "de": "🕐 Zeit ändern"}
    buttons.append([InlineKeyboardButton(cambia_orario_label.get(lingua, cambia_orario_label["it"]), callback_data=f"tras_fraz_quando_{da_zona_id}_{a_zona_id}_{linea_codice}")])

    # Bottone indietro
    buttons.append([InlineKeyboardButton(TRASPORTI_LABELS["back_trasporti"].get(lingua, TRASPORTI_LABELS["back_trasporti"]["it"]), callback_data="tras_frazione")])

    keyboard = InlineKeyboardMarkup(buttons)
    await edit_message_safe(query, text=text, reply_markup=keyboard)


def _get_frazione_departures(da_codice: str, a_codice: str, linea_codice: str, lingua: str, count: int = 3, offset_minuti: int = 0) -> list:
    """
    Ritorna le prossime partenze tra due frazioni usando orari reali dal database.
    """
    from datetime import datetime, timedelta
    import pytz

    departures = []
    rome_tz = pytz.timezone("Europe/Rome")
    now = datetime.now(rome_tz)

    if offset_minuti > 0:
        start_time = now + timedelta(minutes=offset_minuti)
    else:
        start_time = now

    ora_partenza = start_time.strftime("%H:%M")

    # Mappa zona -> fermata per query database
    # NOTA: fermate disponibili nel DB: Cavallino, Ca' Savio, Punta Sabbioni, Ca' Pasquali, Ca' Vio, Ca' Ballarin
    fermate_db = {
        "cavallino": "Cavallino",
        "ca_savio": "Ca' Savio",
        "punta_sabbioni": "Punta Sabbioni",
        "treporti": "Punta Sabbioni",  # Treporti non esiste, usa Punta Sabbioni (vicina)
        "ca_pasquali": "Ca' Pasquali",
        "ca_vio": "Ca' Vio",
        "ca_ballarin": "Ca' Ballarin",
        "ca_di_valle": "Ca' Ballarin",  # Ca' di Valle non esiste, usa Ca' Ballarin (vicina)
    }
    fermata_partenza = fermate_db.get(da_codice, "Punta Sabbioni")
    fermata_arrivo = fermate_db.get(a_codice, "Punta Sabbioni")

    # Query orari REALI dal database
    orari_db = db.get_prossimi_orari_bus(
        linea_codice=linea_codice,
        fermata_nome=fermata_partenza,
        direzione="andata",
        ora_partenza=ora_partenza,
        limit=count
    )

    # Tempi stimati tra fermate (in minuti)
    tempi_tratta = {
        ("cavallino", "ca_savio"): 15,
        ("cavallino", "punta_sabbioni"): 25,
        ("ca_savio", "punta_sabbioni"): 10,
        ("treporti", "punta_sabbioni"): 5,
    }

    # Calcola tempo stimato (bidirezionale)
    durata = tempi_tratta.get((da_codice, a_codice), tempi_tratta.get((a_codice, da_codice), 15))

    if orari_db:
        for orario in orari_db:
            ora_bus = orario.get("ora", "")
            try:
                dep_time = datetime.strptime(ora_bus, "%H:%M").replace(year=now.year, month=now.month, day=now.day, tzinfo=rome_tz)
                arr_time = dep_time + timedelta(minutes=durata)
                departures.append({
                    "partenza": ora_bus,
                    "arrivo": arr_time.strftime("%H:%M"),
                    "durata": durata
                })
            except ValueError:
                continue
    else:
        # Fallback: genera orari stimati
        if start_time.minute < 30:
            next_dep = start_time.replace(minute=30, second=0, microsecond=0)
        else:
            next_dep = (start_time + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

        for i in range(count):
            dep_time = next_dep + timedelta(minutes=30 * i)
            arr_time = dep_time + timedelta(minutes=durata)
            departures.append({
                "partenza": dep_time.strftime("%H:%M"),
                "arrivo": arr_time.strftime("%H:%M"),
                "durata": durata
            })

    return departures


def _get_linee_frazione(da_codice: str, a_codice: str, lingua: str) -> str:
    """
    Ritorna info sulle linee bus per spostarsi tra due frazioni.
    """
    # Mapping linee bus per zone
    linee_per_zona = {
        "cavallino": ["23A Via Fausta", "96 Via Pordelio (da Cimitero)"],
        "ca_ballarin": ["23A Via Fausta (veloce)", "23B Via Baracca (camping)", "96 Via Pordelio (zona mare)"],
        "ca_pasquali": ["23A Via Fausta (veloce)", "23B Via Baracca (camping)", "96 Via Pordelio (zona mare)"],
        "ca_vio": ["23A Via Fausta (veloce)", "23B Via Baracca (camping)", "96 Via Pordelio (zona mare)"],
        "ca_savio": ["23A Via Fausta", "23B Via Baracca", "96 Via Treportina/Spiaggia"],
        "treporti": ["96 Via Pordelio/Ricevitoria", "95 Saccagnana (laguna)"],
        "punta_sabbioni": ["Tutte le linee"],
        "ca_di_valle": ["23A Via Fausta"],
    }

    # Camping per linea
    camping_info = {
        "23B": "Sant'Angelo, Garden Paradiso, Mediterraneo, San Marco, Italy Camping",
        "96": "Ca' Pasquali Village, Vela Blu, Marina di Venezia",
    }

    linee_da = linee_per_zona.get(da_codice, ["Bus locale"])
    linee_a = linee_per_zona.get(a_codice, ["Bus locale"])

    # Trova linee comuni
    linee_comuni = []
    for l1 in linee_da:
        for l2 in linee_a:
            if l1.split()[0] == l2.split()[0]:  # Stesso numero linea
                linee_comuni.append(l1)

    labels = {
        "it": {"linee": "Linee disponibili", "camping": "Camping serviti", "frequenza": "Frequenza: ogni 15-30 min"},
        "en": {"linee": "Available lines", "camping": "Campings served", "frequenza": "Frequency: every 15-30 min"},
        "de": {"linee": "Verfügbare Linien", "camping": "Bediente Campings", "frequenza": "Frequenz: alle 15-30 Min"}
    }
    L = labels.get(lingua, labels["it"])

    text = f"🚌 <b>{L['linee']}:</b>\n"

    if linee_comuni:
        for linea in linee_comuni:
            text += f"• {linea}\n"
            # Aggiungi camping se applicabile
            for num, camps in camping_info.items():
                if num in linea:
                    text += f"  <i>🏕️ {camps}</i>\n"
    else:
        text += "• Prendi una linea da " + da_codice.replace("_", " ").title() + "\n"
        text += "• Poi cambia a Punta Sabbioni\n"

    text += f"\n⏱️ <i>{L['frequenza']}</i>"

    return text


async def handle_trasporti_zona(context, chat_id: int, lingua: str, query, destinazione_id: int):
    """
    SELEZIONE ZONA - Da dove parti?
    Callback: tras_dest_{id}
    """
    if query:
        await query.answer()

    destinazione = db.get_destinazione_by_id(destinazione_id)
    if not destinazione:
        error = {"it": "⚠️ Destinazione non trovata.", "en": "⚠️ Destination not found.", "de": "⚠️ Ziel nicht gefunden."}
        await edit_message_safe(query, text=error.get(lingua, error["it"]))
        return

    nome_dest = destinazione.get(f"nome_{lingua}") or destinazione.get("nome_it", "")
    emoji_dest = destinazione.get("emoji", "📍")

    titoli = {
        "it": f"{emoji_dest} <b>{nome_dest}</b>\n\nDa dove parti?",
        "en": f"{emoji_dest} <b>{nome_dest}</b>\n\nWhere are you starting from?",
        "de": f"{emoji_dest} <b>{nome_dest}</b>\n\nVon wo starten Sie?"
    }

    text = titoli.get(lingua, titoli["it"])

    zone = db.get_zone_attive()

    if not zone:
        # Nessuna zona, vai diretto al percorso
        await handle_trasporti_percorso(context, chat_id, lingua, query, destinazione_id, None)
        return

    # Griglia 2x2 - tutte le 8 frazioni
    grid_order = [
        ["cavallino", "ca_di_valle"],
        ["ca_ballarin", "ca_pasquali"],
        ["ca_vio", "ca_savio"],
        ["treporti", "punta_sabbioni"]
    ]

    zone_map = {z.get("codice"): z for z in zone}

    buttons = []
    for row in grid_order:
        btn_row = []
        for codice in row:
            if codice in zone_map:
                zona = zone_map[codice]
                nome = zona.get(f"nome_{lingua}") or zona.get("nome_it", "")
                zona_id = zona.get("id")
                # Vai a selezione fermata se zona ha più fermate, altrimenti a orario
                btn_row.append(InlineKeyboardButton(f"📍 {nome}", callback_data=f"tras_fermata_{destinazione_id}_{zona_id}"))
        if btn_row:
            buttons.append(btn_row)

    buttons.append([InlineKeyboardButton(TRASPORTI_LABELS["back_trasporti"].get(lingua, TRASPORTI_LABELS["back_trasporti"]["it"]), callback_data="tras_arrivo")])
    keyboard = InlineKeyboardMarkup(buttons)

    await edit_message_safe(query, text=text, reply_markup=keyboard)


async def handle_trasporti_fermata(context, chat_id: int, lingua: str, query, destinazione_id: int, zona_id: int):
    """
    SELEZIONE FERMATA - Se la zona ha più fermate, mostra la scelta.
    Callback: tras_fermata_{dest}_{zona}
    """
    if query:
        await query.answer()

    destinazione = db.get_destinazione_by_id(destinazione_id)
    zona = None
    zone = db.get_zone_attive()
    if zone and zona_id:
        zona = next((z for z in zone if z.get("id") == zona_id), None)

    if not destinazione or not zona:
        error = {"it": "⚠️ Errore.", "en": "⚠️ Error.", "de": "⚠️ Fehler."}
        await edit_message_safe(query, text=error.get(lingua, error["it"]))
        return

    nome_dest = destinazione.get(f"nome_{lingua}") or destinazione.get("nome_it", "")
    emoji_dest = destinazione.get("emoji", "📍")
    nome_zona = zona.get(f"nome_{lingua}") or zona.get("nome_it", "")

    # Controlla se la zona ha fermate specifiche
    fermate = zona.get("fermate") or []

    # Vai sempre alla selezione linea bus
    await handle_trasporti_selezione_linea(context, chat_id, lingua, query, destinazione_id, zona_id)


async def handle_trasporti_selezione_linea(context, chat_id: int, lingua: str, query, destinazione_id: int, zona_id: int):
    """
    SELEZIONE LINEA BUS - Quale linea vuoi prendere?
    Callback: tras_linea_{dest}_{zona}
    """
    if query:
        await query.answer()

    destinazione = db.get_destinazione_by_id(destinazione_id)
    zona = None
    zone = db.get_zone_attive()
    if zone and zona_id:
        zona = next((z for z in zone if z.get("id") == zona_id), None)

    if not destinazione or not zona:
        error = {"it": "⚠️ Errore.", "en": "⚠️ Error.", "de": "⚠️ Fehler."}
        await edit_message_safe(query, text=error.get(lingua, error["it"]))
        return

    nome_dest = destinazione.get(f"nome_{lingua}") or destinazione.get("nome_it", "")
    emoji_dest = destinazione.get("emoji", "📍")
    nome_zona = zona.get(f"nome_{lingua}") or zona.get("nome_it", "")
    zona_codice = zona.get("codice", "")

    # Linee disponibili per zona (codice -> lista di {codice, nome, descrizione})
    linee_per_zona = {
        "cavallino": [
            {"codice": "23A", "nome": "Via Fausta", "desc": "principale"},
            {"codice": "96", "nome": "Via Pordelio", "desc": "zona camping"},
        ],
        "ca_di_valle": [
            {"codice": "23A", "nome": "Via Fausta", "desc": "principale"},
        ],
        "ca_ballarin": [
            {"codice": "23A", "nome": "Via Fausta", "desc": "veloce"},
            {"codice": "23B", "nome": "Via Baracca", "desc": "camping interni"},
            {"codice": "96", "nome": "Via Pordelio", "desc": "zona mare"},
        ],
        "ca_pasquali": [
            {"codice": "23A", "nome": "Via Fausta", "desc": "veloce"},
            {"codice": "23B", "nome": "Via Baracca", "desc": "camping interni"},
            {"codice": "96", "nome": "Via Pordelio", "desc": "zona mare"},
        ],
        "ca_vio": [
            {"codice": "23A", "nome": "Via Fausta", "desc": "veloce"},
            {"codice": "23B", "nome": "Via Baracca", "desc": "camping interni"},
            {"codice": "96", "nome": "Via Pordelio", "desc": "zona mare"},
        ],
        "ca_savio": [
            {"codice": "23A", "nome": "Via Fausta", "desc": "principale"},
            {"codice": "23B", "nome": "Via Baracca", "desc": "camping"},
            {"codice": "96", "nome": "Via Treportina", "desc": "spiaggia"},
        ],
        "treporti": [
            {"codice": "96", "nome": "Via Pordelio", "desc": "Ricevitoria"},
        ],
        "punta_sabbioni": [
            {"codice": "23A", "nome": "Via Fausta", "desc": "principale"},
            {"codice": "96", "nome": "Via Pordelio", "desc": "zona camping"},
        ],
    }

    linee = linee_per_zona.get(zona_codice, [{"codice": "23A", "nome": "Via Fausta", "desc": "principale"}])

    # Se c'è una sola linea, vai direttamente alla selezione orario
    if len(linee) == 1:
        await handle_trasporti_quando(context, chat_id, lingua, query, destinazione_id, zona_id, linee[0]["codice"])
        return

    # Labels multilingua
    titoli = {
        "it": f"{emoji_dest} <b>{nome_dest}</b>\n📍 {nome_zona}\n━━━━━━━━━━━━━━━━━━━━\n\n🚌 <b>Quale linea vuoi prendere?</b>",
        "en": f"{emoji_dest} <b>{nome_dest}</b>\n📍 {nome_zona}\n━━━━━━━━━━━━━━━━━━━━\n\n🚌 <b>Which line do you want to take?</b>",
        "de": f"{emoji_dest} <b>{nome_dest}</b>\n📍 {nome_zona}\n━━━━━━━━━━━━━━━━━━━━\n\n🚌 <b>Welche Linie möchten Sie nehmen?</b>"
    }

    text = titoli.get(lingua, titoli["it"])

    buttons = []
    for linea in linee:
        btn_text = f"🚌 {linea['codice']} - {linea['nome']} ({linea['desc']})"
        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"tras_percorso_{destinazione_id}_{zona_id}_{linea['codice']}")])

    buttons.append([InlineKeyboardButton(TRASPORTI_LABELS["back_trasporti"].get(lingua, TRASPORTI_LABELS["back_trasporti"]["it"]), callback_data=f"tras_dest_{destinazione_id}")])
    keyboard = InlineKeyboardMarkup(buttons)

    await edit_message_safe(query, text=text, reply_markup=keyboard)


async def handle_trasporti_isole(context, chat_id: int, lingua: str, query):
    """
    ISOLE - Submenu per Murano, Burano, Torcello.
    Callback: tras_isole
    """
    if query:
        await query.answer()

    titoli = {
        "it": "🏝️ <b>Isole della Laguna</b>\n\nScegli la tua destinazione:",
        "en": "🏝️ <b>Lagoon Islands</b>\n\nChoose your destination:",
        "de": "🏝️ <b>Laguneninseln</b>\n\nWählen Sie Ihr Ziel:"
    }

    text = titoli.get(lingua, titoli["it"])

    destinazioni = db.get_destinazioni_attive()
    dest_map = {d.get("codice"): d for d in destinazioni} if destinazioni else {}

    buttons = []
    for codice in ["murano", "burano", "torcello"]:
        if codice in dest_map:
            dest = dest_map[codice]
            emoji = dest.get("emoji", "🏝️")
            nome = dest.get(f"nome_{lingua}") or dest.get("nome_it", "")
            dest_id = dest.get("id")
            buttons.append([InlineKeyboardButton(f"{emoji} {nome}", callback_data=f"tras_dest_{dest_id}")])

    if not buttons:
        # Fallback se non ci sono destinazioni isole nel DB
        no_isole = {"it": "\n<i>Destinazioni non disponibili.</i>", "en": "\n<i>Destinations not available.</i>", "de": "\n<i>Ziele nicht verfügbar.</i>"}
        text += no_isole.get(lingua, no_isole["it"])

    buttons.append([InlineKeyboardButton(TRASPORTI_LABELS["back_trasporti"].get(lingua, TRASPORTI_LABELS["back_trasporti"]["it"]), callback_data="tras_arrivo")])
    keyboard = InlineKeyboardMarkup(buttons)

    await edit_message_safe(query, text=text, reply_markup=keyboard)


async def handle_trasporti_paese(context, chat_id: int, lingua: str, query):
    """
    CAVALLINO-TREPORTI - Ritorno in paese (percorso inverso da Venezia/Lido).
    Callback: tras_paese
    """
    if query:
        await query.answer()

    titoli = {
        "it": "🏠 <b>Ritorno a Cavallino-Treporti</b>\n\nDa dove stai tornando?",
        "en": "🏠 <b>Return to Cavallino-Treporti</b>\n\nWhere are you returning from?",
        "de": "🏠 <b>Rückkehr nach Cavallino-Treporti</b>\n\nVon wo kehren Sie zurück?"
    }

    text = titoli.get(lingua, titoli["it"])

    # Mostra le destinazioni come punti di partenza per il ritorno
    destinazioni = db.get_destinazioni_attive()
    dest_map = {d.get("codice"): d for d in destinazioni} if destinazioni else {}

    buttons = []

    # Riga 1: Venezia, Lido
    row1 = []
    for codice in ["venezia", "lido"]:
        if codice in dest_map:
            dest = dest_map[codice]
            emoji = dest.get("emoji", "📍")
            nome = dest.get(f"nome_{lingua}") or dest.get("nome_it", "")
            nome_short = nome.split("(")[0].strip().split(" - ")[0].strip()
            dest_id = dest.get("id")
            # Usa callback per ritorno
            row1.append(InlineKeyboardButton(f"{emoji} {nome_short}", callback_data=f"tras_ritorno_{dest_id}"))
    if row1:
        buttons.append(row1)

    # Riga 2: Jesolo, Aeroporto
    row2 = []
    for codice in ["jesolo", "aeroporto"]:
        if codice in dest_map:
            dest = dest_map[codice]
            emoji = dest.get("emoji", "📍")
            nome = dest.get(f"nome_{lingua}") or dest.get("nome_it", "")
            nome_short = nome.split("(")[0].strip().split(" - ")[0].strip()
            dest_id = dest.get("id")
            row2.append(InlineKeyboardButton(f"{emoji} {nome_short}", callback_data=f"tras_ritorno_{dest_id}"))
    if row2:
        buttons.append(row2)

    # Isole
    isole_row = []
    for codice in ["murano", "burano"]:
        if codice in dest_map:
            dest = dest_map[codice]
            emoji = dest.get("emoji", "🏝️")
            nome = dest.get(f"nome_{lingua}") or dest.get("nome_it", "")
            dest_id = dest.get("id")
            isole_row.append(InlineKeyboardButton(f"{emoji} {nome}", callback_data=f"tras_ritorno_{dest_id}"))
    if isole_row:
        buttons.append(isole_row)

    buttons.append([InlineKeyboardButton(TRASPORTI_LABELS["back_trasporti"].get(lingua, TRASPORTI_LABELS["back_trasporti"]["it"]), callback_data="tras_arrivo")])
    keyboard = InlineKeyboardMarkup(buttons)

    await edit_message_safe(query, text=text, reply_markup=keyboard)


async def handle_trasporti_quando(context, chat_id: int, lingua: str, query, destinazione_id: int, zona_id: int = None, linea_codice: str = "23A"):
    """
    QUANDO VUOI PARTIRE? - Selezione orario partenza con orari REALI dal database.
    Callback: tras_percorso_{dest}_{zona}_{linea}
    """
    if query:
        await query.answer()

    # Cancella eventuale pending_action (utente ha annullato input orario)
    db.update_user(chat_id, {"pending_action": None})

    destinazione = db.get_destinazione_by_id(destinazione_id)
    if not destinazione:
        error = {"it": "⚠️ Destinazione non trovata.", "en": "⚠️ Destination not found.", "de": "⚠️ Ziel nicht gefunden."}
        await edit_message_safe(query, text=error.get(lingua, error["it"]))
        return

    nome_dest = destinazione.get(f"nome_{lingua}") or destinazione.get("nome_it", "")
    emoji_dest = destinazione.get("emoji", "📍")

    # Recupera zona
    zona_nome = ""
    zona_codice = "punta_sabbioni"
    if zona_id:
        zone = db.get_zone_attive()
        zona = next((z for z in zone if z.get("id") == zona_id), None)
        if zona:
            zona_nome = zona.get(f"nome_{lingua}") or zona.get("nome_it", "")
            zona_codice = zona.get("codice", "punta_sabbioni")

    # Mappa zona -> fermata per query database
    # NOTA: fermate disponibili nel DB: Cavallino, Ca' Savio, Punta Sabbioni, Ca' Pasquali, Ca' Vio, Ca' Ballarin
    fermate_db = {
        "cavallino": "Cavallino",
        "ca_savio": "Ca' Savio",
        "punta_sabbioni": "Punta Sabbioni",
        "treporti": "Punta Sabbioni",  # Treporti non esiste, usa Punta Sabbioni (vicina)
        "ca_pasquali": "Ca' Pasquali",
        "ca_vio": "Ca' Vio",
        "ca_ballarin": "Ca' Ballarin",
        "ca_di_valle": "Ca' Ballarin",  # Ca' di Valle non esiste, usa Ca' Ballarin (vicina)
    }
    fermata_nome = fermate_db.get(zona_codice, "Punta Sabbioni")

    # Labels multilingua
    labels = {
        "it": {"title": "🕐 Quando vuoi partire?", "da": "Da", "linea": "Linea", "adesso": "Adesso", "prossime": "Prossime partenze"},
        "en": {"title": "🕐 When do you want to leave?", "da": "From", "linea": "Line", "adesso": "Now", "prossime": "Next departures"},
        "de": {"title": "🕐 Wann möchten Sie abfahren?", "da": "Von", "linea": "Linie", "adesso": "Jetzt", "prossime": "Nächste Abfahrten"}
    }
    L = labels.get(lingua, labels["it"])

    # Query orari REALI dal database
    from datetime import timedelta
    import pytz
    rome_tz = pytz.timezone("Europe/Rome")
    now = datetime.now(rome_tz)
    ora_corrente = now.strftime("%H:%M")

    orari_db = db.get_prossimi_orari_bus(
        linea_codice=linea_codice,
        fermata_nome=fermata_nome,
        direzione="andata",
        ora_partenza=ora_corrente,
        limit=3
    )

    # Header
    text = f"{emoji_dest} <b>{nome_dest}</b>\n"
    if zona_nome:
        text += f"<i>{L['da']} {zona_nome}</i>\n"
    text += f"<i>🚌 {L['linea']} {linea_codice}</i>\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n\n"
    text += f"<b>{L['title']}</b>"

    zona_str = zona_id if zona_id else 0

    # Costruisci bottoni con orari reali
    buttons = []

    if orari_db and len(orari_db) > 0:
        # Usa orari reali dal database - passa ORA ESATTA nel callback (formato HH-MM)
        row = []
        for i, orario in enumerate(orari_db[:3]):
            ora = orario.get("ora", "")
            ora_callback = ora.replace(":", "-")  # 17:00 -> 17-00 per callback
            if i == 0:
                # Prima partenza = "Adesso" se entro 10 minuti
                try:
                    ora_dt = datetime.strptime(ora, "%H:%M").replace(year=now.year, month=now.month, day=now.day, tzinfo=rome_tz)
                    diff_minuti = int((ora_dt - now).total_seconds() / 60)
                    if diff_minuti <= 10:
                        btn_text = L["adesso"]
                    else:
                        btn_text = f"🕐 {ora}"
                except ValueError:
                    btn_text = f"🕐 {ora}"
                row.append(InlineKeyboardButton(btn_text, callback_data=f"tras_viaggio_{destinazione_id}_{zona_str}_{linea_codice}_{ora_callback}"))
            else:
                row.append(InlineKeyboardButton(f"🕐 {ora}", callback_data=f"tras_viaggio_{destinazione_id}_{zona_str}_{linea_codice}_{ora_callback}"))
        if row:
            buttons.append(row)
    else:
        # Fallback: nessun orario disponibile
        no_orari = {"it": "⚠️ Nessun orario disponibile", "en": "⚠️ No schedule available", "de": "⚠️ Kein Fahrplan verfügbar"}
        buttons.append([InlineKeyboardButton(no_orari.get(lingua, no_orari["it"]), callback_data="noop")])

    # Label per inserimento manuale
    inserisci_label = {"it": "✏️ Inserisci orario", "en": "✏️ Enter time", "de": "✏️ Zeit eingeben"}
    buttons.append([InlineKeyboardButton(inserisci_label.get(lingua, inserisci_label["it"]), callback_data=f"tras_orario_custom_{destinazione_id}_{zona_str}_{linea_codice}")])

    # Bottone indietro
    buttons.append([InlineKeyboardButton(TRASPORTI_LABELS["back_trasporti"].get(lingua, TRASPORTI_LABELS["back_trasporti"]["it"]), callback_data=f"tras_dest_{destinazione_id}")])

    keyboard = InlineKeyboardMarkup(buttons)
    await edit_message_safe(query, text=text, reply_markup=keyboard)


async def handle_trasporti_orario_custom(context, chat_id: int, lingua: str, query, destinazione_id: int, zona_id: int = None, linea_codice: str = "23A"):
    """
    INSERISCI ORARIO - Chiede all'utente di scrivere l'orario desiderato.
    Callback: tras_orario_custom_{dest}_{zona}_{linea}
    """
    if query:
        await query.answer()

    destinazione = db.get_destinazione_by_id(destinazione_id)
    if not destinazione:
        return

    nome_dest = destinazione.get(f"nome_{lingua}") or destinazione.get("nome_it", "")
    emoji_dest = destinazione.get("emoji", "📍")

    # Recupera zona
    zona_nome = ""
    if zona_id:
        zone = db.get_zone_attive()
        zona = next((z for z in zone if z.get("id") == zona_id), None)
        if zona:
            zona_nome = zona.get(f"nome_{lingua}") or zona.get("nome_it", "")

    # Messaggi multilingua
    messages = {
        "it": {
            "prompt": "✏️ <b>Inserisci orario</b>\n\nScrivi l'orario di partenza desiderato.\n\n<i>Esempi:</i>\n• <code>14:30</code>\n• <code>9:00</code>\n• <code>17:00 domani</code>\n• <code>8:30 dopodomani</code>",
            "annulla": "❌ Annulla"
        },
        "en": {
            "prompt": "✏️ <b>Enter time</b>\n\nWrite your desired departure time.\n\n<i>Examples:</i>\n• <code>14:30</code>\n• <code>9:00</code>\n• <code>17:00 tomorrow</code>\n• <code>8:30 day after tomorrow</code>",
            "annulla": "❌ Cancel"
        },
        "de": {
            "prompt": "✏️ <b>Zeit eingeben</b>\n\nSchreiben Sie Ihre gewünschte Abfahrtszeit.\n\n<i>Beispiele:</i>\n• <code>14:30</code>\n• <code>9:00</code>\n• <code>17:00 morgen</code>\n• <code>8:30 übermorgen</code>",
            "annulla": "❌ Abbrechen"
        }
    }
    M = messages.get(lingua, messages["it"])

    text = f"{emoji_dest} <b>{nome_dest}</b>\n"
    if zona_nome:
        text += f"<i>Da {zona_nome}</i>\n"
    text += f"<i>🚌 Linea {linea_codice}</i>\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n\n"
    text += M["prompt"]

    # Ottieni message_id del messaggio bot da editare dopo
    bot_msg_id = query.message.message_id if query and query.message else None

    # Salva stato pending per riconoscere il prossimo messaggio
    import json
    pending_data = json.dumps({
        "action": "trasporti_orario",
        "dest_id": destinazione_id,
        "zona_id": zona_id,
        "linea_codice": linea_codice,
        "bot_msg_id": bot_msg_id
    })

    logger.info(f"[ORARIO_CUSTOM] Saving pending_action: {pending_data}")
    db.update_user(chat_id, {"pending_action": pending_data})

    # Bottone annulla
    buttons = [[InlineKeyboardButton(M["annulla"], callback_data=f"tras_percorso_{destinazione_id}_{zona_id or 0}_{linea_codice}")]]
    keyboard = InlineKeyboardMarkup(buttons)

    await edit_message_safe(query, text=text, reply_markup=keyboard)
    logger.info(f"[ORARIO_CUSTOM] Message edited, waiting for user input")


def parse_time_input(text: str, lingua: str = "it") -> tuple:
    """
    Interpreta un orario scritto dall'utente.
    Ritorna (offset_minuti, errore) dove errore è None se ok.
    """
    import re
    from datetime import datetime, timedelta
    import pytz

    text = text.strip().lower()

    # Pattern per orario: HH:MM o H:MM
    time_match = re.search(r'(\d{1,2})[:.:](\d{2})', text)
    if not time_match:
        errors = {
            "it": "Non ho capito l'orario. Scrivi nel formato HH:MM (es: 14:30)",
            "en": "I didn't understand the time. Write in HH:MM format (e.g.: 14:30)",
            "de": "Ich habe die Zeit nicht verstanden. Schreiben Sie im Format HH:MM (z.B.: 14:30)"
        }
        return None, errors.get(lingua, errors["it"])

    hour = int(time_match.group(1))
    minute = int(time_match.group(2))

    if hour > 23 or minute > 59:
        errors = {
            "it": "Orario non valido. L'ora deve essere 0-23 e i minuti 0-59.",
            "en": "Invalid time. Hours must be 0-23 and minutes 0-59.",
            "de": "Ungültige Zeit. Stunden müssen 0-23 und Minuten 0-59 sein."
        }
        return None, errors.get(lingua, errors["it"])

    # Determina il giorno (timezone Italia)
    rome_tz = pytz.timezone("Europe/Rome")
    now = datetime.now(rome_tz)
    days_ahead = 0

    # Controlla parole chiave per giorno
    domani_words = ["domani", "tomorrow", "morgen"]
    dopodomani_words = ["dopodomani", "day after tomorrow", "übermorgen"]

    if any(w in text for w in dopodomani_words):
        days_ahead = 2
    elif any(w in text for w in domani_words):
        days_ahead = 1

    # Costruisci datetime target (con timezone)
    target = rome_tz.localize(datetime(now.year, now.month, now.day, hour, minute)) + timedelta(days=days_ahead)

    # Se l'orario è passato oggi e non specificato domani, assume domani
    if days_ahead == 0 and target < now:
        target += timedelta(days=1)

    # Ritorna orario esatto in formato HH:MM
    ora_esatta = f"{hour:02d}:{minute:02d}"

    return ora_esatta, None


def _get_journey_data(dest_codice: str, zona_codice: str, lingua: str, linea_codice: str = "23A", ora_partenza: str = None) -> dict:
    """
    Dati journey planner per ogni combinazione destinazione+zona+linea.
    Usa la linea selezionata dall'utente e l'orario esatto.
    ora_partenza: orario esatto (es. "17:00") - se None usa ora corrente
    """
    from datetime import datetime, timedelta
    import pytz

    rome_tz = pytz.timezone("Europe/Rome")
    now = datetime.now(rome_tz)

    # Usa l'orario esatto passato, oppure ora corrente
    if ora_partenza:
        # Normalizza formato (17-00 -> 17:00)
        next_dep_str = ora_partenza.replace("-", ":")
        # Crea datetime object per calcoli
        try:
            next_dep = datetime.strptime(next_dep_str, "%H:%M").replace(year=now.year, month=now.month, day=now.day, tzinfo=rome_tz)
            is_tomorrow = next_dep < now  # Se l'ora è passata, è domani
            if is_tomorrow:
                next_dep += timedelta(days=1)
        except ValueError:
            next_dep = now
            is_tomorrow = False
    else:
        next_dep_str = now.strftime("%H:%M")
        next_dep = now
        is_tomorrow = False

    # Fermate per linea e zona
    fermate_per_linea = {
        "23A": {
            "cavallino": {"it": "Via Fausta (Camping Union Lido)", "en": "Via Fausta (Camping Union Lido)", "de": "Via Fausta (Camping Union Lido)"},
            "ca_savio": {"it": "Via Fausta (Ca' Savio centro)", "en": "Via Fausta (Ca' Savio center)", "de": "Via Fausta (Ca' Savio Zentrum)"},
            "punta_sabbioni": {"it": "Punta Sabbioni (Terminal)", "en": "Punta Sabbioni (Terminal)", "de": "Punta Sabbioni (Terminal)"},
            "ca_pasquali": {"it": "Via Fausta (Ca' Pasquali)", "en": "Via Fausta (Ca' Pasquali)", "de": "Via Fausta (Ca' Pasquali)"},
            "ca_vio": {"it": "Via Fausta (Ca' Vio)", "en": "Via Fausta (Ca' Vio)", "de": "Via Fausta (Ca' Vio)"},
            "ca_ballarin": {"it": "Via Fausta (Ca' Ballarin)", "en": "Via Fausta (Ca' Ballarin)", "de": "Via Fausta (Ca' Ballarin)"},
            "ca_di_valle": {"it": "Via Fausta (Ca' di Valle)", "en": "Via Fausta (Ca' di Valle)", "de": "Via Fausta (Ca' di Valle)"},
        },
        "23B": {
            "cavallino": {"it": "Via Baracca (Sant'Angelo)", "en": "Via Baracca (Sant'Angelo)", "de": "Via Baracca (Sant'Angelo)"},
            "ca_savio": {"it": "Via Baracca (Camping Italy)", "en": "Via Baracca (Camping Italy)", "de": "Via Baracca (Camping Italy)"},
            "ca_pasquali": {"it": "Via Baracca (Garden Paradiso)", "en": "Via Baracca (Garden Paradiso)", "de": "Via Baracca (Garden Paradiso)"},
            "ca_vio": {"it": "Via Baracca (Mediterraneo)", "en": "Via Baracca (Mediterraneo)", "de": "Via Baracca (Mediterraneo)"},
            "ca_ballarin": {"it": "Via Baracca (San Marco)", "en": "Via Baracca (San Marco)", "de": "Via Baracca (San Marco)"},
            "punta_sabbioni": {"it": "Punta Sabbioni (Terminal)", "en": "Punta Sabbioni (Terminal)", "de": "Punta Sabbioni (Terminal)"},
        },
        "96": {
            "cavallino": {"it": "Via Pordelio (Cimitero)", "en": "Via Pordelio (Cemetery)", "de": "Via Pordelio (Friedhof)"},
            "treporti": {"it": "Via Pordelio (Treporti)", "en": "Via Pordelio (Treporti)", "de": "Via Pordelio (Treporti)"},
            "ca_pasquali": {"it": "Via Pordelio (Ca' Pasquali Village)", "en": "Via Pordelio (Ca' Pasquali Village)", "de": "Via Pordelio (Ca' Pasquali Village)"},
            "ca_savio": {"it": "Via Treportina (Spiaggia)", "en": "Via Treportina (Beach)", "de": "Via Treportina (Strand)"},
            "ca_vio": {"it": "Via Pordelio (Vela Blu)", "en": "Via Pordelio (Vela Blu)", "de": "Via Pordelio (Vela Blu)"},
            "punta_sabbioni": {"it": "Punta Sabbioni (Terminal)", "en": "Punta Sabbioni (Terminal)", "de": "Punta Sabbioni (Terminal)"},
        }
    }

    # Fermata di default
    default_fermata = {"it": "Punta Sabbioni (Terminal)", "en": "Punta Sabbioni (Terminal)", "de": "Punta Sabbioni (Terminal)"}
    fermate_linea = fermate_per_linea.get(linea_codice, fermate_per_linea["23A"])
    fermata = fermate_linea.get(zona_codice, default_fermata)

    # Dati percorso per destinazione
    journeys = {
        "venezia": {
            "bus_linea": "23",
            "bus_tempo": 25,
            "ferry_linea": "14",
            "ferry_tempo": 35,
            "costo": "15.00",
            "ultimo_ritorno": "23:30",
            "note": {"it": "Frequenza traghetti: ogni 20 min", "en": "Ferry frequency: every 20 min", "de": "Fährfrequenz: alle 20 Min"}
        },
        "lido": {
            "bus_linea": "23",
            "bus_tempo": 25,
            "ferry_linea": "14",
            "ferry_tempo": 15,
            "costo": "12.00",
            "ultimo_ritorno": "23:45",
            "note": {"it": "Scendi a Lido S.M.E.", "en": "Get off at Lido S.M.E.", "de": "Aussteigen bei Lido S.M.E."}
        },
        "burano": {
            "bus_linea": "23",
            "bus_tempo": 25,
            "ferry_linea": "12",
            "ferry_tempo": 40,
            "costo": "15.00",
            "ultimo_ritorno": "21:30",
            "note": {"it": "Via Treporti-Burano diretto", "en": "Via Treporti-Burano direct", "de": "Über Treporti-Burano direkt"}
        },
        "murano": {
            "bus_linea": "23",
            "bus_tempo": 25,
            "ferry_linea": "12",
            "ferry_tempo": 55,
            "costo": "15.00",
            "ultimo_ritorno": "22:00",
            "note": {"it": "Cambio a Burano per Murano", "en": "Change at Burano for Murano", "de": "Umsteigen in Burano nach Murano"}
        },
        "jesolo": {
            "bus_linea": "23A",
            "bus_tempo": 45,
            "ferry_linea": None,
            "ferry_tempo": 0,
            "costo": "4.50",
            "ultimo_ritorno": "22:30",
            "note": {"it": "Bus diretto, no traghetto", "en": "Direct bus, no ferry", "de": "Direktbus, keine Fähre"}
        },
        "aeroporto": {
            "bus_linea": "23A + 35",
            "bus_tempo": 90,
            "ferry_linea": None,
            "ferry_tempo": 0,
            "costo": "12.00",
            "ultimo_ritorno": "21:00",
            "note": {"it": "Cambio a Jesolo Stazione Bus", "en": "Change at Jesolo Bus Station", "de": "Umsteigen am Jesolo Busbahnhof"}
        }
    }

    journey = journeys.get(dest_codice, journeys["venezia"])

    # Calcola orari
    bus_arrivo = next_dep + timedelta(minutes=journey["bus_tempo"])
    bus_arrivo_str = bus_arrivo.strftime("%H:%M")

    if journey["ferry_linea"]:
        # Traghetto parte 15 min dopo arrivo bus
        ferry_partenza = bus_arrivo + timedelta(minutes=15)
        ferry_partenza_str = ferry_partenza.strftime("%H:%M")
        arrivo_finale = ferry_partenza + timedelta(minutes=journey["ferry_tempo"])
        arrivo_finale_str = arrivo_finale.strftime("%H:%M")
    else:
        ferry_partenza_str = None
        arrivo_finale_str = bus_arrivo_str

    # Etichetta data per domani
    data_label = None
    if is_tomorrow:
        domani_labels = {"it": "domani", "en": "tomorrow", "de": "morgen"}
        data_label = domani_labels.get(lingua, domani_labels["it"])

    return {
        "fermata": fermata.get(lingua, fermata["it"]),
        "bus_linea": linea_codice,  # Usa la linea selezionata dall'utente
        "bus_partenza": next_dep_str,
        "bus_tempo": journey["bus_tempo"],
        "bus_arrivo": bus_arrivo_str,
        "ferry_linea": journey["ferry_linea"],
        "ferry_partenza": ferry_partenza_str,
        "ferry_tempo": journey["ferry_tempo"],
        "arrivo_finale": arrivo_finale_str,
        "costo": journey["costo"],
        "ultimo_ritorno": journey["ultimo_ritorno"],
        "note": journey["note"].get(lingua, journey["note"]["it"]),
        "data_label": data_label
    }


async def handle_trasporti_percorso(context, chat_id: int, lingua: str, query, destinazione_id: int, zona_id: int = None, linea_codice: str = "23A", ora_partenza: str = None, bot_msg_id: int = None):
    """
    JOURNEY PLANNER - Mostra orari reali con bus + traghetto.
    Callback: tras_viaggio_{dest_id}_{zona_id}_{linea}_{ora}
    ora_partenza: orario esatto selezionato (es. "17:00")
    bot_msg_id: se presente, edita questo messaggio invece di crearne uno nuovo
    """
    if query:
        await query.answer()

    destinazione = db.get_destinazione_by_id(destinazione_id)
    if not destinazione:
        error = {"it": "⚠️ Destinazione non trovata.", "en": "⚠️ Destination not found.", "de": "⚠️ Ziel nicht gefunden."}
        error_text = error.get(lingua, error["it"])
        if query:
            await edit_message_safe(query, text=error_text)
        elif bot_msg_id:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=bot_msg_id, text=error_text, parse_mode="HTML")
        else:
            await context.bot.send_message(chat_id=chat_id, text=error_text, parse_mode="HTML")
        return

    nome_dest = destinazione.get(f"nome_{lingua}") or destinazione.get("nome_it", "")
    emoji_dest = destinazione.get("emoji", "📍")
    dest_codice = destinazione.get("codice", "venezia")

    # Recupera zona
    zona_codice = "punta_sabbioni"
    zona_nome = "Punta Sabbioni"
    if zona_id:
        zone = db.get_zone_attive()
        zona = next((z for z in zone if z.get("id") == zona_id), None)
        if zona:
            zona_nome = zona.get(f"nome_{lingua}") or zona.get("nome_it", "")
            zona_codice = zona.get("codice", "punta_sabbioni")

    # Ottieni dati journey con orario esatto
    j = _get_journey_data(dest_codice, zona_codice, lingua, linea_codice, ora_partenza)

    # Labels multilingua
    labels = {
        "it": {
            "da": "Da",
            "prossima": "Prossima partenza",
            "fermata": "Fermata",
            "tempo": "Tempo percorrenza",
            "arrivo_ps": "Arrivo Punta Sabbioni",
            "traghetto": "Traghetto linea",
            "arrivo": "Arrivo",
            "costo": "Costo totale",
            "ultimo": "Ultimo ritorno",
            "min": "min"
        },
        "en": {
            "da": "From",
            "prossima": "Next departure",
            "fermata": "Stop",
            "tempo": "Travel time",
            "arrivo_ps": "Arrival Punta Sabbioni",
            "traghetto": "Ferry line",
            "arrivo": "Arrival",
            "costo": "Total cost",
            "ultimo": "Last return",
            "min": "min"
        },
        "de": {
            "da": "Von",
            "prossima": "Nächste Abfahrt",
            "fermata": "Haltestelle",
            "tempo": "Fahrzeit",
            "arrivo_ps": "Ankunft Punta Sabbioni",
            "traghetto": "Fähre Linie",
            "arrivo": "Ankunft",
            "costo": "Gesamtkosten",
            "ultimo": "Letzte Rückfahrt",
            "min": "Min"
        }
    }
    L = labels.get(lingua, labels["it"])

    # Costruisci messaggio
    text = f"{emoji_dest} <b>{nome_dest}</b>\n"
    text += f"<i>{L['da']} {zona_nome}</i>\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n\n"

    # BUS (con eventuale label "domani")
    partenza_str = j['bus_partenza']
    if j.get("data_label"):
        partenza_str = f"{j['bus_partenza']} ({j['data_label']})"

    text += f"🚌 <b>{L['prossima']}:</b> {partenza_str}\n"
    text += f"📍 <b>{L['fermata']}:</b> {j['fermata']}\n"
    text += f"⏱️ <b>{L['tempo']}:</b> {j['bus_tempo']} {L['min']}\n"

    if j["ferry_linea"]:
        text += f"🏁 <b>{L['arrivo_ps']}:</b> {j['bus_arrivo']}\n\n"
        # TRAGHETTO
        text += f"🚢 <b>{L['traghetto']} {j['ferry_linea']}:</b> {j['ferry_partenza']}\n"
        text += f"🎯 <b>{L['arrivo']} {nome_dest}:</b> {j['arrivo_finale']}\n\n"
    else:
        text += f"🎯 <b>{L['arrivo']}:</b> {j['arrivo_finale']}\n\n"

    text += f"💰 <b>{L['costo']}:</b> €{j['costo']}\n"
    text += f"⚠️ <b>{L['ultimo']}:</b> {j['ultimo_ritorno']}\n\n"

    if j["note"]:
        text += f"📝 <i>{j['note']}</i>\n"

    text += "\n🦭 <i>SLAPPY</i>"

    # Bottoni
    buttons = []

    # Riga 1: Prossime partenze
    prossimo_label = {"it": "🔄 Prossime partenze", "en": "🔄 Next departures", "de": "🔄 Nächste Abfahrten"}
    ora_callback = ora_partenza.replace(":", "-") if ora_partenza else "now"
    buttons.append([InlineKeyboardButton(prossimo_label.get(lingua, prossimo_label["it"]), callback_data=f"tras_orari_{destinazione_id}_{zona_id or 0}_{linea_codice}_{ora_callback}")])

    # Riga 2: Cambia orario
    cambia_orario_label = {"it": "🕐 Cambia orario", "en": "🕐 Change time", "de": "🕐 Zeit ändern"}
    buttons.append([InlineKeyboardButton(cambia_orario_label.get(lingua, cambia_orario_label["it"]), callback_data=f"tras_percorso_{destinazione_id}_{zona_id or 0}_{linea_codice}")])

    # Riga 3: Indietro
    buttons.append([InlineKeyboardButton(TRASPORTI_LABELS["back_trasporti"].get(lingua, TRASPORTI_LABELS["back_trasporti"]["it"]), callback_data="tras_arrivo")])

    keyboard = InlineKeyboardMarkup(buttons)

    if query:
        await edit_message_safe(query, text=text, reply_markup=keyboard)
    elif bot_msg_id:
        # Chiamato da text input (orario custom) - edita messaggio esistente
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=bot_msg_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            logger.info(f"[PERCORSO] Edited message {bot_msg_id}")
        except Exception as e:
            logger.error(f"[PERCORSO] Edit failed: {e}, sending new message")
            await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML")
    else:
        # Fallback: nuovo messaggio
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML")


def _get_next_departures(dest_codice: str, zona_codice: str, lingua: str, count: int = 3, ora_partenza: str = None, linea_codice: str = "23A") -> list:
    """
    Ritorna le prossime N partenze con orari REALI dal database.
    ora_partenza: orario HH:MM da cui partire (None = ora corrente)
    linea_codice: linea bus selezionata dall'utente
    """
    from datetime import datetime, timedelta
    import pytz

    departures = []
    rome_tz = pytz.timezone("Europe/Rome")
    now = datetime.now(rome_tz)

    # Usa orario passato o ora corrente
    if ora_partenza:
        ora_query = ora_partenza
    else:
        ora_query = now.strftime("%H:%M")

    # Usa la linea selezionata dall'utente
    bus_linea = linea_codice

    # Mappa zona -> fermata per query database
    # NOTA: fermate disponibili nel DB: Cavallino, Ca' Savio, Punta Sabbioni, Ca' Pasquali, Ca' Vio, Ca' Ballarin
    fermate_db = {
        "cavallino": "Cavallino",
        "ca_savio": "Ca' Savio",
        "punta_sabbioni": "Punta Sabbioni",
        "treporti": "Punta Sabbioni",  # Treporti non esiste, usa Punta Sabbioni (vicina)
        "ca_pasquali": "Ca' Pasquali",
        "ca_vio": "Ca' Vio",
        "ca_ballarin": "Ca' Ballarin",
        "ca_di_valle": "Ca' Ballarin",  # Ca' di Valle non esiste, usa Ca' Ballarin (vicina)
    }
    fermata_nome = fermate_db.get(zona_codice, "Punta Sabbioni")

    # Per linee composite (es. "23A + 35"), usa solo la prima
    linea_query = bus_linea.split(" + ")[0] if " + " in bus_linea else bus_linea

    # Query orari REALI dal database
    orari_db = db.get_prossimi_orari_bus(
        linea_codice=linea_query,
        fermata_nome=fermata_nome,
        direzione="andata",
        ora_partenza=ora_query,
        limit=count
    )

    if orari_db:
        # Usa orari reali dal database
        for orario in orari_db:
            ora_bus = orario.get("ora", "")
            j = _get_journey_data(dest_codice, zona_codice, lingua, linea_codice, ora_bus)

            # Parse ora partenza
            try:
                dep_time = datetime.strptime(ora_bus, "%H:%M").replace(
                    year=now.year, month=now.month, day=now.day,
                    tzinfo=rome_tz
                )
            except ValueError:
                continue

            j["bus_partenza"] = ora_bus

            # Calcola arrivo usando tempo_da_capolinea dal database
            arrivo_calcolato = db.calcola_arrivo_fermata(
                ora_partenza=ora_bus,
                linea_codice=linea_query,
                fermata_partenza=fermata_nome,
                fermata_arrivo="Punta Sabbioni" if dest_codice in ["venezia", "lido", "burano", "murano"] else fermata_nome
            )

            if arrivo_calcolato:
                j["bus_arrivo"] = arrivo_calcolato
                try:
                    bus_arrivo_dt = datetime.strptime(arrivo_calcolato, "%H:%M").replace(
                        year=now.year, month=now.month, day=now.day,
                        tzinfo=rome_tz
                    )
                except ValueError:
                    bus_arrivo_dt = dep_time + timedelta(minutes=j["bus_tempo"])
            else:
                # Fallback: usa tempo stimato
                bus_arrivo_dt = dep_time + timedelta(minutes=j["bus_tempo"])
                j["bus_arrivo"] = bus_arrivo_dt.strftime("%H:%M")

            # Calcola orari traghetto se necessario
            if j["ferry_linea"]:
                ferry_partenza = bus_arrivo_dt + timedelta(minutes=15)
                j["ferry_partenza"] = ferry_partenza.strftime("%H:%M")
                arrivo_finale = ferry_partenza + timedelta(minutes=j["ferry_tempo"])
                j["arrivo_finale"] = arrivo_finale.strftime("%H:%M")
            else:
                j["arrivo_finale"] = j["bus_arrivo"]

            departures.append(j)
    else:
        # Fallback: genera orari stimati se database vuoto
        logger.warning(f"Nessun orario trovato in DB per linea {bus_linea}, fermata {fermata_nome}")

        # Prima partenza disponibile (arrotondata ai :00 o :30)
        if start_time.minute < 30:
            next_dep = start_time.replace(minute=30, second=0, microsecond=0)
        else:
            next_dep = (start_time + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

        if next_dep < start_time:
            next_dep += timedelta(minutes=30)

        for i in range(count):
            dep_time = next_dep + timedelta(minutes=30 * i)
            dep_time_str = dep_time.strftime("%H:%M")
            j = _get_journey_data(dest_codice, zona_codice, lingua, linea_codice, dep_time_str)

            bus_tempo = j["bus_tempo"]
            bus_arrivo = dep_time + timedelta(minutes=bus_tempo)
            j["bus_arrivo"] = bus_arrivo.strftime("%H:%M")

            if j["ferry_linea"]:
                ferry_partenza = bus_arrivo + timedelta(minutes=15)
                j["ferry_partenza"] = ferry_partenza.strftime("%H:%M")
                arrivo_finale = ferry_partenza + timedelta(minutes=j["ferry_tempo"])
                j["arrivo_finale"] = arrivo_finale.strftime("%H:%M")
            else:
                j["arrivo_finale"] = j["bus_arrivo"]

            departures.append(j)

    return departures


async def handle_trasporti_orari(context, chat_id: int, lingua: str, query, destinazione_id: int, zona_id: int = None, linea_codice: str = "23A", ora_partenza: str = None):
    """
    PROSSIME PARTENZE - Lista delle prossime 3 partenze.
    Callback: tras_orari_{dest_id}_{zona_id}_{linea}_{ora}
    """
    if query:
        await query.answer()

    destinazione = db.get_destinazione_by_id(destinazione_id)
    if not destinazione:
        error = {"it": "⚠️ Destinazione non trovata.", "en": "⚠️ Destination not found.", "de": "⚠️ Ziel nicht gefunden."}
        await edit_message_safe(query, text=error.get(lingua, error["it"]))
        return

    nome_dest = destinazione.get(f"nome_{lingua}") or destinazione.get("nome_it", "")
    emoji_dest = destinazione.get("emoji", "📍")
    dest_codice = destinazione.get("codice", "venezia")

    # Recupera zona
    zona_codice = "punta_sabbioni"
    zona_nome = "Punta Sabbioni"
    if zona_id:
        zone = db.get_zone_attive()
        zona = next((z for z in zone if z.get("id") == zona_id), None)
        if zona:
            zona_nome = zona.get(f"nome_{lingua}") or zona.get("nome_it", "")
            zona_codice = zona.get("codice", "punta_sabbioni")

    # Labels
    labels = {
        "it": {"title": "Prossime partenze", "da": "Da", "linea": "Linea", "partenza": "Partenza", "arrivo": "Arrivo"},
        "en": {"title": "Next departures", "da": "From", "linea": "Line", "partenza": "Departure", "arrivo": "Arrival"},
        "de": {"title": "Nächste Abfahrten", "da": "Von", "linea": "Linie", "partenza": "Abfahrt", "arrivo": "Ankunft"}
    }
    L = labels.get(lingua, labels["it"])

    # Header
    text = f"🔄 <b>{L['title']} → {nome_dest}</b>\n"
    text += f"<i>{L['da']} {zona_nome}</i>\n"
    text += f"<i>🚌 {L['linea']} {linea_codice}</i>\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n\n"

    # Prossime 3 partenze (usa ora_partenza e linea per partire dall'orario selezionato)
    departures = _get_next_departures(dest_codice, zona_codice, lingua, 3, ora_partenza, linea_codice)

    for i, dep in enumerate(departures):
        num_emoji = ["1️⃣", "2️⃣", "3️⃣"][i]
        text += f"{num_emoji} 🚌 {dep['bus_partenza']}"
        if dep["ferry_linea"]:
            text += f" → 🚢 {dep['ferry_partenza']}"
        text += f" → 🎯 {dep['arrivo_finale']}\n"

    if departures:
        text += f"\n💰 €{departures[0]['costo']}\n"

    # Hint per selezionare partenza
    select_hint = {"it": "Seleziona una partenza:", "en": "Select a departure:", "de": "Wähle eine Abfahrt:"}
    text += f"\n<i>{select_hint.get(lingua, select_hint['it'])}</i>"
    text += "\n\n🦭 <i>SLAPPY</i>"

    buttons = []

    # Bottoni per selezionare partenza specifica (1, 2, 3) - passa anche ora e linea
    dep_buttons = []
    ora_callback = ora_partenza.replace(":", "-") if ora_partenza else "now"
    for i in range(min(3, len(departures))):
        num_label = ["1️⃣", "2️⃣", "3️⃣"][i]
        dep_buttons.append(InlineKeyboardButton(num_label, callback_data=f"tras_dep_{destinazione_id}_{zona_id or 0}_{linea_codice}_{i}_{ora_callback}"))
    buttons.append(dep_buttons)

    # Bottone indietro
    buttons.append([InlineKeyboardButton(TRASPORTI_LABELS["back_trasporti"].get(lingua, TRASPORTI_LABELS["back_trasporti"]["it"]), callback_data=f"tras_percorso_{destinazione_id}_{zona_id or 0}_{linea_codice}")])

    keyboard = InlineKeyboardMarkup(buttons)
    await edit_message_safe(query, text=text, reply_markup=keyboard)


async def handle_trasporti_dep_select(context, chat_id: int, lingua: str, query, destinazione_id: int, zona_id: int = None, linea_codice: str = "23A", dep_index: int = 0, ora_partenza: str = None):
    """
    SELEZIONE PARTENZA - Mostra dettagli della partenza selezionata (1, 2 o 3).
    Callback: tras_dep_{dest_id}_{zona_id}_{linea}_{index}_{ora}
    """
    if query:
        await query.answer()

    destinazione = db.get_destinazione_by_id(destinazione_id)
    if not destinazione:
        error = {"it": "⚠️ Destinazione non trovata.", "en": "⚠️ Destination not found.", "de": "⚠️ Ziel nicht gefunden."}
        await edit_message_safe(query, text=error.get(lingua, error["it"]))
        return

    nome_dest = destinazione.get(f"nome_{lingua}") or destinazione.get("nome_it", "")
    emoji_dest = destinazione.get("emoji", "📍")
    dest_codice = destinazione.get("codice", "venezia")

    # Recupera zona
    zona_codice = "punta_sabbioni"
    zona_nome = "Punta Sabbioni"
    if zona_id:
        zone = db.get_zone_attive()
        zona = next((z for z in zone if z.get("id") == zona_id), None)
        if zona:
            zona_nome = zona.get(f"nome_{lingua}") or zona.get("nome_it", "")
            zona_codice = zona.get("codice", "punta_sabbioni")

    # Labels
    labels = {
        "it": {"da": "Da", "linea": "Linea", "partenza": "Partenza", "arrivo": "Arrivo", "durata": "Durata", "costo": "Costo"},
        "en": {"da": "From", "linea": "Line", "partenza": "Departure", "arrivo": "Arrival", "durata": "Duration", "costo": "Cost"},
        "de": {"da": "Von", "linea": "Linie", "partenza": "Abfahrt", "arrivo": "Ankunft", "durata": "Dauer", "costo": "Kosten"}
    }
    L = labels.get(lingua, labels["it"])

    # Ottieni le 3 partenze e seleziona quella richiesta (usa ora_partenza e linea per coerenza)
    departures = _get_next_departures(dest_codice, zona_codice, lingua, 3, ora_partenza, linea_codice)
    if dep_index >= len(departures):
        dep_index = 0

    dep = departures[dep_index]
    num_emoji = ["1️⃣", "2️⃣", "3️⃣"][dep_index]

    # Header
    text = f"{num_emoji} {emoji_dest} <b>{nome_dest}</b>\n"
    text += f"<i>{L['da']} {zona_nome}</i>\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n\n"

    # Dettagli partenza
    text += f"🚌 <b>{L['partenza']}:</b> {dep['bus_partenza']}\n"
    text += f"📍 {dep['fermata']}\n\n"

    if dep["ferry_linea"]:
        text += f"🚢 <b>Traghetto {dep['ferry_linea']}:</b> {dep['ferry_partenza']}\n\n"

    # Arrivo con nome destinazione
    arrivo_label = {"it": f"Arrivo {nome_dest}", "en": f"Arrival {nome_dest}", "de": f"Ankunft {nome_dest}"}
    text += f"🎯 <b>{arrivo_label.get(lingua, arrivo_label['it'])}:</b> {dep['arrivo_finale']}\n"
    text += f"⏱️ {L['durata']}: ~{dep['bus_tempo'] + (dep['ferry_tempo'] if dep['ferry_linea'] else 0)} min\n"
    text += f"💰 {L['costo']}: €{dep['costo']}\n"

    if dep.get("note"):
        text += f"\n📝 <i>{dep['note']}</i>\n"

    text += "\n🦭 <i>SLAPPY</i>"

    # Bottoni
    buttons = []

    # Riga 1: Torna alle partenze (passa ora e linea per coerenza)
    back_orari_label = {"it": "🔄 Altre partenze", "en": "🔄 Other departures", "de": "🔄 Andere Abfahrten"}
    ora_callback = ora_partenza.replace(":", "-") if ora_partenza else "now"
    buttons.append([InlineKeyboardButton(back_orari_label.get(lingua, back_orari_label["it"]), callback_data=f"tras_orari_{destinazione_id}_{zona_id or 0}_{linea_codice}_{ora_callback}")])

    # Riga 2: Indietro al menu trasporti
    buttons.append([InlineKeyboardButton(TRASPORTI_LABELS["back_trasporti"].get(lingua, TRASPORTI_LABELS["back_trasporti"]["it"]), callback_data="tras_arrivo")])

    keyboard = InlineKeyboardMarkup(buttons)
    await edit_message_safe(query, text=text, reply_markup=keyboard)


def _get_return_journey_data(dest_codice: str, zona_codice: str, lingua: str) -> dict:
    """
    Dati journey ritorno (inverso).
    """
    from datetime import datetime, timedelta
    import pytz

    # Orari ritorno (partenze ogni 30 min dalla destinazione) - timezone Italia
    rome_tz = pytz.timezone("Europe/Rome")
    now = datetime.now(rome_tz)
    if now.minute < 30:
        next_dep = now.replace(minute=30, second=0, microsecond=0)
    else:
        next_dep = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

    next_dep_str = next_dep.strftime("%H:%M")

    # Fermate destinazione (dove prendere il ritorno)
    fermate_ritorno = {
        "venezia": {"it": "San Marco (Giardinetti)", "en": "San Marco (Giardinetti)", "de": "San Marco (Giardinetti)"},
        "lido": {"it": "Lido S.M.E.", "en": "Lido S.M.E.", "de": "Lido S.M.E."},
        "burano": {"it": "Burano (Fermata principale)", "en": "Burano (Main stop)", "de": "Burano (Haupthaltestelle)"},
        "murano": {"it": "Murano Faro", "en": "Murano Faro", "de": "Murano Faro"},
        "jesolo": {"it": "Jesolo Stazione Bus", "en": "Jesolo Bus Station", "de": "Jesolo Busbahnhof"},
        "aeroporto": {"it": "Aeroporto Marco Polo", "en": "Marco Polo Airport", "de": "Flughafen Marco Polo"}
    }

    # Dati percorso ritorno
    journeys = {
        "venezia": {"ferry_linea": "14", "ferry_tempo": 35, "bus_linea": "23", "bus_tempo": 25, "costo": "15.00", "ultimo": "23:30"},
        "lido": {"ferry_linea": "14", "ferry_tempo": 15, "bus_linea": "23", "bus_tempo": 25, "costo": "12.00", "ultimo": "23:45"},
        "burano": {"ferry_linea": "12", "ferry_tempo": 40, "bus_linea": "23", "bus_tempo": 25, "costo": "15.00", "ultimo": "21:30"},
        "murano": {"ferry_linea": "12", "ferry_tempo": 55, "bus_linea": "23", "bus_tempo": 25, "costo": "15.00", "ultimo": "22:00"},
        "jesolo": {"ferry_linea": None, "ferry_tempo": 0, "bus_linea": "23A", "bus_tempo": 45, "costo": "4.50", "ultimo": "22:30"},
        "aeroporto": {"ferry_linea": None, "ferry_tempo": 0, "bus_linea": "35 + 23A", "bus_tempo": 90, "costo": "12.00", "ultimo": "21:00"}
    }

    journey = journeys.get(dest_codice, journeys["venezia"])
    fermata = fermate_ritorno.get(dest_codice, fermate_ritorno["venezia"])

    # Calcola orari ritorno
    if journey["ferry_linea"]:
        # Parte con traghetto, poi bus
        ferry_arrivo_ps = next_dep + timedelta(minutes=journey["ferry_tempo"])
        ferry_arrivo_ps_str = ferry_arrivo_ps.strftime("%H:%M")
        bus_partenza = ferry_arrivo_ps + timedelta(minutes=10)
        bus_partenza_str = bus_partenza.strftime("%H:%M")
        arrivo_finale = bus_partenza + timedelta(minutes=journey["bus_tempo"])
        arrivo_finale_str = arrivo_finale.strftime("%H:%M")
    else:
        # Solo bus
        ferry_arrivo_ps_str = None
        bus_partenza_str = next_dep_str
        arrivo_finale = next_dep + timedelta(minutes=journey["bus_tempo"])
        arrivo_finale_str = arrivo_finale.strftime("%H:%M")

    return {
        "fermata": fermata.get(lingua, fermata["it"]),
        "ferry_linea": journey["ferry_linea"],
        "ferry_partenza": next_dep_str if journey["ferry_linea"] else None,
        "ferry_tempo": journey["ferry_tempo"],
        "ferry_arrivo_ps": ferry_arrivo_ps_str if journey["ferry_linea"] else None,
        "bus_linea": journey["bus_linea"],
        "bus_partenza": bus_partenza_str if journey["ferry_linea"] else next_dep_str,
        "bus_tempo": journey["bus_tempo"],
        "arrivo_finale": arrivo_finale_str,
        "costo": journey["costo"],
        "ultimo_ritorno": journey["ultimo"]
    }


async def handle_trasporti_ritorno(context, chat_id: int, lingua: str, query, destinazione_id: int, zona_id: int = None):
    """
    RITORNO - Journey planner per il viaggio di ritorno.
    Callback: tras_ritorno_{dest_id}_{zona_id}
    """
    if query:
        await query.answer()

    destinazione = db.get_destinazione_by_id(destinazione_id)
    if not destinazione:
        error = {"it": "⚠️ Destinazione non trovata.", "en": "⚠️ Destination not found.", "de": "⚠️ Ziel nicht gefunden."}
        await edit_message_safe(query, text=error.get(lingua, error["it"]))
        return

    nome_dest = destinazione.get(f"nome_{lingua}") or destinazione.get("nome_it", "")
    emoji_dest = destinazione.get("emoji", "📍")
    dest_codice = destinazione.get("codice", "venezia")

    # Recupera zona
    zona_codice = "punta_sabbioni"
    zona_nome = "Punta Sabbioni"
    if zona_id:
        zone = db.get_zone_attive()
        zona = next((z for z in zone if z.get("id") == zona_id), None)
        if zona:
            zona_nome = zona.get(f"nome_{lingua}") or zona.get("nome_it", "")
            zona_codice = zona.get("codice", "punta_sabbioni")

    # Dati ritorno
    r = _get_return_journey_data(dest_codice, zona_codice, lingua)

    # Labels multilingua
    labels = {
        "it": {
            "verso": "Verso",
            "prossima": "Prossima partenza",
            "fermata": "Fermata",
            "traghetto": "Traghetto linea",
            "arrivo_ps": "Arrivo Punta Sabbioni",
            "bus": "Bus linea",
            "arrivo": "Arrivo",
            "costo": "Costo totale",
            "ultimo": "Ultimo ritorno",
            "min": "min"
        },
        "en": {
            "verso": "To",
            "prossima": "Next departure",
            "fermata": "Stop",
            "traghetto": "Ferry line",
            "arrivo_ps": "Arrival Punta Sabbioni",
            "bus": "Bus line",
            "arrivo": "Arrival",
            "costo": "Total cost",
            "ultimo": "Last return",
            "min": "min"
        },
        "de": {
            "verso": "Nach",
            "prossima": "Nächste Abfahrt",
            "fermata": "Haltestelle",
            "traghetto": "Fähre Linie",
            "arrivo_ps": "Ankunft Punta Sabbioni",
            "bus": "Bus Linie",
            "arrivo": "Ankunft",
            "costo": "Gesamtkosten",
            "ultimo": "Letzte Rückfahrt",
            "min": "Min"
        }
    }
    L = labels.get(lingua, labels["it"])

    # Header
    text = f"🔙 <b>Ritorno da {nome_dest}</b>\n"
    text += f"<i>{L['verso']} {zona_nome}</i>\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n\n"

    # TRAGHETTO (se presente)
    if r["ferry_linea"]:
        text += f"🚢 <b>{L['prossima']}:</b> {r['ferry_partenza']}\n"
        text += f"📍 <b>{L['fermata']}:</b> {r['fermata']}\n"
        text += f"🏁 <b>{L['arrivo_ps']}:</b> {r['ferry_arrivo_ps']}\n\n"
        text += f"🚌 <b>{L['bus']} {r['bus_linea']}:</b> {r['bus_partenza']}\n"
    else:
        text += f"🚌 <b>{L['prossima']}:</b> {r['bus_partenza']}\n"
        text += f"📍 <b>{L['fermata']}:</b> {r['fermata']}\n"

    text += f"🎯 <b>{L['arrivo']} {zona_nome}:</b> {r['arrivo_finale']}\n\n"

    text += f"💰 <b>{L['costo']}:</b> €{r['costo']}\n"
    text += f"⚠️ <b>{L['ultimo']}:</b> {r['ultimo_ritorno']}\n"

    text += "\n🦭 <i>SLAPPY</i>"

    buttons = []

    # Bottone indietro al percorso andata
    back_andata = {"it": "◀️ Andata", "en": "◀️ Outbound", "de": "◀️ Hinfahrt"}
    buttons.append([InlineKeyboardButton(back_andata.get(lingua, back_andata["it"]), callback_data=f"tras_percorso_{destinazione_id}_{zona_id or 0}")])

    # Bottone indietro al menu
    buttons.append([InlineKeyboardButton(TRASPORTI_LABELS["back_trasporti"].get(lingua, TRASPORTI_LABELS["back_trasporti"]["it"]), callback_data=f"tras_dest_{destinazione_id}")])

    keyboard = InlineKeyboardMarkup(buttons)
    await edit_message_safe(query, text=text, reply_markup=keyboard)


async def handle_trasporti_bus(context, chat_id: int, lingua: str, query):
    """
    ORARI BUS ATVO - Lista linee bus.
    Callback: tras_bus
    """
    if query:
        await query.answer()

    titoli = {
        "it": "🚌 <b>Autobus ATVO</b>",
        "en": "🚌 <b>ATVO Buses</b>",
        "de": "🚌 <b>ATVO Busse</b>"
    }

    text = titoli.get(lingua, titoli["it"]) + "\n\n"

    linee = db.get_linee_by_tipo("bus")

    if not linee:
        text += TRASPORTI_LABELS["no_lines"].get(lingua, TRASPORTI_LABELS["no_lines"]["it"])
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(TRASPORTI_LABELS["back_trasporti"].get(lingua, TRASPORTI_LABELS["back_trasporti"]["it"]), callback_data="tras_home")]
        ])
    else:
        buttons = []
        for linea in linee:
            codice = linea.get("codice", "")
            nome = linea.get(f"nome_{lingua}") or linea.get("nome_it", "")
            linea_id = linea.get("id")

            btn_text = f"🚌 Linea {codice}"
            if nome and len(nome) < 30:
                btn_text += f" - {nome}"

            buttons.append([InlineKeyboardButton(btn_text, callback_data=f"tras_bus_linea_{linea_id}")])

        # Link al sito ATVO
        operatori = db.get_operatori_attivi("bus")
        if operatori and operatori[0].get("sito_web"):
            buttons.append([InlineKeyboardButton("🔗 Sito ATVO", url=operatori[0]["sito_web"])])

        buttons.append([InlineKeyboardButton(TRASPORTI_LABELS["back_trasporti"].get(lingua, TRASPORTI_LABELS["back_trasporti"]["it"]), callback_data="tras_home")])
        keyboard = InlineKeyboardMarkup(buttons)

    await edit_message_safe(query, text=text, reply_markup=keyboard)


async def handle_trasporti_ferry(context, chat_id: int, lingua: str, query):
    """
    ORARI TRAGHETTI ACTV - Lista linee vaporetto.
    Callback: tras_ferry
    """
    if query:
        await query.answer()

    titoli = {
        "it": "🚢 <b>Vaporetti ACTV</b>",
        "en": "🚢 <b>ACTV Ferries</b>",
        "de": "🚢 <b>ACTV Fähren</b>"
    }

    text = titoli.get(lingua, titoli["it"]) + "\n\n"

    linee = db.get_linee_by_tipo("traghetto")

    if not linee:
        text += TRASPORTI_LABELS["no_lines"].get(lingua, TRASPORTI_LABELS["no_lines"]["it"])
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(TRASPORTI_LABELS["back_trasporti"].get(lingua, TRASPORTI_LABELS["back_trasporti"]["it"]), callback_data="tras_home")]
        ])
    else:
        buttons = []
        for linea in linee:
            codice = linea.get("codice", "")
            nome = linea.get(f"nome_{lingua}") or linea.get("nome_it", "")
            linea_id = linea.get("id")

            btn_text = f"🚢 Linea {codice}"
            if nome and len(nome) < 30:
                btn_text += f" - {nome}"

            buttons.append([InlineKeyboardButton(btn_text, callback_data=f"tras_ferry_linea_{linea_id}")])

        # Link al sito ACTV
        operatori = db.get_operatori_attivi("traghetto")
        if operatori and operatori[0].get("sito_web"):
            buttons.append([InlineKeyboardButton("🔗 Sito ACTV", url=operatori[0]["sito_web"])])

        buttons.append([InlineKeyboardButton(TRASPORTI_LABELS["back_trasporti"].get(lingua, TRASPORTI_LABELS["back_trasporti"]["it"]), callback_data="tras_home")])
        keyboard = InlineKeyboardMarkup(buttons)

    await edit_message_safe(query, text=text, reply_markup=keyboard)


# ============================================================
# HANDLER TRAGHETTI ACTV PER DESTINAZIONE
# ============================================================

async def handle_trasporti_ferry_destinazione(context, chat_id: int, lingua: str, query, dest_key: str):
    """
    Mostra info e orari per una destinazione traghetto.
    Callback: tras_ferry_dest_{dest_key}
    dest_key: venezia_sm, venezia_fn, isole, lido
    """
    if query:
        await query.answer()

    dest = FERRY_DESTINATIONS.get(dest_key)
    if not dest:
        error = {"it": "⚠️ Destinazione non trovata.", "en": "⚠️ Destination not found.", "de": "⚠️ Ziel nicht gefunden."}
        await edit_message_safe(query, text=error.get(lingua, error["it"]))
        return

    nome = dest["nome"].get(lingua, dest["nome"]["it"])
    sottotitolo = dest["sottotitolo"].get(lingua, dest["sottotitolo"]["it"])
    emoji = dest["emoji"]
    linea = dest["linea"]
    partenza = dest["partenza"]
    durata = dest["durata"]

    # Labels
    labels = {
        "it": {"linea": "Linea", "partenza": "Partenza", "durata": "Durata", "prossime": "Prossime partenze", "min": "min"},
        "en": {"linea": "Line", "partenza": "Departure", "durata": "Duration", "prossime": "Next departures", "min": "min"},
        "de": {"linea": "Linie", "partenza": "Abfahrt", "durata": "Dauer", "prossime": "Nächste Abfahrten", "min": "Min"}
    }
    L = labels.get(lingua, labels["it"])

    # Header
    text = f"{emoji} <b>{nome}</b>\n"
    text += f"<i>{sottotitolo}</i>\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n\n"

    text += f"🚢 <b>{L['linea']}:</b> {linea} ACTV\n"
    text += f"📍 <b>{L['partenza']}:</b> {partenza}\n"
    text += f"⏱️ <b>{L['durata']}:</b> ~{durata} {L['min']}\n\n"

    # Prossimi orari dal database
    from datetime import datetime
    import pytz
    rome_tz = pytz.timezone("Europe/Rome")
    now = datetime.now(rome_tz)
    ora_corrente = now.strftime("%H:%M")

    orari = db.get_orari_traghetto(
        linea_codice=linea,
        fermata_nome=partenza,
        direzione="andata",
        ora_partenza=ora_corrente,
        limit=4
    )

    text += f"🕐 <b>{L['prossime']}:</b>\n"
    if orari:
        for orario in orari[:4]:
            ora = orario.get("ora", "")[:5]
            text += f"   • {ora}\n"
    else:
        no_orari = {"it": "Nessun orario disponibile", "en": "No schedules available", "de": "Keine Fahrpläne verfügbar"}
        text += f"   <i>{no_orari.get(lingua, no_orari['it'])}</i>\n"

    # Note speciali per Torcello
    if dest_key == "isole":
        torcello_note = {
            "it": "\n⚠️ <b>Torcello:</b> Fermata a richiesta\nChiama 800 845 065 (20 min prima)",
            "en": "\n⚠️ <b>Torcello:</b> Request stop\nCall 800 845 065 (20 min before)",
            "de": "\n⚠️ <b>Torcello:</b> Bedarfshalt\nAnruf 800 845 065 (20 Min vorher)"
        }
        text += torcello_note.get(lingua, torcello_note["it"])

    # Linea 15 per San Marco (solo feriale)
    if dest_key == "venezia_sm":
        linea15_note = {
            "it": "\n\n💡 <b>Linea 15:</b> Collegamento diretto (solo Lun-Sab)",
            "en": "\n\n💡 <b>Line 15:</b> Direct connection (Mon-Sat only)",
            "de": "\n\n💡 <b>Linie 15:</b> Direktverbindung (nur Mo-Sa)"
        }
        text += linea15_note.get(lingua, linea15_note["it"])

    text += "\n\n🦭 <i>SLAPPY</i>"

    # Bottoni
    buttons = []

    # Tutti gli orari
    orari_btn = {"it": "📋 Tutti gli orari", "en": "📋 All schedules", "de": "📋 Alle Fahrpläne"}
    buttons.append([InlineKeyboardButton(orari_btn.get(lingua, orari_btn["it"]), callback_data=f"tras_ferry_orari_{dest_key}_andata")])

    # Orari ritorno
    ritorno_btn = {"it": "🔙 Orari ritorno", "en": "🔙 Return schedules", "de": "🔙 Rückfahrpläne"}
    buttons.append([InlineKeyboardButton(ritorno_btn.get(lingua, ritorno_btn["it"]), callback_data=f"tras_ferry_orari_{dest_key}_ritorno")])

    # Info biglietti
    info_btn = {"it": "🎫 Biglietti e info", "en": "🎫 Tickets & info", "de": "🎫 Tickets & Info"}
    buttons.append([InlineKeyboardButton(info_btn.get(lingua, info_btn["it"]), callback_data=f"tras_ferry_info_{dest_key}")])

    # Indietro
    buttons.append([InlineKeyboardButton(TRASPORTI_LABELS["back_trasporti"].get(lingua, TRASPORTI_LABELS["back_trasporti"]["it"]), callback_data="tras_arrivo")])

    keyboard = InlineKeyboardMarkup(buttons)
    await edit_message_safe(query, text=text, reply_markup=keyboard)


async def handle_trasporti_ferry_orari(context, chat_id: int, lingua: str, query, dest_key: str, direzione: str = "andata"):
    """
    Mostra lista completa orari traghetto.
    Callback: tras_ferry_orari_{dest_key}_{direzione}
    """
    if query:
        await query.answer()

    dest = FERRY_DESTINATIONS.get(dest_key)
    if not dest:
        error = {"it": "⚠️ Destinazione non trovata.", "en": "⚠️ Destination not found.", "de": "⚠️ Ziel nicht gefunden."}
        await edit_message_safe(query, text=error.get(lingua, error["it"]))
        return

    nome = dest["nome"].get(lingua, dest["nome"]["it"])
    emoji = dest["emoji"]
    linea = dest["linea"]

    # Per il ritorno, inverti partenza/arrivo
    if direzione == "ritorno":
        fermata_query = dest["arrivo"]
        da_label = dest["arrivo"]
        a_label = dest["partenza"]
    else:
        fermata_query = dest["partenza"]
        da_label = dest["partenza"]
        a_label = dest["arrivo"]

    # Labels
    labels = {
        "it": {"andata": "Andata", "ritorno": "Ritorno", "da": "Da", "a": "A", "linea": "Linea", "orari": "Orari"},
        "en": {"andata": "Outbound", "ritorno": "Return", "da": "From", "a": "To", "linea": "Line", "orari": "Schedules"},
        "de": {"andata": "Hinfahrt", "ritorno": "Rückfahrt", "da": "Von", "a": "Nach", "linea": "Linie", "orari": "Fahrplan"}
    }
    L = labels.get(lingua, labels["it"])

    dir_label = L["andata"] if direzione == "andata" else L["ritorno"]

    # Header
    text = f"{emoji} <b>{nome}</b>\n"
    text += f"🚢 {L['linea']} {linea} - {dir_label}\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n\n"
    text += f"📍 {L['da']}: <b>{da_label}</b>\n"
    text += f"🎯 {L['a']}: <b>{a_label}</b>\n\n"

    # Query orari (tutti, non solo da ora corrente)
    orari = db.get_orari_traghetto(
        linea_codice=linea,
        fermata_nome=fermata_query,
        direzione=direzione,
        ora_partenza=None,
        limit=50
    )

    text += f"🕐 <b>{L['orari']}:</b>\n"
    if orari:
        # Raggruppa per fascia oraria
        orari_str = []
        for orario in orari:
            ora = orario.get("ora", "")[:5]
            tipo = orario.get("tipo_giorno", "fF")
            if tipo == "f":
                ora += " ⚡"
            orari_str.append(ora)

        # Mostra in righe di 4
        for i in range(0, len(orari_str), 4):
            chunk = orari_str[i:i+4]
            text += "   " + "  •  ".join(chunk) + "\n"

        # Legenda
        legenda = {"it": "\n⚡ = solo feriale (Lun-Sab)", "en": "\n⚡ = weekdays only (Mon-Sat)", "de": "\n⚡ = nur werktags (Mo-Sa)"}
        if any("⚡" in o for o in orari_str):
            text += legenda.get(lingua, legenda["it"])
    else:
        no_orari = {"it": "Nessun orario disponibile", "en": "No schedules available", "de": "Keine Fahrpläne verfügbar"}
        text += f"   <i>{no_orari.get(lingua, no_orari['it'])}</i>\n"

    text += "\n\n🦭 <i>SLAPPY</i>"

    # Bottoni
    buttons = []

    # Toggle andata/ritorno
    if direzione == "andata":
        toggle_btn = {"it": "🔙 Vedi ritorno", "en": "🔙 See return", "de": "🔙 Rückfahrt anzeigen"}
        buttons.append([InlineKeyboardButton(toggle_btn.get(lingua, toggle_btn["it"]), callback_data=f"tras_ferry_orari_{dest_key}_ritorno")])
    else:
        toggle_btn = {"it": "➡️ Vedi andata", "en": "➡️ See outbound", "de": "➡️ Hinfahrt anzeigen"}
        buttons.append([InlineKeyboardButton(toggle_btn.get(lingua, toggle_btn["it"]), callback_data=f"tras_ferry_orari_{dest_key}_andata")])

    # Torna a destinazione
    back_dest = {"it": "◀️ Torna", "en": "◀️ Back", "de": "◀️ Zurück"}
    buttons.append([InlineKeyboardButton(back_dest.get(lingua, back_dest["it"]), callback_data=f"tras_ferry_dest_{dest_key}")])

    keyboard = InlineKeyboardMarkup(buttons)
    await edit_message_safe(query, text=text, reply_markup=keyboard)


async def handle_trasporti_ferry_info(context, chat_id: int, lingua: str, query, dest_key: str):
    """
    Info biglietti e tariffe ACTV.
    Callback: tras_ferry_info_{dest_key}
    """
    if query:
        await query.answer()

    dest = FERRY_DESTINATIONS.get(dest_key)
    if not dest:
        error = {"it": "⚠️ Destinazione non trovata.", "en": "⚠️ Destination not found.", "de": "⚠️ Ziel nicht gefunden."}
        await edit_message_safe(query, text=error.get(lingua, error["it"]))
        return

    nome = dest["nome"].get(lingua, dest["nome"]["it"])
    emoji = dest["emoji"]

    # Info tariffe ACTV
    info_text = {
        "it": f"""{emoji} <b>{nome}</b>
━━━━━━━━━━━━━━━━━━━━

🎫 <b>Biglietti ACTV</b>

💰 <b>Corsa singola:</b> €9,50
   Valido 75 minuti

💰 <b>Andata e ritorno:</b> €19,00
   Valido stesso giorno

💰 <b>Abbonamento giornaliero:</b> €25,00
   Illimitato per 24 ore

🏷️ <b>Riduzioni:</b>
   • Bambini 6-14 anni: 50%
   • Under 6: gratis
   • Residenti Veneto: tariffe agevolate

📍 <b>Dove comprare:</b>
   • Biglietterie ACTV (Punta Sabbioni, Treporti)
   • Edicole e tabacchi autorizzati
   • App AVM Venezia
   • A bordo (+€5 sovrapprezzo)

⚠️ <b>Importante:</b>
   Validare SEMPRE il biglietto prima di salire!

🔗 <b>Sito ufficiale:</b>
   actv.avmspa.it""",

        "en": f"""{emoji} <b>{nome}</b>
━━━━━━━━━━━━━━━━━━━━

🎫 <b>ACTV Tickets</b>

💰 <b>Single ride:</b> €9.50
   Valid 75 minutes

💰 <b>Round trip:</b> €19.00
   Valid same day

💰 <b>Day pass:</b> €25.00
   Unlimited for 24 hours

🏷️ <b>Discounts:</b>
   • Children 6-14: 50%
   • Under 6: free
   • Veneto residents: reduced rates

📍 <b>Where to buy:</b>
   • ACTV ticket offices (Punta Sabbioni, Treporti)
   • Authorized newsagents and tobacconists
   • AVM Venezia app
   • On board (+€5 surcharge)

⚠️ <b>Important:</b>
   ALWAYS validate your ticket before boarding!

🔗 <b>Official website:</b>
   actv.avmspa.it""",

        "de": f"""{emoji} <b>{nome}</b>
━━━━━━━━━━━━━━━━━━━━

🎫 <b>ACTV Fahrkarten</b>

💰 <b>Einzelfahrt:</b> €9,50
   Gültig 75 Minuten

💰 <b>Hin und zurück:</b> €19,00
   Gültig am selben Tag

💰 <b>Tageskarte:</b> €25,00
   Unbegrenzt für 24 Stunden

🏷️ <b>Ermäßigungen:</b>
   • Kinder 6-14 Jahre: 50%
   • Unter 6: kostenlos
   • Einwohner Venetien: ermäßigte Tarife

📍 <b>Wo kaufen:</b>
   • ACTV Fahrkartenschalter (Punta Sabbioni, Treporti)
   • Autorisierte Kioske und Tabakläden
   • AVM Venezia App
   • An Bord (+€5 Zuschlag)

⚠️ <b>Wichtig:</b>
   Fahrkarte IMMER vor dem Einsteigen entwerten!

🔗 <b>Offizielle Website:</b>
   actv.avmspa.it"""
    }

    text = info_text.get(lingua, info_text["it"])
    text += "\n\n🦭 <i>SLAPPY</i>"

    # Bottoni
    buttons = []

    # Link sito ACTV
    buttons.append([InlineKeyboardButton("🔗 Sito ACTV", url="https://actv.avmspa.it")])

    # Torna a destinazione
    back_dest = {"it": "◀️ Torna", "en": "◀️ Back", "de": "◀️ Zurück"}
    buttons.append([InlineKeyboardButton(back_dest.get(lingua, back_dest["it"]), callback_data=f"tras_ferry_dest_{dest_key}")])

    keyboard = InlineKeyboardMarkup(buttons)
    await edit_message_safe(query, text=text, reply_markup=keyboard)


async def handle_trasporti_linea(context, chat_id: int, lingua: str, query, linea_id: int, tipo: str = "bus"):
    """
    DETTAGLIO LINEA - Info complete su una linea bus/ferry.
    Callback: tras_bus_linea_{id} o tras_ferry_linea_{id}
    """
    if query:
        await query.answer()

    linea = db.get_linea_by_id(linea_id)
    if not linea:
        error = {"it": "⚠️ Linea non trovata.", "en": "⚠️ Line not found.", "de": "⚠️ Linie nicht gefunden."}
        await edit_message_safe(query, text=error.get(lingua, error["it"]))
        return

    emoji = OPERATORE_EMOJI.get(tipo, "🚌")
    codice = linea.get("codice", "")
    nome = linea.get(f"nome_{lingua}") or linea.get("nome_it", "")
    note = linea.get(f"note_{lingua}") or linea.get("note_it", "")
    durata = linea.get("durata_minuti", "")
    frequenza = linea.get("frequenza_minuti", "")

    text = f"{emoji} <b>Linea {codice}</b>\n"
    if nome:
        text += f"<i>{nome}</i>\n\n"

    if durata:
        dur_label = TRASPORTI_LABELS["duration"].get(lingua, TRASPORTI_LABELS["duration"]["it"])
        min_label = TRASPORTI_LABELS["minutes"].get(lingua, TRASPORTI_LABELS["minutes"]["it"])
        text += f"⏱️ {dur_label}: ~{durata} {min_label}\n"

    if frequenza:
        freq_label = {"it": "Frequenza", "en": "Frequency", "de": "Frequenz"}
        text += f"🔄 {freq_label.get(lingua, freq_label['it'])}: ogni {frequenza} min\n"

    if note:
        text += f"\n📝 {note}\n"

    text += "\n🦭 <i>SLAPPY</i>"

    buttons = []

    # Link sito operatore
    if linea.get("operatori", {}).get("sito_web"):
        op_name = linea["operatori"].get("nome", "Sito")
        buttons.append([InlineKeyboardButton(f"🔗 {op_name}", url=linea["operatori"]["sito_web"])])

    back_callback = "tras_bus" if tipo == "bus" else "tras_ferry"
    back_label = {"it": "◀️ Linee", "en": "◀️ Lines", "de": "◀️ Linien"}
    buttons.append([InlineKeyboardButton(back_label.get(lingua, back_label["it"]), callback_data=back_callback)])

    keyboard = InlineKeyboardMarkup(buttons)
    await edit_message_safe(query, text=text, reply_markup=keyboard)


async def handle_trasporti_prezzi(context, chat_id: int, lingua: str, query):
    """
    BIGLIETTI E PREZZI - Mostra operatori e link a tariffe.
    Callback: tras_prezzi
    """
    if query:
        await query.answer()

    titoli = {
        "it": "🎫 <b>Biglietti e Prezzi</b>",
        "en": "🎫 <b>Tickets & Prices</b>",
        "de": "🎫 <b>Tickets & Preise</b>"
    }

    sottotitoli = {
        "it": "Seleziona l'operatore:",
        "en": "Select the operator:",
        "de": "Wählen Sie den Betreiber:"
    }

    text = titoli.get(lingua, titoli["it"]) + "\n"
    text += f"<i>{sottotitoli.get(lingua, sottotitoli['it'])}</i>\n"

    operatori = db.get_operatori_attivi()

    if not operatori:
        no_op = {"it": "Informazioni non disponibili.", "en": "Information not available.", "de": "Informationen nicht verfügbar."}
        text += f"\n{no_op.get(lingua, no_op['it'])}"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(TRASPORTI_LABELS["back_trasporti"].get(lingua, TRASPORTI_LABELS["back_trasporti"]["it"]), callback_data="tras_home")]
        ])
    else:
        buttons = []
        for op in operatori:
            nome = op.get("nome", "")
            tipo = op.get("tipo", "")
            op_id = op.get("id")
            emoji = OPERATORE_EMOJI.get(tipo, "🎫")

            buttons.append([InlineKeyboardButton(f"{emoji} {nome}", callback_data=f"tras_prezzi_op_{op_id}")])

        buttons.append([InlineKeyboardButton(TRASPORTI_LABELS["back_trasporti"].get(lingua, TRASPORTI_LABELS["back_trasporti"]["it"]), callback_data="tras_home")])
        keyboard = InlineKeyboardMarkup(buttons)

    await edit_message_safe(query, text=text, reply_markup=keyboard)


async def handle_trasporti_prezzi_operatore(context, chat_id: int, lingua: str, query, operatore_id: int):
    """
    TARIFFE OPERATORE - Dettaglio prezzi di un operatore.
    Callback: tras_prezzi_op_{id}
    """
    if query:
        await query.answer()

    operatore = db.get_operatore_by_id(operatore_id)
    if not operatore:
        error = {"it": "⚠️ Operatore non trovato.", "en": "⚠️ Operator not found.", "de": "⚠️ Betreiber nicht gefunden."}
        await edit_message_safe(query, text=error.get(lingua, error["it"]))
        return

    nome = operatore.get("nome", "")
    tipo = operatore.get("tipo", "")
    link = operatore.get("sito_web", "")
    telefono = operatore.get("telefono", "")
    emoji = OPERATORE_EMOJI.get(tipo, "🎫")

    text = f"{emoji} <b>{nome}</b>\n\n"

    tariffe = db.get_tariffe_by_operatore(operatore_id)

    if tariffe:
        for tariffa in tariffe:
            nome_tariffa = tariffa.get(f"nome_{lingua}") or tariffa.get("nome_it", tariffa.get("tipo", ""))
            prezzo = tariffa.get("prezzo", "")
            note = tariffa.get(f"note_{lingua}") or tariffa.get("note_it", "")

            text += f"💰 <b>{nome_tariffa}</b>: €{prezzo}"
            if note:
                text += f"\n   <i>{note}</i>"
            text += "\n"
    else:
        no_tariffe = {"it": "Consulta il sito per le tariffe aggiornate.", "en": "Check the website for current fares.", "de": "Aktuelle Tarife auf der Website."}
        text += no_tariffe.get(lingua, no_tariffe["it"]) + "\n"

    if telefono:
        text += f"\n📞 {telefono}\n"

    text += "\n🦭 <i>SLAPPY</i>"

    buttons = []
    if link:
        tariffe_label = {"it": "🔗 Tariffe complete", "en": "🔗 Full fares", "de": "🔗 Alle Tarife"}
        buttons.append([InlineKeyboardButton(tariffe_label.get(lingua, tariffe_label["it"]), url=link)])

    buttons.append([InlineKeyboardButton(TRASPORTI_LABELS["back_trasporti"].get(lingua, TRASPORTI_LABELS["back_trasporti"]["it"]), callback_data="tras_prezzi")])

    keyboard = InlineKeyboardMarkup(buttons)
    await edit_message_safe(query, text=text, reply_markup=keyboard)


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
    Numeri di emergenza - testo copiabile, numeri cliccabili.
    """
    # Rispondi al callback SUBITO
    if query:
        await query.answer()

    # I numeri nel testo sono automaticamente cliccabili in Telegram
    texts = {
        "it": """🆘 <b>EMERGENZA</b>

Tocca il numero per chiamare:

🆘 <b>112</b> - Emergenze (Europeo)
🚑 <b>118</b> - Ambulanza
🚒 <b>115</b> - Vigili del Fuoco
⚓ <b>1530</b> - Guardia Costiera""",
        "en": """🆘 <b>EMERGENCY</b>

Tap number to call:

🆘 <b>112</b> - Emergency (European)
🚑 <b>118</b> - Ambulance
🚒 <b>115</b> - Fire Department
⚓ <b>1530</b> - Coast Guard""",
        "de": """🆘 <b>NOTFALL</b>

Nummer antippen zum Anrufen:

🆘 <b>112</b> - Notruf (Europäisch)
🚑 <b>118</b> - Krankenwagen
🚒 <b>115</b> - Feuerwehr
⚓ <b>1530</b> - Küstenwache"""
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

🕐 <b>Quando chiamare:</b>
• Notti feriali: 20:00 - 08:00
• Weekend: sab 10:00 → lun 08:00
• Festivi: tutto il giorno

Per urgenze <b>NON</b> gravi (no 118)

📞 Chiama: <b>116117</b>""",
        "en": """🩺 <b>MEDICAL GUARD</b>

🕐 <b>When to call:</b>
• Weeknights: 8pm - 8am
• Weekends: Sat 10am → Mon 8am
• Holidays: all day

For <b>NON</b>-serious emergencies (not 118)

📞 Call: <b>116117</b>""",
        "de": """🩺 <b>BEREITSCHAFTSARZT</b>

🕐 <b>Wann anrufen:</b>
• Wochennächte: 20:00 - 08:00
• Wochenende: Sa 10:00 → Mo 08:00
• Feiertage: ganztägig

Für <b>NICHT</b> schwere Notfälle (nicht 118)

📞 Anrufen: <b>116117</b>"""
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
🕐 Estate H24 • Inverno 8-20
📞 <b>041 5300214</b>

<b>PS Jesolo</b>
🕐 Aperto H24
📞 <b>0421 388111</b>""",
        "en": """🏥 <b>HOSPITALS</b>

<b>First Aid Ca' Savio</b>
🕐 Summer 24/7 • Winter 8am-8pm
📞 <b>041 5300214</b>

<b>ER Jesolo</b>
🕐 Open 24/7
📞 <b>0421 388111</b>""",
        "de": """🏥 <b>KRANKENHÄUSER</b>

<b>Erste Hilfe Ca' Savio</b>
🕐 Sommer 24h • Winter 8-20
📞 <b>041 5300214</b>

<b>Notaufnahme Jesolo</b>
🕐 24h geöffnet
📞 <b>0421 388111</b>"""
    }
    text = texts.get(lingua, texts["it"])

    btn_back = {"it": "⬅️ Indietro", "en": "⬅️ Back", "de": "⬅️ Zurück"}.get(lingua, "⬅️ Indietro")

    # Bottoni navigazione
    btn_nav_ppi = {"it": "📍 PPI Ca' Savio", "en": "📍 First Aid Ca' Savio", "de": "📍 Erste Hilfe Ca' Savio"}.get(lingua, "📍 PPI Ca' Savio")
    btn_nav_ps = {"it": "📍 PS Jesolo", "en": "📍 ER Jesolo", "de": "📍 Notaufnahme Jesolo"}.get(lingua, "📍 PS Jesolo")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(btn_nav_ppi, url="https://www.google.com/maps/dir/?api=1&destination=45.4477,12.4847")],
        [InlineKeyboardButton(btn_nav_ps, url="https://www.google.com/maps/dir/?api=1&destination=45.5089,12.6463")],
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

        # Limita a max 3 farmacie per non appesantire i bottoni
        farmacie_mostrate = farmacie[:3]

        btn_nav_label = {"it": "📍 Naviga", "en": "📍 Navigate", "de": "📍 Navigation"}.get(lingua, "📍 Naviga")

        keyboard_rows = []
        for i, f in enumerate(farmacie_mostrate):
            text += f"\n<b>{i+1}. {f.nome}</b>\n"
            if f.indirizzo:
                text += f"📍 {f.indirizzo}\n"
            if f.orario:
                text += f"🕐 {f.orario}\n"
            if f.telefono:
                text += f"📞 <b>{f.telefono}</b>\n"

            # Bottone Naviga per ogni farmacia
            maps_url = get_maps_url(f)
            keyboard_rows.append([InlineKeyboardButton(f"{i+1}. {btn_nav_label}", url=maps_url)])

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
