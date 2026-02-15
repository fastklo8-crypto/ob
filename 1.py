import requests
import time
from bs4 import BeautifulSoup
import hashlib
import os
import re

# ====== НАЛАШТУВАННЯ ======
TELEGRAM_TOKEN = "8375812588:AAFFJSZbzwQLnqo4w7KlFln8nW-_EBl8En4"
TELEGRAM_CHAT_ID = "8311072217, 399707006"
OLX_URL = "https://www.olx.ua/uk/kiev/?search%5Border%5D=created_at:desc&search%5Bfilter_float_price:from%5D=100000"
SEEN_FILE = "seen_ads.txt"
CHECK_INTERVAL = 900
# ==========================

def send_telegram_message(text):
    """Надсилає повідомлення в Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {
        "chat_id": TELEGRAM_CHAT_ID, 
        "text": text, 
        "parse_mode": "HTML",
        "disable_web_page_preview": False  # Показувати прев'ю посилання
    }
    
    try:
        requests.post(url, data=data)
    except Exception as e:
        print(f"Помилка надсилання: {e}")

def parse_olx_page():
    """Отримує HTML сторінки та повертає список нових оголошень"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    try:
        response = requests.get(OLX_URL, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        # Знаходимо всі оголошення на сторінці
        ads = soup.find_all('div', attrs={'data-cy': 'l-card'})

        new_ads = []
        for ad in ads:
            # Посилання
            link_tag = ad.find('a', href=True)
            if not link_tag:
                continue
            relative_link = link_tag['href']
            full_link = relative_link if relative_link.startswith('http') else 'https://www.olx.ua' + relative_link

            # Заголовок
            title_tag = ad.find('h4', class_=re.compile('title'))
            title = title_tag.text.strip() if title_tag else "Без назви"

            # Ціна
            price_tag = ad.find('p', attrs={'data-testid': 'ad-price'})
            price = price_tag.text.strip() if price_tag else "Ціна не вказана"

            # ID оголошення
            ad_id = ad.get('id') or hashlib.md5(full_link.encode()).hexdigest()

            new_ads.append({
                'id': ad_id,
                'title': title,
                'price': price,
                'link': full_link
            })
        return new_ads
    except Exception as e:
        print(f"Помилка парсингу: {e}")
        return []

def load_seen_ids():
    """Завантажує ID вже відправлених оголошень з файлу"""
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, 'r') as f:
        return set(line.strip() for line in f)

def save_seen_ids(seen_ids):
    """Зберігає ID відправлених оголошень у файл"""
    with open(SEEN_FILE, 'w') as f:
        for ad_id in seen_ids:
            f.write(f"{ad_id}\n")

def main():
    send_telegram_message("🤖 Бот моніторингу OLX запущено!")
    seen_ids = load_seen_ids()

    while True:
        print(f"Перевірка о {time.strftime('%Y-%m-%d %H:%M:%S')}")
        current_ads = parse_olx_page()

        if not current_ads:
            print("Не вдалося отримати оголошення.")
            time.sleep(CHECK_INTERVAL)
            continue

        # Шукаємо нові оголошення
        new_ads_found = False
        for ad in current_ads:
            if ad['id'] not in seen_ids:
                # Форматуємо повідомлення з прямим посиланням
                message = (
                    f"🆕 <b>{ad['title']}</b>\n"
                    f"💰 {ad['price']}\n\n"
                    f"🔗 <a href='{ad['link']}'>👉 Відкрити оголошення на OLX</a>\n\n"
                    f"<i>Щоб позначити як прочитане - видаліть це повідомлення</i>"
                )
                
                send_telegram_message(message)
                seen_ids.add(ad['id'])
                new_ads_found = True
                time.sleep(1)  # Невелика затримка між надсиланням

        if new_ads_found:
            save_seen_ids(seen_ids)  # Оновлюємо файл

        print(f"Перевірено. Всього в базі: {len(seen_ids)} оголошень.")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()