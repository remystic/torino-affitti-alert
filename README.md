# Torino Affitti Alert — GitHub Actions

Bot Telegram gratuito che controlla periodicamente nuovi annunci di affitto a Torino.

## Portali configurati

- Immobiliare.it
- idealista
- Casa.it
- Subito
- Bakeca

Facebook Marketplace non viene sottoposto a scraping automatico: richiede un collegamento separato delle notifiche Facebook.

## Installazione

1. Crea un repository GitHub **pubblico**.
2. Carica tutti i file mantenendo la cartella `.github/workflows/`.
3. Nel repository apri `Settings` → `Secrets and variables` → `Actions`.
4. Crea un repository secret:
   - Name: `TELEGRAM_BOT_TOKEN`
   - Secret: il token ricevuto da BotFather
5. In Telegram invia `/start` al bot.
6. Su GitHub apri `Actions` → `Controlla affitti Torino` → `Run workflow`.
7. La prima esecuzione registra gli annunci esistenti senza inviarli. Le successive inviano solo i nuovi.

## Comandi

I comandi vengono letti alla successiva esecuzione programmata, quindi la risposta può arrivare dopo alcuni minuti.

- `/start` registra la chat
- `/status` mostra lo stato
- `/sources` mostra i portali
- `/pause` sospende gli avvisi
- `/resume` riattiva gli avvisi
- `/check` richiede un controllo nella sessione successiva

## Note

- Il workflow è programmato ogni 10 minuti; GitHub può ritardare le esecuzioni nei periodi di carico.
- I portali possono modificare le pagine o limitare gli accessi automatizzati. In tal caso il relativo connettore va aggiornato.
- Il file `state.json` contiene solo stato tecnico, URL già visti e ID della chat Telegram. Non contiene il token.
- Non scrivere mai il token dentro un file del repository.
