"""
Configurazione ambiente per Slappy Bot
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Admin per notifiche errori
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "118218170"))

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Stormglass API (per maree)
STORMGLASS_API_KEY = os.getenv("STORMGLASS_API_KEY", "")

# Sentry (monitoraggio errori)
SENTRY_DSN = os.getenv("SENTRY_DSN", "")

# Cache TTL (secondi)
CACHE_TTL = 300  # 5 minuti

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Webhook (opzionale, per Railway/produzione)
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
PORT = int(os.getenv("PORT", 8443))
