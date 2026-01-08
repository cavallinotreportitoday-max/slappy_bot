"""
API Farmacie di Turno - Scraper per farmaciediturno.org
Estrae SOLO la farmacia di turno (non tutte le farmacie aperte)
Comuni supportati: Cavallino-Treporti (27044), Jesolo (27019)
"""
import asyncio
import re
import time
import logging
from typing import Optional, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Codici ISTAT comuni
COMUNI = {
    "Cavallino-Treporti": 27044,
    "Jesolo": 27019
}

# Cache farmacie (durata 3 ore)
CACHE_DURATION = 3 * 60 * 60  # 3 ore in secondi
_cache = {
    "data": None,
    "timestamp": 0
}


@dataclass
class Farmacia:
    nome: str
    indirizzo: str
    telefono: str
    orario: str
    comune: str
    turno_info: str = ""  # Info specifica sul turno
    lat: Optional[float] = None
    lon: Optional[float] = None


def _parse_telefono(tel: str) -> str:
    """Pulisce e formatta numero di telefono"""
    tel = re.sub(r'[^\d]', '', tel)
    if tel and not tel.startswith('0'):
        tel = '0' + tel
    return tel


def _get_coordinates(comune: str) -> tuple:
    """Coordinate approssimative per i comuni"""
    coords = {
        "cavallino-treporti": (45.4580, 12.5280),
        "cavallino": (45.4580, 12.5100),
        "jesolo": (45.5089, 12.6463),
    }
    comune_lower = comune.lower()
    for key, (lat, lon) in coords.items():
        if key in comune_lower:
            return lat, lon
    return 45.4580, 12.5280


async def _fetch_html(cod_comune: int) -> Optional[str]:
    """Scarica HTML pagina farmacie"""
    import aiohttp

    url = f"https://www.farmaciediturno.org/comune.asp?cod={cod_comune}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "it-IT,it;q=0.9"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    return await response.text()
                logger.warning(f"HTTP {response.status} per comune {cod_comune}")
                return None
    except Exception as e:
        logger.error(f"Errore fetch: {e}")
        return None


def _extract_farmacia_turno(html: str, comune_nome: str) -> Optional[Farmacia]:
    """
    Estrae SOLO la farmacia di turno dall'HTML.

    Indicatori farmacia di turno:
    - Testo "Turno:" seguito da info
    - "fino a domani" negli orari
    - Orari che iniziano da "0:00" (turno notturno)
    - "Tutto il giorno"
    """

    # Pattern per identificare blocchi farmacia
    # Cerchiamo sezioni che contengono FARMACIA e hanno indicatori di turno

    # Indicatori che identificano una farmacia DI TURNO (non solo aperta)
    turno_indicators = [
        r'Turno[:\s]+',
        r'fino a domani',
        r'tutto il giorno',
        r'Tutto il giorno',
        r'TURNO',
        r'notturno',
        r'Notturno',
        r'0:00\s*-',  # Turno che inizia a mezzanotte
        r'24\s*ore',
        r'h\s*24',
    ]

    turno_pattern = '|'.join(turno_indicators)

    # Split per trovare blocchi farmacia
    # Il sito usa strutture con <td> o <div> per ogni farmacia

    # Prova a trovare blocchi che contengono sia "FARMACIA" che un indicatore di turno
    blocks = re.split(r'(?=FARMACIA\s+[A-Z])', html, flags=re.IGNORECASE)

    for block in blocks:
        # Deve contenere "FARMACIA" e un indicatore di turno
        if not re.search(r'FARMACIA', block, re.IGNORECASE):
            continue

        if not re.search(turno_pattern, block, re.IGNORECASE):
            continue

        # Estrai nome farmacia
        nome_match = re.search(r'(FARMACIA\s+[A-Z][A-Za-z\s\']+?)(?:<|,|\(|\d{5})', block, re.IGNORECASE)
        if not nome_match:
            nome_match = re.search(r'(FARMACIA\s+[A-Z][A-Za-z\s\']{2,20})', block, re.IGNORECASE)

        if not nome_match:
            continue

        nome = nome_match.group(1).strip()
        nome = re.sub(r'\s+', ' ', nome)

        # Estrai indirizzo
        indirizzo = ""
        # Pattern: Via/Viale/Piazza + nome + eventuale numero civico
        ind_match = re.search(
            r'((?:Via|Viale|Piazza|P\.?zza|Corso|Largo)\s+[^<,]+?(?:,\s*\d+)?)\s*[-–]?\s*(?:\d{5}|[A-Z]{2,})',
            block, re.IGNORECASE
        )
        if ind_match:
            indirizzo = ind_match.group(1).strip()
            indirizzo = re.sub(r'\s+', ' ', indirizzo)

        # Estrai telefono
        telefono = ""
        tel_match = re.search(r'(?:Tel[:\s]*|📞\s*)?(\d{10,11}|\d{3,4}[\s\-]?\d{6,7})', block)
        if tel_match:
            telefono = _parse_telefono(tel_match.group(1))

        # Estrai info turno
        turno_info = ""
        turno_match = re.search(r'(Turno[:\s]+[^<]+?)(?:<|\n|$)', block, re.IGNORECASE)
        if turno_match:
            turno_info = turno_match.group(1).strip()
        else:
            # Cerca orario esteso
            orario_match = re.search(r'(\d{1,2}[:.]\d{2}\s*[-–]\s*\d{1,2}[:.]\d{2}[^<]*fino a domani[^<]*)', block, re.IGNORECASE)
            if orario_match:
                turno_info = orario_match.group(1).strip()

        # Estrai orario normale
        orario = ""
        orario_match = re.search(r'(\d{1,2}[:.]\d{2}\s*[-–]\s*\d{1,2}[:.]\d{2})', block)
        if orario_match:
            orario = orario_match.group(1).replace('.', ':')

        if turno_info:
            orario = turno_info

        # Coordinate
        lat, lon = _get_coordinates(comune_nome)

        if nome:
            return Farmacia(
                nome=nome,
                indirizzo=indirizzo or comune_nome,
                telefono=telefono,
                orario=orario or "Di turno",
                comune=comune_nome,
                turno_info=turno_info,
                lat=lat,
                lon=lon
            )

    return None


async def get_farmacie_turno() -> Optional[List[Farmacia]]:
    """
    Ottiene la lista delle farmacie DI TURNO (max 1 per comune).
    Usa cache di 3 ore.

    Returns:
        Lista di Farmacia o None se errore
    """
    global _cache

    now = time.time()

    # Controlla cache
    if _cache["data"] and (now - _cache["timestamp"]) < CACHE_DURATION:
        logger.debug("Farmacie da cache")
        return _cache["data"]

    logger.info("Fetching farmacie di turno...")

    farmacie = []

    for comune_nome, cod in COMUNI.items():
        html = await _fetch_html(cod)
        if html:
            farmacia = _extract_farmacia_turno(html, comune_nome)
            if farmacia:
                farmacie.append(farmacia)
                logger.info(f"Turno {comune_nome}: {farmacia.nome}")
            else:
                logger.warning(f"Nessuna farmacia di turno trovata per {comune_nome}")

    if farmacie:
        _cache["data"] = farmacie
        _cache["timestamp"] = now
        return farmacie

    # Se fetch fallisce ma abbiamo cache vecchia, usala
    if _cache["data"]:
        logger.warning("Fetch fallito, uso cache scaduta")
        return _cache["data"]

    return None


# Fallback statico (usato se scraping fallisce)
FARMACIE_FALLBACK = [
    Farmacia(
        nome="FARMACIA CAVALLINO",
        indirizzo="Via Equilia, 26 - Cavallino",
        telefono="041968196",
        orario="Di turno",
        comune="Cavallino-Treporti",
        lat=45.4580,
        lon=12.5100
    ),
    Farmacia(
        nome="FARMACIA JESOLO",
        indirizzo="Jesolo",
        telefono="0421350377",
        orario="Di turno",
        comune="Jesolo",
        lat=45.5089,
        lon=12.6463
    ),
]


async def get_farmacie_turno_safe() -> List[Farmacia]:
    """
    Versione safe che ritorna sempre qualcosa.
    Se API fallisce, ritorna dati statici.
    """
    farmacie = await get_farmacie_turno()

    if farmacie:
        return farmacie

    logger.warning("Usando farmacie fallback statiche")
    return FARMACIE_FALLBACK


def get_maps_url(farmacia: Farmacia) -> str:
    """Genera URL Google Maps per navigazione"""
    if farmacia.lat and farmacia.lon:
        return f"https://www.google.com/maps/dir/?api=1&destination={farmacia.lat},{farmacia.lon}"

    indirizzo_encoded = farmacia.indirizzo.replace(' ', '+').replace(',', '%2C')
    return f"https://www.google.com/maps/search/?api=1&query={indirizzo_encoded}"


# Test
if __name__ == "__main__":
    async def test():
        print("=== TEST FARMACIE DI TURNO ===\n")

        farmacie = await get_farmacie_turno()

        if farmacie:
            print(f"Trovate {len(farmacie)} farmacie di turno:\n")
            for f in farmacie:
                print(f"📍 {f.comune}")
                print(f"   {f.nome}")
                print(f"   {f.indirizzo}")
                print(f"   Tel: {f.telefono}")
                print(f"   Orario: {f.orario}")
                print(f"   Maps: {get_maps_url(f)}")
                print()
        else:
            print("❌ Nessuna farmacia trovata (usando fallback)")
            for f in FARMACIE_FALLBACK:
                print(f"   - {f.nome} ({f.comune})")

    asyncio.run(test())
