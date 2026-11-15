import csv
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo
import urllib.request
import urllib.parse
from playwright.sync_api import sync_playwright

URL = "https://ivg.auction/de/auktionsobjekt/laufrad-scool-acedemy-grade-1/a328acc7-7148-4687-b94b-9e850cec418c"
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

def fetch_price() -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(locale="de-DE")
        page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)  # Warten, bis dynamische Gebote geladen sind
        text = page.inner_text("body")
        browser.close()

    # 1. Direkt nach "Aktuelles Gebot" suchen
    match = re.search(r"Aktuelles Gebot\s*[:\-]?\s*(\d{1,3}(?:\.\d{3})*,\d{2}\s*€)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    # 2. Fallback: Ersten Euro-Betrag auf der Seite erfassen
    fallback = re.search(r"(\d{1,3}(?:\.\d{3})*,\d{2}\s*€)", text)
    if fallback:
        return fallback.group(1).strip()

    return "Kein Preis sichtbar (Auktion beendet?)"

def get_last_price() -> str | None:
    if not os.path.exists(CSV_FILE):
        return None
    with open(CSV_FILE, mode="r", encoding="utf-8") as f:
        rows = list(csv.reader(f, delimiter=";"))
        if len(rows) > 1:
            return rows[-1][1]
    return None

def main():
    now = datetime.now(ZoneInfo("Europe/Berlin")).strftime("%Y-%m-%d %H:%M:%S")
    current_price = fetch_price()
    last_price = get_last_price()

    file_exists = os.path.exists(CSV_FILE)
    with open(CSV_FILE, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=";")
        if not file_exists:
            writer.writerow(["Zeitstempel", "Preis"])
        writer.writerow([now, current_price])

    print(f"[{now}] Preis: {current_price}")

    if last_price is not None and current_price != last_price:
        send_telegram(f"🔔 Preisänderung bei Makita DSP600!\nAlt: {last_price}\nNeu: {current_price}\n{URL}")

if __name__ == "__main__":
    main()
