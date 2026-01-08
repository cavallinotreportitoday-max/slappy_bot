#!/bin/bash
cd /home/slappy_bot

# Prova backup
git add -A
git commit -m "backup automatico $(date '+%Y-%m-%d %H:%M')" 2>/dev/null

# Prova push
if git push origin main 2>&1; then
    echo "$(date): Backup OK"
else
    # Notifica Telegram se fallisce
    curl -s "https://api.telegram.org/bot8543856308:AAGTo8BTKxUMYRVOB6dEkJoBqX6HBW7RJc8/sendMessage" \
        -d "chat_id=118218170" \
        -d "text=🚨 BACKUP FALLITO $(date '+%Y-%m-%d %H:%M')" \
        > /dev/null
    echo "$(date): Backup FALLITO"
fi
