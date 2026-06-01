"""
VFS Global — монитор слотов для GitHub Actions
Запускается каждые 5 минут, шлёт уведомление в Telegram.
"""

import os
import sys
import json
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime

# ── Настройки (берутся из GitHub Secrets) ────────────────────────────────────
EMAIL      = os.environ["VFS_EMAIL"]
PASSWORD   = os.environ["VFS_PASSWORD"]
TG_TOKEN   = os.environ["TG_TOKEN"]
TG_CHAT_ID = os.environ["TG_CHAT_ID"]

# Код страны в URL VFS (deu=Германия, fra=Франция, ita=Италия, esp=Испания и т.д.)
COUNTRY_CODE = os.environ.get("VFS_COUNTRY_CODE", "deu")
# Полное название для уведомления
COUNTRY_NAME = os.environ.get("VFS_COUNTRY_NAME", "Germany")

BASE_URL = f"https://visa.vfsglobal.com/rus/ru/{COUNTRY_CODE}"
# ─────────────────────────────────────────────────────────────────────────────


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def tg(message: str):
    """Отправка сообщения в Telegram."""
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
    """Создаём opener с куками и заголовками реального браузера."""
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
        ("Referer", f"{BASE_URL}/login"),
        ("Origin", "https://visa.vfsglobal.com"),
    ]
    return opener


def fetch(opener, url, data=None, json_body=None):
    """GET или POST запрос, возвращает (status, text)."""
    if json_body is not None:
        payload = json.dumps(json_body).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"}
        )
    elif data is not None:
        payload = urllib.parse.urlencode(data).encode("utf-8")
        req = urllib.request.Request(url, data=payload)
    else:
        req = urllib.request.Request(url)

    try:
        with opener.open(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, body
    except Exception as e:
        return 0, str(e)


def login(opener) -> bool:
    """Авторизация через API VFS Global."""
    log("Авторизация...")

    # Шаг 1: загрузить главную страницу (получить куки/токены)
    status, html = fetch(opener, f"{BASE_URL}/login")
    log(f"  Главная: HTTP {status}")
    if status not in (200, 302):
        log("  ⚠️ Неожиданный статус главной страницы")

    time.sleep(1)

    # Шаг 2: попытка логина через JSON API (стандартный эндпоинт VFS)
    login_url = "https://visa.vfsglobal.com/api/v1/users/login"
    status, body = fetch(opener, login_url, json_body={
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
                log("  ✅ Авторизован (токен получен)")
                return True
        except json.JSONDecodeError:
            pass

    if status == 403 or "captcha" in body.lower():
        log("  ⚠️ Капча или блокировка — VFS требует браузер")
        tg("⚠️ <b>VFS Monitor</b>\nСайт требует капчу. Войди вручную и провери куки.")
        return False

    if status in (200, 302):
        log("  ✅ Возможно авторизован (нет токена, но статус OK)")
        return True

    log(f"  ❌ Не удалось войти. Ответ: {body[:300]}")
    return False


def check_slots(opener) -> tuple[bool, str]:
    """
    Проверяет наличие слотов через API.
    Возвращает (слоты_есть, описание).
    """
    # Основной API-эндпоинт для проверки дат
    endpoints = [
        f"https://visa.vfsglobal.com/api/v1/appointment/slots?"
        f"countryCode=rus&languageCode=ru&missionCode={COUNTRY_CODE}",

        f"https://visa.vfsglobal.com/api/v1/holiday?"
        f"countryCode=rus&missionCode={COUNTRY_CODE}",

        f"{BASE_URL}/book-an-appointment",
    ]

    for url in endpoints:
        status, body = fetch(opener, url)
        log(f"  Проверка {url.split('/')[-1].split('?')[0]}: HTTP {status}")

        if status == 0:
            continue

        body_lower = body.lower()

        # Признаки отсутствия слотов
        no_slot_signals = [
            "no slots available",
            "no appointment",
            "currently no appointments",
            "slots are not available",
            "нет доступных",
            "no available",
            "notavailable",
        ]
        for signal in no_slot_signals:
            if signal in body_lower:
                return False, f"Слотов нет ('{signal}')"

        # Признаки наличия слотов
        yes_slot_signals = [
            "availabledate",
            "available_date",
            "\"available\":true",
            "slotavailable",
            "openslot",
        ]
        for signal in yes_slot_signals:
            if signal in body_lower:
                return True, f"Найден сигнал наличия слотов: '{signal}'"

        # Парсим JSON если возможно
        if status == 200 and body.startswith("[") or body.startswith("{"):
            try:
                data = json.loads(body)
                # Если пришёл непустой массив дат — скорее всего слоты есть
                if isinstance(data, list) and len(data) > 0:
                    return True, f"API вернул {len(data)} записей"
                if isinstance(data, dict):
                    slots = data.get("slots") or data.get("dates") or data.get("availableDates")
                    if slots and len(slots) > 0:
                        return True, f"Найдено слотов: {len(slots)}"
            except json.JSONDecodeError:
                pass

        # Если страница бронирования загрузилась и нет сигналов «нет слотов»
        if status == 200 and "book-an-appointment" in url:
            if "appointment" in body_lower and "date" in body_lower:
                return True, "Страница записи доступна и содержит даты"

    return False, "Слотов не обнаружено"


def main():
    log("=" * 50)
    log(f"VFS Monitor | {COUNTRY_NAME} | Санкт-Петербург")
    log("=" * 50)

    opener = make_session()

    logged_in = login(opener)
    if not logged_in:
        log("Авторизация не удалась. Завершаю.")
        sys.exit(1)

    time.sleep(2)

    found, reason = check_slots(opener)
    log(f"Результат: {reason}")

    if found:
        msg = (
            f"🎉 <b>СЛОТЫ ПОЯВИЛИСЬ!</b>\n\n"
            f"🌍 Страна: {COUNTRY_NAME}\n"
            f"📍 Центр: Санкт-Петербург\n"
            f"🔗 <a href='{BASE_URL}/book-an-appointment'>Записаться сейчас</a>\n\n"
            f"ℹ️ Причина: {reason}"
        )
        log("🎉 СЛОТЫ ЕСТЬ! Отправляю уведомление...")
        tg(msg)
    else:
        log("Слотов нет. До следующей проверки.")
        # Раскомментируй строку ниже если хочешь получать отчёт каждый час:
        # tg(f"ℹ️ VFS Monitor: слотов нет ({datetime.now().strftime('%H:%M')})")


if __name__ == "__main__":
    main()
