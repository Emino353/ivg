import csv
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo
import urllib.request
import urllib.parse
from playwright.sync_api import sync_playwright

URLS_FILE = "auktionen.txt"
CSV_FILE = "preise.csv"

def send_telegram(message: str):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode()
    try:
        urllib.request.urlopen(api_url, data=data, timeout=10)
    except Exception as e:
        print(f"Telegram-Fehler: {e}")

def load_urls() -> list[str]:
    if not os.path.exists(URLS_FILE):
        print(f"Datei {URLS_FILE} nicht gefunden.")
        return []
    urls = []
    with open(URLS_FILE, mode="r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return urls

def get_last_prices() -> dict[str, str]:
    last_prices = {}
    if not os.path.exists(CSV_FILE):
        return last_prices
    with open(CSV_FILE, mode="r", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter=";")
        next(reader, None)  # Kopfzeile überspringen
        for row in reader:
            if len(row) >= 4:
                last_prices[row[3]] = row[2]
    return last_prices

def fetch_auction_data(page, url: str) -> tuple[str, str]:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        text = page.inner_text("body")

        # Artikelnamen aus der Hauptüberschrift auslesen (Fallback: aus URL)
        title = ""
        h1 = page.locator("h1").first
        if h1.count() > 0:
            title = h1.inner_text().strip().replace("\n", " ")
        if not title:
            parts = [p for p in url.rstrip("/").split("/") if p]
            title = parts[-2] if len(parts) >= 2 else url

        # Aktuelles Gebot suchen
        match = re.search(r"Aktuelles Gebot\s*[:\-]?\s*(\d{1,3}(?:\.\d{3})*,\d{2}\s*€)", text, re.IGNORECASE)
        if match:
            return title, match.group(1).strip()

        fallback = re.search(r"(\d{1,3}(?:\.\d{3})*,\d{2}\s*€)", text)
        if fallback:
            return title, fallback.group(1).strip()

        return title, "Kein Preis sichtbar (beendet?)"
    except Exception as e:
        print(f"Fehler bei {url}: {e}")
        return url, "Fehler beim Abruf"

def main():
    urls = load_urls()
    if not urls:
        print("Keine URLs in auktionen.txt gefunden.")
        return

    now = datetime.now(ZoneInfo("Europe/Berlin")).strftime("%Y-%m-%d %H:%M:%S")
    last_prices = get_last_prices()
    file_exists = os.path.exists(CSV_FILE)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(locale="de-DE")

        with open(CSV_FILE, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter=";")
            if not file_exists:
                writer.writerow(["Zeitstempel", "Artikel", "Preis", "URL"])

            for url in urls:
                title, current_price = fetch_auction_data(page, url)
                writer.writerow([now, title, current_price, url])
                print(f"[{now}] {title}: {current_price}")

                prev_price = last_prices.get(url)
                if prev_price is not None and current_price != prev_price:
                    send_telegram(
                        f"🔔 Preisänderung bei {title}!\n"
                        f"Alt: {prev_price}\n"
                        f"Neu: {current_price}\n{url}"
                    )

        browser.close()

if __name__ == "__main__":
    main()
