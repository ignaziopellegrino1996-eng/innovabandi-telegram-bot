# Innovabandi Telegram Bot (UE/Italia/Sicilia/Emilia-Romagna)

Bot Telegram asincrono (python-telegram-bot) che monitora quotidianamente bandi/avvisi su innovazione
(digitale, R&S, transizione 4.0/5.0, AI, cybersecurity, cloud, energia, economia circolare, ecc.)
e invia SOLO le novità in un gruppo Telegram alle 08:00 Europe/Rome.

## Cosa fa
- Deduplica robusta + persistenza con SQLite
- Modalità per chat:
  - FULL: UE + Italia + Sicilia + Emilia-Romagna + fonti mirate
  - REGIONI: Sicilia + Emilia-Romagna (+ GURS + fonti mirate territoriali)
- GURS 2026: discovery PDF + keyword scan nel PDF
- Report settimanale: lunedì 08:05 (digest 7 giorni + “in scadenza”)
- Modalità CLI: --once / --weekly-once (cron/GitHub Actions)

## Setup bot Telegram (rapido)
1) Crea bot con @BotFather (/newbot) e salva token
2) Aggiungi bot nel gruppo e rendilo admin
3) (Consigliato) BotFather /setprivacy -> Disable
4) Ottieni chat_id:
   - invia un messaggio nel gruppo
   - apri: https://api.telegram.org/bot<TOKEN>/getUpdates
   - copia chat.id (-100...)

## Comandi bot
- /check -> controllo immediato
- /status -> ultimo run, quanti nuovi, modalità, errori fonti
- /sources -> fonti attive
- /mode full | /mode regioni -> cambia modalità (solo admin)
- /weekly -> report settimanale subito (solo admin o allowlist)

## GitHub Actions (schedulazioni)
GitHub Actions usa UTC. Nel repo ci sono due cron (inverno/estate) e un guard:
- daily: invia solo se ora locale Europe/Rome == 08:00
- weekly: invia solo se lunedì e ora locale == 08:05

## Secrets richiesti
Repo -> Settings -> Secrets and variables -> Actions:
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID

## Persistenza stato
Il DB SQLite (data/state.sqlite3) viene salvato/recuperato con actions/cache.
Serve per non reinviare mai lo stesso item.
