"""
VFS Global — монитор слотов для GitHub Actions
Проверяет 5 стран за один запуск: Венгрия, Франция, Италия, Испания, Греция
"""

import os
import sys
import json
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime

# ── Настройки (берутся из GitHub Secrets) ─────────────────────────────────────
EMAIL      = os.environ["VFS_EMAIL"]
PASSWORD   = os.environ["VFS_PASSWORD"]
TG_TOKEN   = os.environ["TG_TOKEN"]
TG_CHAT_ID = os.environ["TG_CHAT_ID"]

# Страны для мониторинга: (код_в_url, название)
COUNTRIES = [
    ("hun", "Венгрия 🇭🇺"),
    ("fra", "Франция 🇫🇷"),
    ("ita", "Италия 🇮🇹"),
    ("esp", "Испания 🇪🇸"),
    ("grc", "Греция 🇬🇷"),
]
# ─────────────────────────────────────────────────────────────────────────────


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def tg(message: str):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    data = json.dumps({
        "chat_id": TG_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            if not result.get("ok"):
                log(f"TG ошибка: {result}")
    except Exception as e:
        log(f"TG исключение: {e}")


def make_session():
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor()
    )
    opener.addheaders = [
        ("User-Agent",
         "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
         "AppleWebKit/537.36 (KHTML, like Gecko) "
         "Chrome/124.0.0.0 Safari/537.36"),
        ("Accept", "application/json, text/plain, */*"),
        ("Accept-Language", "ru-RU,ru;q=0.9,en;q=0.8"),
        ("Origin", "https://visa.vfsglobal.com"),
    ]
    return opener


def fetch(opener, url, json_body=None):
    if json_body is not None:
        payload = json.dumps(json_body).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"}
        )
    else:
        req = urllib.request.Request(url)
    try:
        with opener.open(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)


def login(opener) -> bool:
    log("Авторизация...")
    status, _ = fetch(opener, "https://visa.vfsglobal.com/rus/ru/hun/login")
    log(f"  Главная: HTTP {status}")
    time.sleep(1)

    status, body = fetch(opener, "https://visa.vfsglobal.com/api/v1/users/login",
                         json_body={
                             "username": EMAIL,
                             "password": PASSWORD,
                             "countryCode": "rus",
                             "languageCode": "ru",
                         })
    log(f"  Логин API: HTTP {status}")

    if status == 200:
        try:
            data = json.loads(body)
            token = data.get("token") or data.get("access_token")
            if token:
                opener.addheaders.append(("Authorization", f"Bearer {token}"))
                log("  ✅ Авторизован")
                return True
        except json.JSONDecodeError:
            pass

    if status == 403 or "captcha" in body.lower():
        log("  ⚠️ Капча или блокировка")
        tg("⚠️ <b>VFS Monitor</b>\nСайт требует капчу. Проверь вручную.")
        return False

    if status in (200, 302):
        log("  ✅ Авторизован (без токена)")
        return True

    log(f"  ❌ Ошибка входа. Ответ: {body[:200]}")
    return False


def check_country(opener, code: str, name: str) -> tuple[bool, str]:
    base = f"https://visa.vfsglobal.com/rus/ru/{code}"
    endpoints = [
        f"https://visa.vfsglobal.com/api/v1/appointment/slots?"
        f"countryCode=rus&languageCode=ru&missionCode={code}",
        f"https://visa.vfsglobal.com/api/v1/holiday?"
        f"countryCode=rus&missionCode={code}",
        f"{base}/book-an-appointment",
    ]

    no_slot_signals = [
        "no slots available", "no appointment", "currently no appointments",
        "slots are not available", "нет доступных", "no available", "notavailable",
    ]
    yes_slot_signals = [
        "availabledate", "available_date", '"available":true',
        "slotavailable", "openslot",
    ]

    for url in endpoints:
        status, body = fetch(opener, url)
        label = url.split("/")[-1].split("?")[0]
        log(f"  [{name}] {label}: HTTP {status}")

        if status == 0:
            continue

        body_lower = body.lower()

        for s in no_slot_signals:
            if s in body_lower:
                return False, f"нет ('{s}')"

        for s in yes_slot_signals:
            if s in body_lower:
                return True, f"сигнал '{s}'"

        if status == 200:
            try:
                data = json.loads(body)
                if isinstance(data, list) and len(data) > 0:
                    return True, f"API: {len(data)} записей"
                if isinstance(data, dict):
                    slots = data.get("slots") or data.get("dates") or data.get("availableDates")
                    if slots:
                        return True, f"слотов: {len(slots)}"
            except json.JSONDecodeError:
                pass

            if "book-an-appointment" in url and "appointment" in body_lower and "date" in body_lower:
                return True, "страница записи с датами"

    return False, "слотов не найдено"


def main():
    log("=" * 55)
    log("VFS Monitor | СПб | Венгрия / Франция / Италия / Испания / Греция")
    log("=" * 55)

    opener = make_session()

    if not login(opener):
        log("Авторизация не удалась. Завершаю.")
        sys.exit(1)

    time.sleep(2)

    found_any = []

    for code, name in COUNTRIES:
        log(f"\n── {name} ──")
        found, reason = check_country(opener, code, name)
        log(f"  Итог: {reason}")
        if found:
            found_any.append((code, name, reason))
        time.sleep(3)  # пауза между странами, чтобы не триггерить rate-limit

    if found_any:
        lines = "\n".join(
            f"• {name} — <a href='https://visa.vfsglobal.com/rus/ru/{code}/book-an-appointment'>записаться</a>"
            for code, name, _ in found_any
        )
        msg = (
            f"🎉 <b>СЛОТЫ ПОЯВИЛИСЬ!</b>\n\n"
            f"📍 Санкт-Петербург\n\n"
            f"{lines}\n\n"
            f"Действуй быстро — слоты разбирают моментально!"
        )
        log("\n🎉 НАЙДЕНЫ СЛОТЫ! Отправляю уведомление в Telegram...")
        tg(msg)
    else:
        log("\nСлотов нет ни по одной стране. До следующей проверки.")


if __name__ == "__main__":
    main()
