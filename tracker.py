import csv
import json
import os
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import urllib.request
import urllib.parse

URLS_FILE = "auktionen.txt"
CSV_FILE = "preise.csv"
STATUS_FILE = "status.json"
TZ = ZoneInfo("Europe/Berlin")


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


def parse_german_datetime(text: str) -> datetime | None:
    match = re.search(
        r"(\d{1,2})\.(\d{1,2})\.(\d{4})[^\d]{1,12}(\d{1,2}):(\d{2})(?::(\d{2}))?",
        text,
    )
    if not match:
        return None
    day, month, year, hour, minute, second = match.groups()
    try:
        return datetime(
            int(year),
            int(month),
            int(day),
            int(hour),
            int(minute),
            int(second or 0),
            tzinfo=TZ,
        )
    except ValueError:
        return None


def load_urls() -> list[tuple[str, datetime | None]]:
    if not os.path.exists(URLS_FILE):
        return []
    entries = []
    with open(URLS_FILE, mode="r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "|" in line:
                url_part, date_part = line.split("|", 1)
                entries.append((url_part.strip(), parse_german_datetime(date_part.strip())))
            else:
                entries.append((line, None))
    return entries


def load_status() -> dict:
    if not os.path.exists(STATUS_FILE):
        return {"last_daily_date": "", "auctions": {}}
    with open(STATUS_FILE, mode="r", encoding="utf-8") as f:
        return json.load(f)


def save_status(status: dict):
    with open(STATUS_FILE, mode="w", encoding="utf-8") as f:
        json.dump(status, f, indent=2, ensure_ascii=False)


def get_due_urls(
    entries: list[tuple[str, datetime | None]], status: dict, now: datetime
) -> tuple[list[tuple[str, datetime | None]], bool]:
    today_str = now.strftime("%Y-%m-%d")
    is_daily_window = (now.hour == 12) and (status.get("last_daily_date") != today_str)
    due = []

    for url, manual_end in entries:
        info = status["auctions"].get(url)

        # 1. Neue URL in auktionen.txt -> sofort einmal prüfen
        if info is None:
            due.append((url, manual_end))
            continue

        # Bereits beendete Auktionen überspringen
        if info.get("finished"):
            continue

        end_dt = manual_end
        if not end_dt and info.get("end_time"):
            end_dt = datetime.fromisoformat(info["end_time"])

        # 2. Falls keine Endzeit erkennbar war -> nur beim täglichen 12-Uhr-Check abfragen
        if end_dt is None:
            if is_daily_window:
                due.append((url, manual_end))
            continue

        mins_left = (end_dt - now).total_seconds() / 60.0

        # 3. Endspurt: 35 Min. vor Ende bis 15 Min. nach Ende alle 5 Minuten
        if -15 <= mins_left <= 35:
            due.append((url, manual_end))
            continue

        # 4. Nachzügler-Check, falls das Ende knapp verpasst wurde
        last_checked_str = info.get("last_checked")
        if mins_left < -15 and last_checked_str:
            if datetime.fromisoformat(last_checked_str) < end_dt:
                due.append((url, manual_end))
                continue

        # 5. Täglicher Mittags-Check um 12 Uhr für alle laufenden Auktionen
        if is_daily_window and mins_left > 0:
            due.append((url, manual_end))

    return due, is_daily_window


def extract_end_time(text: str, now: datetime) -> datetime | None:
    keyword_match = re.search(
        r"(?:Auktionsende|Endet(?:\s+am)?|Gebotsende|Ende)\s*[:\-]?\s*(\d{1,2}\.\d{1,2}\.\d{4}[^\d]{1,12}\d{1,2}:\d{2}(?::\d{2})?)",
        text,
        re.IGNORECASE,
    )
    if keyword_match:
        dt = parse_german_datetime(keyword_match.group(1))
        if dt:
            return dt

    for m in re.finditer(r"\d{1,2}\.\d{1,2}\.\d{4}[^\d]{1,12}\d{1,2}:\d{2}(?::\d{2})?", text):
        dt = parse_german_datetime(m.group(0))
        if dt and dt > (now - timedelta(hours=12)):
            return dt
    return None


def fetch_auction_data(page, url: str, now: datetime) -> tuple[str, str, datetime | None, bool]:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        text = page.inner_text("body")

        title = ""
        h1 = page.locator("h1").first
        if h1.count() > 0:
            title = h1.inner_text().strip().replace("\n", " ")
        if not title:
            parts = [p for p in url.rstrip("/").split("/") if p]
            title = parts[-2] if len(parts) >= 2 else url

        end_dt = extract_end_time(text, now)
        is_ended_text = bool(re.search(r"Auktion\s+(?:ist\s+)?beendet", text, re.IGNORECASE))

        match = re.search(r"Aktuelles Gebot\s*[:\-]?\s*(\d{1,3}(?:\.\d{3})*,\d{2}\s*€)", text, re.IGNORECASE)
        if match:
            return title, match.group(1).strip(), end_dt, is_ended_text

        fallback = re.search(r"(\d{1,3}(?:\.\d{3})*,\d{2}\s*€)", text)
        if fallback:
            return title, fallback.group(1).strip(), end_dt, is_ended_text

        return title, "Kein Preis sichtbar (beendet?)", end_dt, True
    except Exception as e:
        print(f"Fehler bei {url}: {e}")
        return url, "Fehler beim Abruf", None, False


def main():
    now = datetime.now(TZ)
    entries = load_urls()
    status = load_status()
    due_entries, is_daily_window = get_due_urls(entries, status, now)

    # Vorab-Check ohne Browser-Installation
    if "--check" in sys.argv:
        should_run = len(due_entries) > 0
        print(f"[{now.strftime('%H:%M')}] Fällige Auktionen: {len(due_entries)} -> run_browser={should_run}")
        github_output = os.getenv("GITHUB_OUTPUT")
        if github_output:
            with open(github_output, "a", encoding="utf-8") as f:
                f.write(f"run_browser={'true' if should_run else 'false'}\n")
        return

    if not due_entries:
        print("Aktuell keine Auktion zur Abfrage fällig.")
        return

    from playwright.sync_api import sync_playwright

    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    file_exists = os.path.exists(CSV_FILE)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(locale="de-DE")

        with open(CSV_FILE, mode="a", newline="", encoding="utf-8") as f:
            if not file_exists:
                f.write("\ufeff")  # UTF-8 BOM für saubere Umlaute/€-Zeichen in Excel
            writer = csv.writer(f, delimiter=";")
            if not file_exists:
                writer.writerow(["Zeitstempel", "Artikel", "Preis", "Auktionsende", "URL"])

            for url, manual_end in due_entries:
                prev_info = status["auctions"].get(url, {})
                prev_price = prev_info.get("last_price")

                title, current_price, scraped_end, is_ended_text = fetch_auction_data(page, url, now)

                # Bei Zeitverlängerung immer das spätere Auktionsende behalten
                effective_end = scraped_end or manual_end
                if prev_info.get("end_time"):
                    old_end = datetime.fromisoformat(prev_info["end_time"])
                    if effective_end is None or old_end > effective_end:
                        effective_end = old_end

                end_display = effective_end.strftime("%Y-%m-%d %H:%M") if effective_end else "unbekannt"
                writer.writerow([now_str, title, current_price, end_display, url])
                print(f"[{now_str}] {title}: {current_price} (Ende: {end_display})")

                if prev_price and current_price != prev_price:
                    send_telegram(
                        f"🔔 Preisänderung bei {title}!\n"
                        f"Alt: {prev_price}\n"
                        f"Neu: {current_price}\n"
                        f"Ende: {end_display}\n{url}"
                    )

                finished = is_ended_text
                if effective_end and now > (effective_end + timedelta(minutes=15)):
                    finished = True

                status["auctions"][url] = {
                    "title": title,
                    "end_time": effective_end.isoformat() if effective_end else None,
                    "last_price": current_price,
                    "last_checked": now.isoformat(),
                    "finished": finished,
                }

        browser.close()

    if is_daily_window:
        status["last_daily_date"] = now.strftime("%Y-%m-%d")

    save_status(status)


if __name__ == "__main__":
    main()
