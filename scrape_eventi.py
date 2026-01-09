"""
Scraper eventi dal sito del Comune di Cavallino-Treporti
Esegui: python scrape_eventi.py
"""
import asyncio
import httpx
import re
import logging
from datetime import datetime, date
from bs4 import BeautifulSoup
from supabase import create_client
from config import SUPABASE_URL, SUPABASE_KEY, TELEGRAM_BOT_TOKEN, ADMIN_CHAT_ID

logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_URL = "https://www.comune.cavallinotreporti.ve.it"
EVENTI_URL = f"{BASE_URL}/home/vivere/eventi.html"
REQUEST_DELAY = 1

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

MESI = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4,
    "maggio": 5, "giugno": 6, "luglio": 7, "agosto": 8,
    "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12
}

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0.0.0"}

JUNK_WORDS = ['cookie', 'privacy', 'consenso', 'analytics', 'gdpr', 'accetta', 'rifiuta', 'tracciamento']


def is_junk(text: str) -> bool:
    return any(w in text.lower() for w in JUNK_WORDS)


def parse_date(text: str):
    """Estrae data. Ritorna (data_inizio, data_fine)."""
    text = text.lower()

    # "dal 21 settembre 2025 al 29 marzo 2026"
    m = re.search(r'dal\s+(\d{1,2})\s+(\w+)\s+(\d{4})\s+al\s+(\d{1,2})\s+(\w+)\s+(\d{4})', text)
    if m:
        g1, m1, a1, g2, m2, a2 = m.groups()
        if m1 in MESI and m2 in MESI:
            try:
                return date(int(a1), MESI[m1], int(g1)), date(int(a2), MESI[m2], int(g2))
            except:
                pass

    # "8 gennaio 2026"
    m = re.search(r'(\d{1,2})\s+(gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre)\s+(\d{4})', text)
    if m:
        g, mese, a = m.groups()
        try:
            d = date(int(a), MESI[mese], int(g))
            return d, d
        except:
            pass

    return None, None


def parse_time(text: str):
    """Estrae orario."""
    m = re.search(r'dalle?\s*(\d{1,2})[.:,](\d{2})\s*alle?\s*(\d{1,2})[.:,](\d{2})', text.lower())
    if m:
        return f"{m.group(1)}:{m.group(2)}-{m.group(3)}:{m.group(4)}"

    m = re.search(r'ore\s*(\d{1,2})[.:,](\d{2})', text.lower())
    if m:
        return f"{m.group(1)}:{m.group(2)}"

    return None


def categorize(title: str):
    t = title.lower()
    if any(w in t for w in ["mercato", "mercatino", "fiera"]):
        return "mercato"
    if any(w in t for w in ["sagra", "festa", "degustazione"]):
        return "sagra"
    if any(w in t for w in ["concerto", "musica", "jazz"]):
        return "musica"
    if any(w in t for w in ["mostra", "museo", "teatro"]):
        return "cultura"
    if any(w in t for w in ["torneo", "gara", "sport"]):
        return "sport"
    if any(w in t for w in ["bambini", "famiglia", "laboratorio"]):
        return "famiglia"
    return "altro"


async def fetch(client, url):
    try:
        r = await client.get(url, headers=HEADERS, follow_redirects=True, timeout=30)
        return r.text if r.status_code == 200 else None
    except Exception as e:
        logger.error(f"Errore fetch: {e}")
        return None


async def extract_description(client, url):
    """Estrae solo la descrizione da una pagina dettaglio."""
    html = await fetch(client, url)
    if not html:
        return None

    soup = BeautifulSoup(html, 'html.parser')

    for tag in soup.select('script, style, nav, header, footer, aside, .breadcrumb, .sidebar'):
        tag.decompose()

    main = soup.select_one('article') or soup.select_one('main') or soup.select_one('.content') or soup

    for p in main.find_all('p'):
        text = p.get_text(strip=True)
        if len(text) > 100 and not is_junk(text):
            return text[:1500]

    return None


async def parse_detail_page(client, url):
    """Estrae dati dalla pagina dettaglio."""
    html = await fetch(client, url)
    if not html:
        return None

    soup = BeautifulSoup(html, 'html.parser')

    for tag in soup.select('script, style, nav, header, footer, aside, .breadcrumb, .sidebar'):
        tag.decompose()

    main = soup.select_one('article') or soup.select_one('main') or soup.select_one('.content') or soup
    page_text = main.get_text()

    data_inizio, data_fine = parse_date(page_text)
    if not data_inizio:
        return None

    orario = parse_time(page_text)

    descrizione = None
    for p in main.find_all('p'):
        text = p.get_text(strip=True)
        if len(text) > 100 and not is_junk(text):
            descrizione = text[:1500]
            break

    luogo = None
    luogo_match = re.search(r'(?:presso|location|dove|luogo)[:\s]+([^,\n\.]{5,50})', page_text, re.IGNORECASE)
    if luogo_match:
        luogo = luogo_match.group(1).strip()

    return {
        'data_inizio': data_inizio,
        'data_fine': data_fine,
        'orario': orario,
        'descrizione': descrizione,
        'luogo': luogo
    }


def upsert_evento(data):
    """UPSERT evento. Ritorna (is_new, is_updated)."""
    try:
        titolo = data['titolo_it']
        data_inizio = data['data_inizio']

        r = supabase.table('eventi').select('id').eq('titolo_it', titolo).eq('data_inizio', data_inizio).execute()

        if r.data:
            # Update
            update_data = {
                'descrizione_it': data.get('descrizione_it'),
                'orario': data.get('orario'),
                'luogo': data.get('luogo'),
                'data_fine': data.get('data_fine'),
                'url': data.get('url'),
                'updated_at': datetime.now().isoformat()
            }
            supabase.table('eventi').update(update_data).eq('id', r.data[0]['id']).execute()
            return False, True
        else:
            # Insert
            supabase.table('eventi').insert(data).execute()
            return True, False

    except Exception as e:
        logger.error(f"Errore DB: {e}")
        return False, False


async def notify_admin(new_count, updated_count, backfilled, total):
    """Notifica Telegram."""
    if not TELEGRAM_BOT_TOKEN:
        return

    text = f"🔄 <b>Scraping Eventi</b>\n\n"
    text += f"📅 {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
    text += f"━━━━━━━━━━━━━━━━━━━━\n"
    text += f"🔍 Trovati: <b>{total}</b>\n"
    text += f"✅ Nuovi: <b>{new_count}</b>\n"
    text += f"🔁 Aggiornati: <b>{updated_count}</b>\n"
    if backfilled > 0:
        text += f"📝 Backfill: <b>{backfilled}</b>\n"
    text += f"\n🦭 <i>SLAPPY</i>"

    try:
        async with httpx.AsyncClient() as c:
            await c.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": ADMIN_CHAT_ID, "text": text, "parse_mode": "HTML"}
            )
    except:
        pass


async def get_all_event_links(client, soup):
    """Trova tutti i link eventi dalla pagina, incluse le pagine categoria."""
    all_hrefs = set()

    # 1. Link eventi dalla pagina corrente
    for link in soup.find_all('a', href=lambda h: h and '/eventi/' in h and '.html' in h):
        href = link.get('href', '')
        if href:
            all_hrefs.add(href)

    # 2. Trova link a pagine categoria (?categoria=xxx)
    categoria_links = soup.find_all('a', href=lambda h: h and 'categoria=' in h)
    categoria_urls = set()
    for link in categoria_links:
        href = link.get('href', '')
        if href.startswith('/'):
            categoria_urls.add(BASE_URL + href)
        elif href.startswith('http'):
            categoria_urls.add(href)

    # 3. Visita ogni pagina categoria
    for cat_url in categoria_urls:
        logger.info(f"Scaricando pagina categoria: {cat_url}")
        await asyncio.sleep(REQUEST_DELAY)
        cat_html = await fetch(client, cat_url)
        if cat_html:
            cat_soup = BeautifulSoup(cat_html, 'html.parser')
            for link in cat_soup.find_all('a', href=lambda h: h and '/eventi/' in h and '.html' in h):
                href = link.get('href', '')
                if href:
                    all_hrefs.add(href)

    return all_hrefs


async def backfill_descriptions(client):
    """Backfill: aggiorna eventi con URL ma senza descrizione."""
    logger.info("Avvio backfill descrizioni...")

    try:
        # Prendi eventi senza descrizione ma con URL
        result = supabase.table('eventi') \
            .select('id, url, titolo_it') \
            .is_('descrizione_it', 'null') \
            .not_.is_('url', 'null') \
            .execute()

        missing = result.data or []
        logger.info(f"Trovati {len(missing)} eventi senza descrizione")

        backfilled = 0
        for row in missing:
            url = row.get('url', '')
            if not url:
                continue

            # Costruisci URL completo
            if url.startswith('/'):
                full_url = BASE_URL + url
            else:
                full_url = url

            logger.info(f"Backfill: {row.get('titolo_it', '')[:40]}...")
            await asyncio.sleep(REQUEST_DELAY)

            descrizione = await extract_description(client, full_url)
            if descrizione:
                supabase.table('eventi').update({
                    'descrizione_it': descrizione,
                    'updated_at': datetime.now().isoformat()
                }).eq('id', row['id']).execute()
                backfilled += 1
                logger.info(f"  → Descrizione aggiunta")

        return backfilled

    except Exception as e:
        logger.error(f"Errore backfill: {e}")
        return 0


async def scrape():
    """Scraping principale."""
    logger.info("Avvio scraping...")

    new_count = 0
    updated_count = 0

    async with httpx.AsyncClient() as client:
        # 1. Scarica pagina lista
        html = await fetch(client, EVENTI_URL)
        if not html:
            logger.error("Impossibile scaricare pagina eventi")
            return

        soup = BeautifulSoup(html, 'html.parser')

        # 2. Trova tutti i link eventi (incluse pagine categoria)
        all_hrefs = await get_all_event_links(client, soup)
        logger.info(f"Totale link eventi unici: {len(all_hrefs)}")

        # 3. Per ogni link, trova il titolo e processa
        processed = 0
        for href in all_hrefs:
            # Trova il link originale per estrarre il titolo
            link = soup.find('a', href=href)
            if link:
                titolo = link.get_text(strip=True)
            else:
                # Estrai titolo dall'URL
                titolo = href.split('/')[-1].replace('.html', '').replace('-', ' ').title()

            # SALTA se titolo contiene "Leggi altro" o è troppo corto
            if not titolo or len(titolo) < 5:
                continue
            if 'leggi altro' in titolo.lower():
                continue

            # Costruisci URL completo
            if href.startswith('/'):
                url = BASE_URL + href
            elif href.startswith('http'):
                url = href
            else:
                continue

            processed += 1
            logger.info(f"[{processed}/{len(all_hrefs)}] {titolo[:50]}...")

            # Delay
            await asyncio.sleep(REQUEST_DELAY)

            # 4. Visita pagina dettaglio
            details = await parse_detail_page(client, url)
            if not details:
                logger.warning(f"  → Nessuna data trovata, skip")
                continue

            # Prepara dati (incluso URL)
            evento = {
                'titolo_it': titolo[:255],
                'descrizione_it': details.get('descrizione'),
                'data_inizio': details['data_inizio'].isoformat(),
                'data_fine': details['data_fine'].isoformat(),
                'orario': details.get('orario'),
                'luogo': details.get('luogo', '')[:255] if details.get('luogo') else None,
                'url': href,  # Salva il path
                'categoria': categorize(titolo),
                'attivo': True
            }

            # 5. UPSERT
            is_new, is_updated = upsert_evento(evento)
            if is_new:
                new_count += 1
                logger.info(f"  → NUOVO")
            elif is_updated:
                updated_count += 1
                logger.info(f"  → Aggiornato")

        # 6. BACKFILL: eventi senza descrizione ma con URL
        backfilled = await backfill_descriptions(client)

        # Notifica admin
        await notify_admin(new_count, updated_count, backfilled, len(all_hrefs))

    logger.info(f"Completato: {new_count} nuovi, {updated_count} aggiornati, {backfilled} backfill")


if __name__ == "__main__":
    asyncio.run(scrape())
