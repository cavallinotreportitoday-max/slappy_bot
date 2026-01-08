"""
Database Supabase - connessione, cache e query
"""
import time
import logging
from typing import Optional, Dict, Any
from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY, CACHE_TTL

logger = logging.getLogger(__name__)

# Client Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Cache globale
_cache = {
    "testi": {},
    "config": {},
    "eventi_oggi": None,
    "eventi_oggi_data": None,
    "testi_loaded_at": 0,
    "config_loaded_at": 0,
    "eventi_loaded_at": 0
}


def _is_cache_valid(cache_key: str) -> bool:
    """Verifica se la cache è ancora valida"""
    loaded_at = _cache.get(f"{cache_key}_loaded_at", 0)
    return (time.time() - loaded_at) < CACHE_TTL


def get_testi() -> Dict[str, Dict[str, str]]:
    """Carica testi dal DB con cache 5 minuti"""
    if _is_cache_valid("testi") and _cache["testi"]:
        return _cache["testi"]

    try:
        response = supabase.table("testi").select("*").execute()
        T = {}
        for row in response.data:
            if row.get("chiave"):
                T[row["chiave"]] = {
                    "it": row.get("it", ""),
                    "en": row.get("en", ""),
                    "de": row.get("de", "")
                }
        _cache["testi"] = T
        _cache["testi_loaded_at"] = time.time()
        logger.info(f"Cache testi ricaricata: {len(T)} chiavi")
        return T
    except Exception as e:
        logger.error(f"Errore caricamento testi: {e}")
        return _cache.get("testi", {})


def get_config() -> Dict[str, str]:
    """Carica config dal DB con cache 5 minuti"""
    if _is_cache_valid("config") and _cache["config"]:
        return _cache["config"]

    try:
        response = supabase.table("config").select("*").execute()
        config = {}
        for row in response.data:
            if row.get("chiave"):
                config[row["chiave"]] = row.get("valore", "")
        _cache["config"] = config
        _cache["config_loaded_at"] = time.time()
        logger.info(f"Cache config ricaricata: {len(config)} chiavi")
        return config
    except Exception as e:
        logger.error(f"Errore caricamento config: {e}")
        return _cache.get("config", {})


def get_text(chiave: str, lingua: str = "it") -> str:
    """Ottiene testo tradotto, fallback a italiano"""
    T = get_testi()
    if chiave in T:
        if T[chiave].get(lingua):
            return T[chiave][lingua]
        if T[chiave].get("it"):
            return T[chiave]["it"]
    return chiave


def get_user(chat_id: int) -> Optional[Dict[str, Any]]:
    """Cerca utente per chat_id"""
    try:
        response = supabase.table("utenti").select("*").eq("chat_id", chat_id).execute()
        if response.data and len(response.data) > 0:
            return response.data[0]
        return None
    except Exception as e:
        logger.error(f"Errore ricerca utente {chat_id}: {e}")
        return None


def create_user(chat_id: int) -> Optional[Dict[str, Any]]:
    """Crea nuovo utente"""
    try:
        data = {
            "chat_id": chat_id,
            "stato_onboarding": "new",
            "lingua": "it",
            "error_count_dob": 0,
            "is_bloccato": False,
            "last_update_id": 0
        }
        response = supabase.table("utenti").insert(data).execute()
        logger.info(f"Utente creato: {chat_id}")
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Errore creazione utente {chat_id}: {e}")
        return None


def increment_utenti_count() -> bool:
    """Incrementa contatore utenti in config"""
    try:
        config = get_config()
        current = int(config.get("utenti_count", "0"))
        supabase.table("config").update({"valore": str(current + 1)}).eq("chiave", "utenti_count").execute()
        # Invalida cache config
        _cache["config_loaded_at"] = 0
        return True
    except Exception as e:
        logger.error(f"Errore incremento contatore: {e}")
        return False


def update_user(chat_id: int, updates: Dict[str, Any]) -> bool:
    """Aggiorna campi utente"""
    try:
        supabase.table("utenti").update(updates).eq("chat_id", chat_id).execute()
        return True
    except Exception as e:
        logger.error(f"Errore aggiornamento utente {chat_id}: {e}")
        return False


def save_lingua(chat_id: int, lingua: str, update_id: int) -> bool:
    """Salva lingua e aggiorna stato"""
    return update_user(chat_id, {
        "lingua": lingua,
        "stato_onboarding": "lingua_ok",
        "last_update_id": update_id
    })


def save_privacy_ok(chat_id: int, update_id: int) -> bool:
    """Salva accettazione privacy"""
    return update_user(chat_id, {
        "stato_onboarding": "privacy_ok",
        "last_update_id": update_id
    })


def save_privacy_no(chat_id: int, update_id: int) -> bool:
    """Salva rifiuto privacy"""
    return update_user(chat_id, {
        "stato_onboarding": "uscito",
        "last_update_id": update_id
    })


def save_nome(chat_id: int, nome: str, update_id: int) -> bool:
    """Salva nome utente"""
    return update_user(chat_id, {
        "nome": nome,
        "stato_onboarding": "nome_ok",
        "last_update_id": update_id
    })


def save_data_nascita(chat_id: int, data_nascita: str, minorenne: bool, update_id: int) -> bool:
    """Salva data nascita e completa onboarding"""
    from datetime import datetime
    return update_user(chat_id, {
        "data_nascita": data_nascita,
        "minorenne": minorenne,
        "stato_onboarding": "completo",
        "completed_at": datetime.utcnow().isoformat(),
        "error_count_dob": 0,
        "last_update_id": update_id
    })


def increment_dob_error(chat_id: int, update_id: int) -> int:
    """Incrementa contatore errori data nascita"""
    try:
        user = get_user(chat_id)
        count = (user.get("error_count_dob") or 0) + 1
        update_user(chat_id, {
            "error_count_dob": count,
            "last_update_id": update_id
        })
        return count
    except Exception as e:
        logger.error(f"Errore incremento errori dob {chat_id}: {e}")
        return 0


def save_last_bot_msg(chat_id: int, msg_id: int, step: str) -> bool:
    """Salva ID ultimo messaggio bot per cancellazione"""
    return update_user(chat_id, {
        "last_bot_msg_id": msg_id,
        "last_bot_msg_step": step
    })


def check_duplicate_update(chat_id: int, update_id: int) -> bool:
    """Verifica se update è duplicato. Ritorna True se duplicato."""
    user = get_user(chat_id)
    if user and user.get("last_update_id", 0) >= update_id:
        logger.info(f"Update duplicato ignorato: chat_id={chat_id}, update_id={update_id}")
        return True
    return False


def invalidate_cache():
    """Forza ricaricamento cache"""
    _cache["testi_loaded_at"] = 0
    _cache["config_loaded_at"] = 0
    _cache["eventi_loaded_at"] = 0


def get_consiglio_meteo(condizione: str, lang: str = "it") -> Optional[str]:
    """
    Ottiene consiglio meteo dalla tabella consigli_meteo.
    condizione: es. "pioggia", "sole", "vento", "mare_mosso"
    """
    try:
        response = supabase.table("consigli_meteo").select("*").eq("condizione", condizione).execute()
        if response.data and len(response.data) > 0:
            row = response.data[0]
            # Prova lingua richiesta, fallback a italiano
            consiglio = row.get(lang) or row.get("it") or ""
            return consiglio if consiglio else None
        return None
    except Exception as e:
        logger.error(f"Errore get_consiglio_meteo {condizione}: {e}")
        return None


def get_evento_oggi(lang: str = "it") -> Optional[str]:
    """
    Ottiene l'evento del giorno dalla tabella eventi con cache.
    Cerca eventi dove oggi è compreso tra data_inizio e data_fine.
    La cache si invalida dopo CACHE_TTL o se cambia il giorno.

    Struttura tabella eventi attesa:
    - titolo_it, titolo_en, titolo_de: titoli tradotti
    - data_inizio: data inizio evento (YYYY-MM-DD)
    - data_fine: data fine evento (YYYY-MM-DD)
    - attivo: boolean (opzionale)
    """
    from datetime import date

    oggi = date.today().isoformat()

    # Verifica cache: valida se stesso giorno e non scaduta
    if (_is_cache_valid("eventi") and
            _cache["eventi_oggi_data"] == oggi and
            _cache["eventi_oggi"] is not None):
        evento = _cache["eventi_oggi"]
        if evento:
            titolo = evento.get(f"titolo_{lang}") or evento.get("titolo_it") or evento.get("titolo") or ""
            return titolo if titolo else None
        return None

    try:
        # Cerca eventi attivi dove oggi è nel range data_inizio - data_fine
        response = supabase.table("eventi") \
            .select("*") \
            .lte("data_inizio", oggi) \
            .gte("data_fine", oggi) \
            .eq("attivo", True) \
            .order("data_inizio") \
            .limit(1) \
            .execute()

        # Aggiorna cache (anche se vuoto, per evitare query ripetute)
        if response.data and len(response.data) > 0:
            _cache["eventi_oggi"] = response.data[0]
        else:
            _cache["eventi_oggi"] = {}
        _cache["eventi_oggi_data"] = oggi
        _cache["eventi_loaded_at"] = time.time()
        logger.info(f"Cache eventi ricaricata per {oggi}")

        if response.data and len(response.data) > 0:
            row = response.data[0]
            titolo = row.get(f"titolo_{lang}") or row.get("titolo_it") or row.get("titolo") or ""
            return titolo if titolo else None
        return None
    except Exception as e:
        logger.error(f"Errore get_evento_oggi: {e}")
        # Ritorna cache precedente se disponibile
        evento = _cache.get("eventi_oggi")
        if evento:
            titolo = evento.get(f"titolo_{lang}") or evento.get("titolo_it") or evento.get("titolo") or ""
            return titolo if titolo else None
        return None


def get_utenti_attivi() -> list:
    """
    Restituisce lista di utenti attivi con onboarding completo.
    Usato per il morning briefing automatico.
    """
    try:
        response = supabase.table("utenti") \
            .select("chat_id, lingua, nome") \
            .eq("stato_onboarding", "completo") \
            .eq("is_bloccato", False) \
            .execute()

        if response.data:
            logger.info(f"Trovati {len(response.data)} utenti attivi per morning briefing")
            return response.data
        return []
    except Exception as e:
        logger.error(f"Errore get_utenti_attivi: {e}")
        return []


def get_stats() -> dict:
    """
    Restituisce statistiche per il comando /stats admin.
    """
    from datetime import datetime, timedelta

    stats = {
        "utenti_totali": 0,
        "utenti_attivi_7g": 0,
        "utenti_completi": 0,
        "eventi_totali": 0,
        "eventi_attivi": 0
    }

    try:
        # Utenti totali
        response = supabase.table("utenti").select("id", count="exact").execute()
        stats["utenti_totali"] = response.count or 0

        # Utenti con onboarding completo
        response = supabase.table("utenti").select("id", count="exact").eq("stato_onboarding", "completo").execute()
        stats["utenti_completi"] = response.count or 0

        # Utenti attivi negli ultimi 7 giorni (basato su updated_at o last_update_id)
        sette_giorni_fa = (datetime.now() - timedelta(days=7)).isoformat()
        response = supabase.table("utenti").select("id", count="exact").gte("updated_at", sette_giorni_fa).execute()
        stats["utenti_attivi_7g"] = response.count or 0

    except Exception as e:
        logger.error(f"Errore get_stats utenti: {e}")

    try:
        # Eventi totali
        response = supabase.table("eventi").select("id", count="exact").execute()
        stats["eventi_totali"] = response.count or 0

        # Eventi attivi oggi
        from datetime import date
        oggi = date.today().isoformat()
        response = supabase.table("eventi") \
            .select("id", count="exact") \
            .lte("data_inizio", oggi) \
            .gte("data_fine", oggi) \
            .eq("attivo", True) \
            .execute()
        stats["eventi_attivi"] = response.count or 0

    except Exception as e:
        logger.error(f"Errore get_stats eventi: {e}")

    return stats
