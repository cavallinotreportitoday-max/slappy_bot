"""
Validatori per nome e data di nascita
"""
import re
from datetime import datetime, date
from typing import Optional, Tuple

# Mesi in italiano, inglese, tedesco
MESI = {
    # Italiano
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4,
    "maggio": 5, "giugno": 6, "luglio": 7, "agosto": 8,
    "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
    # Inglese
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    # Tedesco
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3,
    "mai": 5, "juni": 6, "juli": 7,
    "september": 9, "oktober": 10, "dezember": 12,
    # Abbreviazioni comuni
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "gen": 1, "mag": 5, "giu": 6, "lug": 7, "ago": 8,
    "set": 9, "ott": 10, "dic": 12
}


def validate_name(text: str) -> Tuple[bool, str]:
    """
    Valida nome utente.
    Ritorna (is_valid, nome_pulito)

    Logica identica a n8n nodo 23:
    - Rimuove tag HTML
    - Rimuove caratteri pericolosi
    - Max 50 caratteri
    - Minimo 2 caratteri
    - Deve contenere almeno una lettera
    """
    if not text:
        return False, ""

    nome = text.strip()
    # Rimuove tag HTML
    nome = re.sub(r'<[^>]*>', '', nome)
    # Rimuove caratteri pericolosi
    nome = re.sub(r'[<>"\'&]', '', nome)
    # Max 50 caratteri
    nome = nome[:50].strip()

    # Validazione
    is_valid = len(nome) >= 2 and bool(re.search(r'[a-zA-ZÀ-ÿ]', nome))

    return is_valid, nome


def parse_date(text: str) -> Optional[date]:
    """
    Parsing flessibile data di nascita.
    Supporta formati:
    - 15/03/1985
    - 15-03-1985
    - 15.03.1985
    - 15 marzo 1985
    - March 15, 1985
    - 1985-03-15 (ISO)
    """
    if not text:
        return None

    text = text.strip().lower()

    # Pattern 1: numeri separati da /, -, .
    match = re.match(r'^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})$', text)
    if match:
        day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        try:
            return date(year, month, day)
        except ValueError:
            pass

    # Pattern 2: ISO format (YYYY-MM-DD)
    match = re.match(r'^(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})$', text)
    if match:
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        try:
            return date(year, month, day)
        except ValueError:
            pass

    # Pattern 3: giorno mese_testo anno (15 marzo 1985)
    match = re.match(r'^(\d{1,2})\s+([a-zäöüß]+)\s+(\d{4})$', text)
    if match:
        day = int(match.group(1))
        month_text = match.group(2)
        year = int(match.group(3))
        month = MESI.get(month_text)
        if month:
            try:
                return date(year, month, day)
            except ValueError:
                pass

    # Pattern 4: mese_testo giorno, anno (March 15, 1985)
    match = re.match(r'^([a-zäöüß]+)\s+(\d{1,2}),?\s+(\d{4})$', text)
    if match:
        month_text = match.group(1)
        day = int(match.group(2))
        year = int(match.group(3))
        month = MESI.get(month_text)
        if month:
            try:
                return date(year, month, day)
            except ValueError:
                pass

    return None


def validate_dob(text: str) -> Tuple[bool, Optional[str], bool]:
    """
    Valida data di nascita.
    Ritorna (is_valid, data_formattata, is_minorenne)

    - Data deve essere parsabile
    - Anno tra 1900 e anno corrente
    - Non nel futuro
    - Calcola se minorenne (età < 18)
    """
    parsed = parse_date(text)

    if not parsed:
        return False, None, False

    today = date.today()

    # Controlli validità
    if parsed.year < 1900 or parsed.year > today.year:
        return False, None, False

    if parsed > today:
        return False, None, False

    # Calcola età
    age = today.year - parsed.year
    if (today.month, today.day) < (parsed.month, parsed.day):
        age -= 1

    is_minorenne = age < 18

    # Formato ISO per DB
    data_str = parsed.isoformat()

    return True, data_str, is_minorenne
