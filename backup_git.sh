#!/bin/bash
cd /home/slappy_bot
git add -A
git commit -m "backup automatico $(date '+%Y-%m-%d %H:%M')" || true
git push origin main || echo "Push fallito"
