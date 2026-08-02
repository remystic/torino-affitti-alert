from __future__ import annotations

import asyncio
import html
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from playwright.async_api import Browser, Page, async_playwright

STATE_PATH = Path("state.json")
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
MAX_SEEN = 5000


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    patterns: tuple[str, ...]
    wait_ms: int = 2500
    max_items: int = 80


SOURCES: tuple[Source, ...] = (
    Source(
        "Immobiliare.it",
        "https://www.immobiliare.it/affitto-case/torino/",
        (r"/annunci/\d+",),
    ),
    Source(
        "idealista",
        "https://www.idealista.it/affitto-case/torino-torino/",
        (r"/immobile/\d+",),
    ),
    Source(
        "Casa.it",
        "https://www.casa.it/affitto/residenziale/torino/",
        (r"/immobili/\d+", r"/affitto/[^?#]+/\d+"),
    ),
    Source(
        "Subito",
        "https://www.subito.it/annunci-piemonte/affitto/appartamenti/torino/torino/",
        (r"subito\.it/appartamenti/[^?#]+-\d+\.htm",),
    ),
    Source(
        "Bakeca",
        "https://torino.bakeca.it/annunci/offro-casa/",
        (r"bakeca\.it/dettaglio/[^?#]+", r"/annunci/offro-casa/[^?#]+"),
    ),
)


def default_state() -> dict[str, Any]:
    return {
        "chat_id": None,
        "last_update_id": 0,
        "paused": False,
        "seen": [],
        "initialized_sources": [],
        "last_errors": {},
    }


def load_state() -> dict[str, Any]:
    state = default_state()
    if STATE_PATH.exists():
        try:
            loaded = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state.update(loaded)
        except (OSError, json.JSONDecodeError):
            pass
    return state


def save_state(state: dict[str, Any]) -> None:
    seen = list(dict.fromkeys(str(v) for v in state.get("seen", [])))
    state["seen"] = seen[-MAX_SEEN:]
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    filtered = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in {"fbclid", "gclid", "msockid"}
    ]
    clean_path = parts.path.rstrip("/") or "/"
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), clean_path, urlencode(filtered), "")
    )


def compact_text(value: str, limit: int = 220) -> str:
    cleaned = " ".join(value.split())
    return cleaned[:limit].strip()


class Telegram:
    def __init__(self, token: str) -> None:
        if not token:
            raise RuntimeError(
                "Manca il secret TELEGRAM_BOT_TOKEN nelle impostazioni GitHub Actions."
            )
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.client = httpx.AsyncClient(timeout=35.0)

    async def close(self) -> None:
        await self.client.aclose()

    async def call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        response = await self.client.post(f"{self.base_url}/{method}", json=payload or {})
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("description", "Errore Telegram"))
        return data.get("result")

    async def send(self, chat_id: int, text: str) -> None:
        await self.call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text[:4096],
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
        )


async def process_commands(telegram: Telegram, state: dict[str, Any]) -> bool:
    """Registra la chat e applica i pochi comandi utili.

    Restituisce True quando è stato richiesto /check.
    """
    offset = int(state.get("last_update_id", 0)) + 1
    updates = await telegram.call(
        "getUpdates",
        {"offset": offset, "timeout": 0, "allowed_updates": ["message"]},
    )
    force_check = False

    for update in updates:
        update_id = int(update.get("update_id", 0))
        state["last_update_id"] = max(int(state.get("last_update_id", 0)), update_id)
        message = update.get("message") or {}
        text = str(message.get("text") or "").strip()
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None or not text.startswith("/"):
            continue

        command = text.split()[0].split("@", 1)[0].casefold()
        numeric_chat_id = int(chat_id)

        if command == "/start":
            state["chat_id"] = numeric_chat_id
            await telegram.send(
                numeric_chat_id,
                "🏠 <b>Torino Affitti Alert attivato</b>\n\n"
                "Riceverai i nuovi annunci trovati a Torino. "
                "La prima scansione registra gli annunci già presenti e non li invia.",
            )
        elif command == "/status":
            registered = "sì" if state.get("chat_id") else "no"
            paused = "in pausa" if state.get("paused") else "attivo"
            await telegram.send(
                numeric_chat_id,
                f"Stato: <b>{paused}</b>\n"
                f"Chat registrata: {registered}\n"
                f"Annunci memorizzati: {len(state.get('seen', []))}\n"
                f"Portali configurati: {len(SOURCES)}",
            )
        elif command == "/sources":
            rows = "\n".join(f"• {html.escape(source.name)}" for source in SOURCES)
            await telegram.send(
                numeric_chat_id,
                "<b>Portali controllati</b>\n" + rows +
                "\n\nFacebook Marketplace richiede un collegamento separato delle notifiche.",
            )
        elif command == "/pause":
            state["paused"] = True
            await telegram.send(numeric_chat_id, "Avvisi sospesi.")
        elif command == "/resume":
            state["paused"] = False
            await telegram.send(numeric_chat_id, "Avvisi riattivati.")
        elif command == "/check":
            force_check = True
            await telegram.send(
                numeric_chat_id,
                "Controllo richiesto. Verrà eseguito in questa sessione GitHub Actions.",
            )

    return force_check


async def accept_cookies(page: Page) -> None:
    selectors = (
        "#onetrust-accept-btn-handler",
        "button:has-text('Accetta tutto')",
        "button:has-text('Accetta')",
        "button:has-text('Accept all')",
    )
    for selector in selectors:
        try:
            button = page.locator(selector).first
            if await button.is_visible(timeout=500):
                await button.click(timeout=1200)
                return
        except Exception:
            continue


async def extract_links(page: Page, source: Source) -> list[dict[str, str]]:
    raw_links: list[dict[str, str]] = await page.locator("a[href]").evaluate_all(
        """
        els => els.map(a => ({
          href: a.href || '',
          text: (a.innerText || a.getAttribute('aria-label') || a.title || '').trim(),
          context: (a.closest('article, li, [class*=card], [class*=listing], [class*=item]')?.innerText || '').trim()
        }))
        """
    )
    patterns = [re.compile(pattern, re.IGNORECASE) for pattern in source.patterns]
    unique: dict[str, dict[str, str]] = {}

    for row in raw_links:
        href = canonical_url(str(row.get("href") or ""))
        if not href.startswith("http"):
            continue
        if not any(pattern.search(href) for pattern in patterns):
            continue
        if href in unique:
            continue
        title = compact_text(str(row.get("text") or ""), 180)
        context = compact_text(str(row.get("context") or ""), 320)
        if len(title) < 4:
            title = context or "Nuovo annuncio"
        unique[href] = {"url": href, "title": title, "context": context}
        if len(unique) >= source.max_items:
            break

    return list(unique.values())


async def scan_source(browser: Browser, source: Source) -> tuple[list[dict[str, str]], str | None]:
    context = await browser.new_context(
        locale="it-IT",
        timezone_id="Europe/Rome",
        viewport={"width": 1440, "height": 1100},
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
        ),
    )
    page = await context.new_page()
    page.set_default_timeout(30000)
    try:
        await page.goto(source.url, wait_until="domcontentloaded", timeout=45000)
        await accept_cookies(page)
        await page.wait_for_timeout(source.wait_ms)
        listings = await extract_links(page, source)
        if not listings:
            return [], "nessun link di annuncio rilevato; il portale potrebbe avere cambiato pagina"
        return listings, None
    except Exception as exc:  # noqa: BLE001 - logghiamo l'errore per sorgente
        return [], compact_text(str(exc), 300)
    finally:
        await context.close()


async def send_listing(
    telegram: Telegram,
    chat_id: int,
    source: Source,
    listing: dict[str, str],
) -> None:
    title = html.escape(listing.get("title") or "Nuovo annuncio")
    url = html.escape(listing["url"], quote=True)
    context = compact_text(listing.get("context") or "", 450)
    message = [
        "🏠 <b>NUOVO ANNUNCIO</b>",
        f"<b>{title}</b>",
        f"🔎 {html.escape(source.name)}",
    ]
    if context and context.casefold() != listing.get("title", "").casefold():
        message.append(f"\n{html.escape(context)}")
    message.append(f'\n🔗 <a href="{url}">Apri annuncio</a>')
    await telegram.send(chat_id, "\n".join(message))


async def run() -> int:
    state = load_state()
    telegram = Telegram(TOKEN)
    try:
        force_check = await process_commands(telegram, state)
        save_state(state)

        chat_id = state.get("chat_id")
        if chat_id is None:
            print("Chat Telegram non registrata. Invia /start al bot e riesegui il workflow.")
            return 0
        if state.get("paused") and not force_check:
            print("Bot in pausa: scansione saltata.")
            return 0

        seen = set(str(v) for v in state.get("seen", []))
        initialized = set(str(v) for v in state.get("initialized_sources", []))
        errors: dict[str, str] = {}
        sent_count = 0

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                for source in SOURCES:
                    listings, error = await scan_source(browser, source)
                    if error:
                        errors[source.name] = error
                        print(f"{source.name}: {error}")
                        continue

                    is_baseline = source.name not in initialized
                    new_for_source = 0
                    for listing in listings:
                        url = listing["url"]
                        if url in seen:
                            continue
                        seen.add(url)
                        new_for_source += 1
                        if not is_baseline:
                            await send_listing(telegram, int(chat_id), source, listing)
                            sent_count += 1
                            await asyncio.sleep(0.25)

                    initialized.add(source.name)
                    print(
                        f"{source.name}: {len(listings)} trovati, "
                        f"{new_for_source} nuovi, baseline={is_baseline}"
                    )
            finally:
                await browser.close()

        state["seen"] = list(seen)
        state["initialized_sources"] = sorted(initialized)
        state["last_errors"] = errors
        save_state(state)

        if force_check:
            await telegram.send(
                int(chat_id),
                f"Controllo completato. Nuovi annunci inviati: <b>{sent_count}</b>.",
            )
        print(f"Scansione completata. Notifiche inviate: {sent_count}")
        return 0
    finally:
        await telegram.close()
        save_state(state)


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(run()))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as exc:  # noqa: BLE001
        print(f"Errore fatale: {exc}", file=sys.stderr)
        raise
