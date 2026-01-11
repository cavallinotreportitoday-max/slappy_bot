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


def get_eventi_prossimi(giorni: int = 7, limit: int = None) -> list:
    """
    Restituisce eventi attivi da oggi ai prossimi N giorni.

    Args:
        giorni: numero di giorni da oggi (default 7)
        limit: limite risultati (None = tutti)

    Returns:
        Lista di eventi ordinati per data_inizio
    """
    from datetime import date, timedelta

    try:
        oggi = date.today()
        fine_periodo = oggi + timedelta(days=giorni)

        query = supabase.table("eventi") \
            .select("*") \
            .lte("data_inizio", fine_periodo.isoformat()) \
            .gte("data_fine", oggi.isoformat()) \
            .eq("attivo", True) \
            .order("data_inizio")

        if limit:
            query = query.limit(limit)

        response = query.execute()

        if response.data:
            return response.data
        return []
    except Exception as e:
        logger.error(f"Errore get_eventi_prossimi: {e}")
        return []


def get_eventi_count(giorni: int = 7) -> int:
    """Conta eventi attivi nei prossimi N giorni."""
    from datetime import date, timedelta

    try:
        oggi = date.today()
        fine_periodo = oggi + timedelta(days=giorni)

        response = supabase.table("eventi") \
            .select("id", count="exact") \
            .lte("data_inizio", fine_periodo.isoformat()) \
            .gte("data_fine", oggi.isoformat()) \
            .eq("attivo", True) \
            .execute()

        return response.count or 0
    except Exception as e:
        logger.error(f"Errore get_eventi_count: {e}")
        return 0


def get_evento_imperdibile() -> Optional[dict]:
    """Restituisce l'evento imperdibile di oggi (se c'è)."""
    from datetime import date

    try:
        oggi = date.today().isoformat()

        response = supabase.table("eventi") \
            .select("*") \
            .lte("data_inizio", oggi) \
            .gte("data_fine", oggi) \
            .eq("attivo", True) \
            .eq("imperdibile", True) \
            .limit(1) \
            .execute()

        if response.data and len(response.data) > 0:
            return response.data[0]
        return None
    except Exception as e:
        logger.error(f"Errore get_evento_imperdibile: {e}")
        return None


def get_eventi_periodo(data_inizio: str, data_fine: str, limit: int = None, offset: int = 0, categoria: str = None) -> list:
    """
    Restituisce eventi in un periodo specifico con paginazione.

    Args:
        data_inizio: data inizio periodo (YYYY-MM-DD)
        data_fine: data fine periodo (YYYY-MM-DD)
        limit: limite risultati per pagina
        offset: offset per paginazione
        categoria: filtra per categoria (opzionale)
    """
    try:
        query = supabase.table("eventi") \
            .select("*") \
            .lte("data_inizio", data_fine) \
            .gte("data_fine", data_inizio) \
            .eq("attivo", True) \
            .order("data_inizio")

        if categoria:
            query = query.eq("categoria", categoria)

        if limit:
            query = query.limit(limit)

        if offset:
            query = query.offset(offset)

        response = query.execute()
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Errore get_eventi_periodo: {e}")
        return []


def get_eventi_count_periodo(data_inizio: str, data_fine: str, categoria: str = None) -> int:
    """Conta eventi in un periodo specifico."""
    try:
        query = supabase.table("eventi") \
            .select("id", count="exact") \
            .lte("data_inizio", data_fine) \
            .gte("data_fine", data_inizio) \
            .eq("attivo", True)

        if categoria:
            query = query.eq("categoria", categoria)

        response = query.execute()
        return response.count or 0
    except Exception as e:
        logger.error(f"Errore get_eventi_count_periodo: {e}")
        return 0


def get_evento_by_id(evento_id: int) -> Optional[dict]:
    """Restituisce un evento per ID."""
    try:
        response = supabase.table("eventi") \
            .select("*") \
            .eq("id", evento_id) \
            .eq("attivo", True) \
            .limit(1) \
            .execute()

        if response.data and len(response.data) > 0:
            return response.data[0]
        return None
    except Exception as e:
        logger.error(f"Errore get_evento_by_id: {e}")
        return None


def get_eventi_giorno(data: str) -> list:
    """Restituisce tutti gli eventi di un giorno specifico."""
    try:
        response = supabase.table("eventi") \
            .select("*") \
            .lte("data_inizio", data) \
            .gte("data_fine", data) \
            .eq("attivo", True) \
            .order("orario") \
            .execute()

        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Errore get_eventi_giorno: {e}")
        return []


def get_giorni_con_eventi(anno: int, mese: int) -> list:
    """Restituisce lista di giorni del mese che hanno eventi."""
    from datetime import date
    import calendar

    try:
        # Primo e ultimo giorno del mese
        primo_giorno = date(anno, mese, 1)
        ultimo_giorno = date(anno, mese, calendar.monthrange(anno, mese)[1])

        response = supabase.table("eventi") \
            .select("data_inizio, data_fine") \
            .lte("data_inizio", ultimo_giorno.isoformat()) \
            .gte("data_fine", primo_giorno.isoformat()) \
            .eq("attivo", True) \
            .execute()

        giorni_con_eventi = set()
        if response.data:
            for evento in response.data:
                # Aggiungi tutti i giorni coperti dall'evento
                try:
                    inizio = date.fromisoformat(evento["data_inizio"])
                    fine = date.fromisoformat(evento["data_fine"])
                    current = max(inizio, primo_giorno)
                    end = min(fine, ultimo_giorno)
                    while current <= end:
                        if current.month == mese:
                            giorni_con_eventi.add(current.day)
                        current = date(current.year, current.month, current.day + 1) if current.day < 28 else current.replace(day=current.day + 1)
                except:
                    pass

        return sorted(list(giorni_con_eventi))
    except Exception as e:
        logger.error(f"Errore get_giorni_con_eventi: {e}")
        return []


def get_categorie_eventi() -> list:
    """Restituisce le categorie disponibili con conteggio eventi attivi."""
    from datetime import date

    categorie = ["mercato", "sagra", "musica", "cultura", "sport", "famiglia"]
    oggi = date.today().isoformat()
    result = []

    for cat in categorie:
        try:
            response = supabase.table("eventi") \
                .select("id", count="exact") \
                .gte("data_fine", oggi) \
                .eq("attivo", True) \
                .eq("categoria", cat) \
                .execute()
            count = response.count or 0
            if count > 0:
                result.append({"categoria": cat, "count": count})
        except:
            pass

    return result


# ============ TRASPORTI ============

def get_operatori_attivi(tipo: str = None) -> list:
    """
    Restituisce operatori attivi, opzionalmente filtrati per tipo.
    tipo: 'bus' o 'ferry' (None = tutti)
    """
    try:
        query = supabase.table("operatori") \
            .select("*") \
            .eq("attivo", True) \
            .order("nome")

        if tipo:
            query = query.eq("tipo", tipo)

        response = query.execute()
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Errore get_operatori_attivi: {e}")
        return []


def get_operatore_by_id(operatore_id: int) -> Optional[dict]:
    """Restituisce un operatore specifico per ID."""
    try:
        response = supabase.table("operatori") \
            .select("*") \
            .eq("id", operatore_id) \
            .limit(1) \
            .execute()
        if response.data and len(response.data) > 0:
            return response.data[0]
        return None
    except Exception as e:
        logger.error(f"Errore get_operatore_by_id: {e}")
        return None


def get_linee_by_tipo(tipo: str) -> list:
    """Restituisce tutte le linee attive di un tipo (bus/traghetto)."""
    try:
        response = supabase.table("linee") \
            .select("*, operatori(nome, sito_web)") \
            .eq("tipo", tipo) \
            .eq("attivo", True) \
            .order("codice") \
            .execute()
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Errore get_linee_by_tipo: {e}")
        return []


def get_linea_by_id(linea_id: int) -> Optional[dict]:
    """Restituisce una linea specifica per ID."""
    try:
        response = supabase.table("linee") \
            .select("*, operatori(nome, sito_web)") \
            .eq("id", linea_id) \
            .eq("attivo", True) \
            .limit(1) \
            .execute()
        if response.data and len(response.data) > 0:
            return response.data[0]
        return None
    except Exception as e:
        logger.error(f"Errore get_linea_by_id: {e}")
        return None


def get_zone_attive() -> list:
    """Restituisce tutte le zone attive ordinate geograficamente."""
    try:
        response = supabase.table("zone") \
            .select("*") \
            .eq("attivo", True) \
            .order("ordine_geografico") \
            .execute()
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Errore get_zone_attive: {e}")
        return []


def get_destinazioni_attive() -> list:
    """Restituisce tutte le destinazioni attive."""
    try:
        response = supabase.table("destinazioni") \
            .select("*") \
            .eq("attivo", True) \
            .order("ordine") \
            .execute()
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Errore get_destinazioni_attive: {e}")
        return []


def get_destinazione_by_id(destinazione_id: int) -> Optional[dict]:
    """Restituisce una destinazione specifica per ID."""
    try:
        response = supabase.table("destinazioni") \
            .select("*") \
            .eq("id", destinazione_id) \
            .eq("attivo", True) \
            .limit(1) \
            .execute()
        if response.data and len(response.data) > 0:
            return response.data[0]
        return None
    except Exception as e:
        logger.error(f"Errore get_destinazione_by_id: {e}")
        return None


def get_percorsi_by_destinazione(destinazione_codice: str) -> list:
    """Restituisce tutti i percorsi per una destinazione (by codice)."""
    try:
        response = supabase.table("percorsi") \
            .select("*, linee(nome, tipo, operatori(nome, link))") \
            .eq("destinazione_codice", destinazione_codice) \
            .eq("attivo", True) \
            .order("durata") \
            .execute()
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Errore get_percorsi_by_destinazione: {e}")
        return []


def get_tariffe_by_operatore(operatore_id: int) -> list:
    """Restituisce tariffe di un operatore."""
    try:
        response = supabase.table("tariffe") \
            .select("*") \
            .eq("operatore_id", operatore_id) \
            .eq("attivo", True) \
            .order("prezzo") \
            .execute()
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Errore get_tariffe_by_operatore: {e}")
        return []


def get_tariffe_by_linea(linea_id: int) -> list:
    """Restituisce tariffe di una linea specifica."""
    try:
        response = supabase.table("tariffe") \
            .select("*") \
            .eq("linea_id", linea_id) \
            .eq("attivo", True) \
            .order("prezzo") \
            .execute()
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Errore get_tariffe_by_linea: {e}")
        return []


# ============ ORARI BUS REALI ============

def get_prossimi_orari_bus(linea_codice: str, fermata_nome: str, direzione: str = "andata",
                          ora_partenza: str = None, tipo_giorno: str = None, limit: int = 3) -> list:
    """
    Restituisce i prossimi orari bus dalla tabella orari_bus.

    Args:
        linea_codice: es. "23A", "96"
        fermata_nome: nome fermata
        direzione: "andata" o "ritorno"
        ora_partenza: orario minimo (HH:MM), se None usa ora corrente
        tipo_giorno: "feriale", "festivo", "sabato" - se None determina automaticamente
        limit: numero massimo di risultati

    Returns:
        Lista di orari [{ora: "HH:MM", ...}, ...]
    """
    from datetime import datetime
    import pytz

    try:
        rome_tz = pytz.timezone("Europe/Rome")
        now = datetime.now(rome_tz)

        # Determina ora partenza (formato HH:MM:SS per database)
        if ora_partenza:
            # Aggiungi :00 se manca
            ora_min = ora_partenza if len(ora_partenza) > 5 else f"{ora_partenza}:00"
        else:
            ora_min = now.strftime("%H:%M:%S")

        # tipo_giorno nel database è "fF" (tutti i giorni feriali/festivi)
        # Per ora usiamo sempre "fF" finché non ci sono dati separati
        tipo_giorno_db = "fF"

        # LOG DETTAGLIATO QUERY
        logger.info(f"[ORARI_BUS] Query: linea={linea_codice}, fermata={fermata_nome}, dir={direzione}, tipo={tipo_giorno_db}, ora>={ora_min}, limit={limit}")

        response = supabase.table("orari_bus") \
            .select("*") \
            .eq("linea_codice", linea_codice) \
            .eq("fermata_nome", fermata_nome) \
            .eq("direzione", direzione) \
            .eq("tipo_giorno", tipo_giorno_db) \
            .gte("ora", ora_min) \
            .order("ora") \
            .limit(limit) \
            .execute()

        # LOG RISULTATO
        logger.info(f"[ORARI_BUS] Risultato: {len(response.data) if response.data else 0} righe - {response.data}")

        # Normalizza formato ora da HH:MM:SS a HH:MM
        def normalize_ora(orario_dict):
            if orario_dict and "ora" in orario_dict:
                ora_raw = orario_dict["ora"]
                if ora_raw and len(ora_raw) > 5:
                    orario_dict["ora"] = ora_raw[:5]  # "16:30:00" -> "16:30"
            return orario_dict

        if response.data:
            risultati = [normalize_ora(o) for o in response.data]
            logger.info(f"[ORARI_BUS] Orari normalizzati: {[r.get('ora') for r in risultati]}")
            return risultati

        # Se non ci sono più corse oggi, cerca le prime di domani
        if not response.data or len(response.data) < limit:
            response_domani = supabase.table("orari_bus") \
                .select("*") \
                .eq("linea_codice", linea_codice) \
                .eq("fermata_nome", fermata_nome) \
                .eq("direzione", direzione) \
                .eq("tipo_giorno", tipo_giorno_db) \
                .order("ora") \
                .limit(limit - len(response.data or [])) \
                .execute()

            orari_domani = []
            if response_domani.data:
                for orario in response_domani.data:
                    orario["domani"] = True
                    orari_domani.append(normalize_ora(orario))

            return [normalize_ora(o) for o in (response.data or [])] + orari_domani

        return [normalize_ora(o) for o in (response.data or [])]

    except Exception as e:
        logger.error(f"Errore get_prossimi_orari_bus: {e}")
        return []


def get_orari_traghetto(linea_codice: str, fermata_nome: str, direzione: str = "andata",
                        ora_partenza: str = None, limit: int = 10) -> list:
    """
    Restituisce orari traghetti dalla tabella orari_bus (linee 12, 14, 15).

    Args:
        linea_codice: "12", "14", "15"
        fermata_nome: nome fermata (es. "Punta Sabbioni")
        direzione: "andata" o "ritorno"
        ora_partenza: orario minimo (HH:MM), se None restituisce tutti
        limit: numero massimo di risultati

    Returns:
        Lista di orari [{ora: "HH:MM", tipo_giorno: "fF", ...}, ...]
    """
    from datetime import datetime
    import pytz

    try:
        rome_tz = pytz.timezone("Europe/Rome")
        now = datetime.now(rome_tz)

        # Normalizza formato ora da HH:MM:SS a HH:MM
        def normalize_ora(orario_dict):
            if orario_dict and "ora" in orario_dict:
                ora_raw = orario_dict["ora"]
                if ora_raw and len(ora_raw) > 5:
                    orario_dict["ora"] = ora_raw[:5]
            return orario_dict

        # Query base
        query = supabase.table("orari_bus") \
            .select("*") \
            .eq("linea_codice", linea_codice) \
            .eq("fermata_nome", fermata_nome) \
            .eq("direzione", direzione)

        # Se ora_partenza specificata, filtra
        if ora_partenza:
            ora_min = ora_partenza if len(ora_partenza) > 5 else f"{ora_partenza}:00"
            query = query.gte("ora", ora_min)

        response = query.order("ora").limit(limit).execute()

        if response.data:
            return [normalize_ora(o) for o in response.data]
        return []

    except Exception as e:
        logger.error(f"Errore get_orari_traghetto: {e}")
        return []


def get_fermata_bus(linea_codice: str, fermata_nome: str) -> Optional[dict]:
    """
    Restituisce info su una fermata specifica dalla tabella fermate_bus.

    Returns:
        {nome, zona, ordine, tempo_da_capolinea, ...}
    """
    try:
        response = supabase.table("fermate_bus") \
            .select("*") \
            .eq("linea_codice", linea_codice) \
            .eq("nome", fermata_nome) \
            .limit(1) \
            .execute()

        if response.data and len(response.data) > 0:
            return response.data[0]
        return None
    except Exception as e:
        logger.error(f"Errore get_fermata_bus: {e}")
        return None


def get_fermate_linea(linea_codice: str, direzione: str = "andata") -> list:
    """
    Restituisce tutte le fermate di una linea in ordine.

    Returns:
        Lista di fermate ordinate per ordine
    """
    try:
        response = supabase.table("fermate_bus") \
            .select("*") \
            .eq("linea_codice", linea_codice) \
            .order("ordine") \
            .execute()

        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Errore get_fermate_linea: {e}")
        return []


def get_tempo_tra_fermate(linea_codice: str, fermata_partenza: str, fermata_arrivo: str) -> Optional[int]:
    """
    Calcola il tempo di percorrenza tra due fermate usando tempo_da_capolinea.

    Returns:
        Tempo in minuti, o None se non trovato
    """
    try:
        fermata_p = get_fermata_bus(linea_codice, fermata_partenza)
        fermata_a = get_fermata_bus(linea_codice, fermata_arrivo)

        if fermata_p and fermata_a:
            tempo_p = fermata_p.get("tempo_da_capolinea", 0) or 0
            tempo_a = fermata_a.get("tempo_da_capolinea", 0) or 0
            return abs(tempo_a - tempo_p)
        return None
    except Exception as e:
        logger.error(f"Errore get_tempo_tra_fermate: {e}")
        return None


def get_linee_per_zona(zona_codice: str) -> list:
    """
    Restituisce le linee bus che servono una zona specifica.

    Returns:
        Lista di linee [{linea_codice, fermata_nome, ...}, ...]
    """
    try:
        response = supabase.table("fermate_bus") \
            .select("linea_codice, nome, tempo_da_capolinea") \
            .eq("zona", zona_codice) \
            .order("linea_codice") \
            .execute()

        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Errore get_linee_per_zona: {e}")
        return []


def calcola_arrivo_fermata(ora_partenza: str, linea_codice: str, fermata_partenza: str, fermata_arrivo: str) -> Optional[str]:
    """
    Calcola l'orario di arrivo a una fermata dato l'orario di partenza.

    Args:
        ora_partenza: orario partenza (HH:MM)
        linea_codice: codice linea
        fermata_partenza: nome fermata partenza
        fermata_arrivo: nome fermata arrivo

    Returns:
        Orario arrivo (HH:MM) o None
    """
    from datetime import datetime, timedelta

    try:
        tempo = get_tempo_tra_fermate(linea_codice, fermata_partenza, fermata_arrivo)
        if tempo is None:
            return None

        # Parse ora partenza
        h, m = map(int, ora_partenza.split(":"))
        dt_partenza = datetime(2000, 1, 1, h, m)
        dt_arrivo = dt_partenza + timedelta(minutes=tempo)

        return dt_arrivo.strftime("%H:%M")
    except Exception as e:
        logger.error(f"Errore calcola_arrivo_fermata: {e}")
        return None


# ============ FORTINI ============

def get_fortini_by_zona(zona: str) -> list:
    """
    Restituisce fortini di una zona ordinati per ruolo_percorso (hub→tappa→isolato).

    Args:
        zona: nome zona (es. "Cavallino", "Ca' Savio")

    Returns:
        Lista di fortini ordinati
    """
    try:
        # Ordine ruolo: hub=1, tappa=2, isolato=3
        response = supabase.table("fortini") \
            .select("*") \
            .eq("zona", zona) \
            .execute()

        if response.data:
            # Ordina per ruolo_percorso
            ruolo_ordine = {"hub": 1, "tappa": 2, "isolato": 3}
            fortini = sorted(
                response.data,
                key=lambda f: ruolo_ordine.get(f.get("ruolo_percorso", "isolato"), 3)
            )
            return fortini
        return []
    except Exception as e:
        logger.error(f"Errore get_fortini_by_zona {zona}: {e}")
        return []


def get_fortino_by_id(fortino_id: str) -> Optional[dict]:
    """Restituisce un fortino specifico per ID."""
    try:
        response = supabase.table("fortini") \
            .select("*") \
            .eq("id", fortino_id) \
            .limit(1) \
            .execute()

        if response.data and len(response.data) > 0:
            return response.data[0]
        return None
    except Exception as e:
        logger.error(f"Errore get_fortino_by_id {fortino_id}: {e}")
        return None


def get_percorsi_fortini_attivi() -> list:
    """Restituisce tutti i percorsi fortini attivi."""
    try:
        response = supabase.table("percorsi") \
            .select("*") \
            .order("id") \
            .execute()

        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Errore get_percorsi_fortini_attivi: {e}")
        return []


def get_fortini_in_percorso(percorso_id: str) -> list:
    """
    Restituisce i fortini di un percorso in ordine.

    Args:
        percorso_id: ID del percorso

    Returns:
        Lista di fortini con ordine nel percorso
    """
    try:
        # Join percorsi_fortini con fortini
        response = supabase.table("percorsi_fortini") \
            .select("ordine, fortini(*)") \
            .eq("percorso_id", percorso_id) \
            .order("ordine") \
            .execute()

        if response.data:
            # Estrai fortini con ordine
            result = []
            for item in response.data:
                fortino = item.get("fortini", {})
                if fortino:
                    fortino["ordine_percorso"] = item.get("ordine", 0)
                    result.append(fortino)
            return result
        return []
    except Exception as e:
        logger.error(f"Errore get_fortini_in_percorso {percorso_id}: {e}")
        return []


def get_percorso_by_id(percorso_id: str) -> Optional[dict]:
    """Restituisce un percorso specifico per ID."""
    try:
        response = supabase.table("percorsi") \
            .select("*") \
            .eq("id", percorso_id) \
            .limit(1) \
            .execute()

        if response.data and len(response.data) > 0:
            return response.data[0]
        return None
    except Exception as e:
        logger.error(f"Errore get_percorso_by_id {percorso_id}: {e}")
        return None
