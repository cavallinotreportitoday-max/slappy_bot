# Deploy Slappy Bot

## 1. Prerequisiti Supabase

Esegui queste query nel SQL Editor di Supabase:

```sql
-- Aggiungi constraint UNIQUE per evitare duplicati
ALTER TABLE utenti ADD CONSTRAINT utenti_chat_id_unique UNIQUE (chat_id);

-- Aggiungi colonna per dedup update_id
ALTER TABLE utenti ADD COLUMN IF NOT EXISTS last_update_id BIGINT DEFAULT 0;

-- Crea indice per performance
CREATE INDEX IF NOT EXISTS idx_utenti_chat_id ON utenti(chat_id);
```

## 2. Test Locale

```bash
# Crea ambiente virtuale
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# oppure: venv\Scripts\activate  # Windows

# Installa dipendenze
pip install -r requirements.txt

# Copia e configura .env
cp .env.example .env
# Modifica .env con le tue credenziali

# Avvia in modalità polling
python main.py
```

## 3. Deploy su Railway

### 3.1 Setup Repository

```bash
git init
git add .
git commit -m "Initial commit"
```

### 3.2 Railway Setup

1. Vai su https://railway.app e crea nuovo progetto
2. Seleziona "Deploy from GitHub repo" o "Deploy from local"
3. Collega il repository

### 3.3 Variabili d'ambiente su Railway

Aggiungi queste variabili nella sezione "Variables":

```
TELEGRAM_BOT_TOKEN=<il_tuo_token>
SUPABASE_URL=<url_supabase>
SUPABASE_KEY=<chiave_supabase>
WEBHOOK_URL=https://<tuo-progetto>.up.railway.app
PORT=8443
LOG_LEVEL=INFO
```

### 3.4 Procfile (crea questo file)

```
web: python main.py
```

### 3.5 Deploy

Railway rileva automaticamente le modifiche e fa deploy.

## 4. Alternative a Railway

### Render.com

1. Crea nuovo "Web Service"
2. Collega GitHub repo
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `python main.py`
5. Aggiungi variabili ambiente

### Fly.io

```bash
# Installa flyctl
curl -L https://fly.io/install.sh | sh

# Login
fly auth login

# Crea app
fly launch

# Configura secrets
fly secrets set TELEGRAM_BOT_TOKEN=xxx SUPABASE_URL=xxx SUPABASE_KEY=xxx

# Deploy
fly deploy
```

## 5. Verifica Deploy

1. Apri Telegram e cerca il tuo bot
2. Invia `/start`
3. Dovresti vedere la scelta lingua

## 6. Monitoraggio

### Logs Railway
```bash
railway logs
```

### Logs Render
Dalla dashboard web

### Debug locale
Imposta `LOG_LEVEL=DEBUG` nel .env

## 7. Struttura File

```
slappy_bot/
├── main.py          # Entry point
├── config.py        # Configurazione
├── database.py      # Supabase queries
├── handlers.py      # Handler Telegram
├── validators.py    # Validazione input
├── requirements.txt # Dipendenze
├── .env.example     # Template variabili
├── .env             # Variabili (non committare!)
├── Procfile         # Per Railway/Heroku
└── DEPLOY.md        # Queste istruzioni
```

## 8. Tabella Testi Richiesti

Assicurati che la tabella `testi` contenga queste chiavi:

| chiave | it | en | de |
|--------|----|----|-----|
| step2_testo | Testo privacy... | Privacy text... | Datenschutz... |
| btn_privacy_si | Accetto | Accept | Akzeptieren |
| btn_privacy_no | Rifiuto | Decline | Ablehnen |
| step3_chiedi_nome | Come ti chiami? | What's your name? | Wie heißt du? |
| step4_chiedi_data | Quando sei nato? | When were you born? | Wann wurdest du geboren? |
| msg_uscita | Arrivederci! | Goodbye! | Auf Wiedersehen! |
| msg_errore_nome | Nome non valido | Invalid name | Ungültiger Name |
| msg_errore_data | Data non valida | Invalid date | Ungültiges Datum |
| msg_fallback | Non capisco | I don't understand | Ich verstehe nicht |
| msg_limite | Posti esauriti | No more spots | Keine Plätze mehr |
| resp_sos | Numeri emergenza... | Emergency numbers... | Notfallnummern... |
| resp_meteo | Meteo oggi... | Weather today... | Wetter heute... |
| resp_eventi | Eventi... | Events... | Veranstaltungen... |
| resp_trasporti | Bus... | Transport... | Transport... |
| resp_idee | Idee... | Ideas... | Ideen... |

## 9. Tabella Config Richiesta

| chiave | valore |
|--------|--------|
| max_utenti | 50000 |
| utenti_count | 0 |
| canale_telegram | https://t.me/tuocanale |
