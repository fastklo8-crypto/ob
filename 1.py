import requests
import time
from bs4 import BeautifulSoup
import hashlib
import os
import re
import json
import threading

# ====== НАЛАШТУВАННЯ ======
TELEGRAM_TOKEN = "8375812588:AAFFJSZbzwQLnqo4w7KlFln8nW-_EBl8En4"
TELEGRAM_CHAT_ID = "1070744113"  # Тимчасово тільки один chat_id
OLX_URL = "https://www.olx.ua/uk/elektronika/telefony-i-aksesuary/mobilnye-telefony-smartfony/q-iphone-17/?currency=UAH&search%5Bprivate_business%5D=private&search%5Border%5D=created_at:desc&search%5Bfilter_enum_state%5D%5B0%5D=used&search%5Bfilter_enum_state%5D%5B1%5D=new&search%5Bfilter_enum_mobile_phone_manufacturer%5D%5B0%5D=2065&search%5Bfilter_enum_operating_system%5D%5B0%5D=2"
SEEN_FILE = "seen_ads.txt"
CHECK_INTERVAL = 900
# ==========================

# Проксі (експортовані з оточення)
PROXY = os.environ.get('PROXY', '')
HTTP_PROXY = os.environ.get('HTTP_PROXY', PROXY)
HTTPS_PROXY = os.environ.get('HTTPS_PROXY', PROXY)

# Глобальна змінна для зберігання offset
last_update_id = 0

def get_proxies():
    """Повертає словник проксі для requests"""
    proxies = {}
    if HTTP_PROXY:
        proxies['http'] = HTTP_PROXY
    if HTTPS_PROXY:
        proxies['https'] = HTTPS_PROXY
    return proxies if proxies else None

def delete_webhook():
    """Видаляє webhook, щоб можна було використовувати getUpdates"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook"
    proxies = get_proxies()
    
    try:
        print(f"🔍 Видалення webhook з проксі: {proxies if proxies else 'без проксі'}")
        response = requests.post(url, proxies=proxies, timeout=30)
        if response.status_code == 200:
            result = response.json()
            if result.get("ok"):
                print("✅ Webhook успішно видалено")
                return True
            else:
                print(f"❌ Помилка видалення webhook: {result}")
        else:
            print(f"❌ Помилка HTTP: {response.status_code}")
    except requests.exceptions.Timeout:
        print("❌ Таймаут при видаленні webhook")
    except requests.exceptions.ConnectionError as e:
        print(f"❌ Помилка з'єднання: {e}")
        print("   Можливо, проксі не працюють. Спробуйте без проксі...")
        # Спробуємо без проксі
        try:
            response = requests.post(url, timeout=30)
            if response.status_code == 200:
                result = response.json()
                if result.get("ok"):
                    print("✅ Webhook успішно видалено (без проксі)")
                    return True
        except Exception as e2:
            print(f"❌ І без проксі не вийшло: {e2}")
    except Exception as e:
        print(f"❌ Помилка при видаленні webhook: {e}")
    return False

def send_telegram_message(text, reply_markup=None):
    """Надсилає повідомлення в Telegram"""
    chat_ids = [chat_id.strip() for chat_id in TELEGRAM_CHAT_ID.split(",")]
    proxies = get_proxies()
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    for chat_id in chat_ids:
        if not chat_id:
            continue
            
        data = {
            "chat_id": chat_id, 
            "text": text, 
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
        
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)
            print(f"Відправляємо повідомлення з кнопкою: {reply_markup}")
        
        try:
            response = requests.post(url, data=data, proxies=proxies, timeout=30)
            print(f"Відповідь Telegram для {chat_id}: {response.status_code}")
            
            if response.status_code == 200:
                print(f"✅ Повідомлення успішно відправлено в чат {chat_id}")
            else:
                print(f"❌ Помилка {response.status_code}: {response.text}")
                
        except requests.exceptions.Timeout:
            print(f"⏱️ Таймаут надсилання для {chat_id}")
        except requests.exceptions.ConnectionError as e:
            print(f"🔌 Помилка з'єднання для {chat_id}: {e}")
        except Exception as e:
            print(f"❌ Помилка надсилання для {chat_id}: {e}")

def send_ad_with_button(ad):
    """Надсилає оголошення з кнопкою 'Позначити як прочитане'"""
    message = (
        f"🆕 <b>{ad['title']}</b>\n"
        f"💰 {ad['price']}\n\n"
        f"🔗 <a href='{ad['link']}'>👉 Відкрити оголошення на OLX</a>"
    )
    
    # Кнопка під повідомленням
    reply_markup = {
        "inline_keyboard": [
            [{"text": "✅ Позначити як прочитане", "callback_data": f"mark_read:{ad['id']}"}]
        ]
    }
    
    send_telegram_message(message, reply_markup)

def extract_ad_data(ad_element):
    """Витягує дані з елемента оголошення"""
    try:
        link_tag = ad_element.find('a', href=True)
        if not link_tag:
            return None
            
        href = link_tag.get('href', '')
        if href.startswith('/'):
            full_link = 'https://www.olx.ua' + href
        elif href.startswith('http'):
            full_link = href
        else:
            full_link = 'https://www.olx.ua/' + href
        
        # ПОШУК ЗАГОЛОВКУ
        title = "Без назви"
        
        title_match = re.search(r'/obyavlenie/([^/]+)-', full_link)
        if title_match:
            title_from_url = title_match.group(1).replace('-', ' ').title()
            if len(title_from_url) > 5:
                title = title_from_url
        
        title_selectors = [
            ('h4', {'class': re.compile(r'title|heading|css-')}),
            ('h6', {'class': re.compile(r'title|heading|css-')}),
            ('a', {'class': re.compile(r'title|link|css-')}),
            ('span', {'class': re.compile(r'title|text|css-')}),
            ('div', {'data-testid': 'title'}),
        ]
        
        for tag, attrs in title_selectors:
            title_tag = ad_element.find(tag, attrs)
            if title_tag and title_tag.text.strip():
                candidate = title_tag.text.strip()
                if len(candidate) > 3 and not candidate.startswith('OLX'):
                    title = candidate
                    break
        
        # ПОШУК ЦІНИ
        price = "Ціна не вказана"
        price_selectors = [
            ('p', {'data-testid': 'ad-price'}),
            ('p', {'class': re.compile(r'price|css-')}),
            ('div', {'class': re.compile(r'price|css-')}),
            ('span', {'class': re.compile(r'price|css-')}),
        ]
        
        for tag, attrs in price_selectors:
            price_tag = ad_element.find(tag, attrs)
            if price_tag and price_tag.text.strip():
                price_candidate = price_tag.text.strip()
                if re.search(r'[\d\s]+[₴$]|грн|\$', price_candidate):
                    price = price_candidate
                    break
        
        # ID оголошення
        ad_id = ad_element.get('id')
        if not ad_id:
            id_match = re.search(r'-([0-9]+)\.html', full_link)
            if id_match:
                ad_id = id_match.group(1)
            else:
                ad_id = hashlib.md5(full_link.encode()).hexdigest()[:10]
        
        return {
            'id': str(ad_id),
            'title': title[:100],
            'price': price,
            'link': full_link
        }
        
    except Exception as e:
        print(f"Помилка обробки елемента: {e}")
        return None

def parse_olx_page():
    """Отримує HTML сторінки та повертає список нових оголошень"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'uk,ru;q=0.9,en;q=0.8',
    }
    
    proxies = get_proxies()
    
    try:
        print(f"🌐 Запит до OLX з проксі: {proxies if proxies else 'без проксі'}")
        response = requests.get(OLX_URL, headers=headers, proxies=proxies, timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        ads_elements = []
        listing_grid = soup.find('div', {'data-testid': 'listing-grid'})
        if listing_grid:
            ads_elements = listing_grid.find_all('div', {'data-cy': 'l-card'})
        
        if not ads_elements:
            ads_elements = soup.find_all('div', class_=re.compile(r'css-.*|offer|card'))
        
        new_ads = []
        for ad_element in ads_elements[:30]:
            ad_data = extract_ad_data(ad_element)
            if ad_data:
                new_ads.append(ad_data)
        
        return new_ads
        
    except requests.exceptions.Timeout:
        print("⏱️ Таймаут при парсингу OLX")
    except requests.exceptions.ConnectionError as e:
        print(f"🔌 Помилка з'єднання з OLX: {e}")
    except Exception as e:
        print(f"❌ Помилка парсингу: {e}")
    return []

def load_seen_ids():
    """Завантажує ID вже відправлених оголошень з файлу"""
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, 'r') as f:
        return set(line.strip() for line in f if line.strip())

def save_seen_ids(seen_ids):
    """Зберігає ID відправлених оголошень у файл"""
    with open(SEEN_FILE, 'w') as f:
        for ad_id in sorted(seen_ids):
            f.write(f"{ad_id}\n")

def process_callback(callback):
    """Обробляє callback від кнопки"""
    proxies = get_proxies()
    
    try:
        callback_data = callback.get("data", "")
        callback_id = callback.get("id", "")
        from_user = callback.get("from", {})
        user_id = from_user.get("id", "unknown")
        user_name = from_user.get("first_name", "unknown")
        
        print(f"\n🔔 ОТРИМАНО CALLBACK!")
        print(f"   ID: {callback_id}")
        print(f"   Дані: {callback_data}")
        print(f"   Від користувача: {user_name} (ID: {user_id})")
        
        if callback_data.startswith("mark_read:"):
            ad_id = callback_data.split(":", 1)[1]
            print(f"   ✅ Позначаємо оголошення {ad_id} як прочитане")
            
            # Відповідаємо на callback (прибираємо "годинник" на кнопці)
            answer_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery"
            answer_data = {
                "callback_query_id": callback_id,
                "text": "✅ Оголошення позначено як прочитане!",
                "show_alert": False
            }
            
            print(f"   Відправляємо answerCallbackQuery...")
            answer_response = requests.post(answer_url, json=answer_data, proxies=proxies, timeout=30)
            print(f"   Відповідь на callback: {answer_response.status_code}")
            
            if answer_response.status_code == 200:
                print(f"   ✅ Callback підтверджено")
            else:
                print(f"   ❌ Помилка answerCallbackQuery: {answer_response.text}")
            
            # Редагуємо повідомлення - видаляємо кнопку
            if "message" in callback:
                chat_id = callback["message"]["chat"]["id"]
                message_id = callback["message"]["message_id"]
                
                print(f"   Редагуємо повідомлення {message_id} в чаті {chat_id}...")
                
                edit_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageReplyMarkup"
                
                edit_data = {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "reply_markup": json.dumps({"inline_keyboard": []})
                }
                
                edit_response = requests.post(edit_url, json=edit_data, proxies=proxies, timeout=30)
                print(f"   Редагування повідомлення: {edit_response.status_code}")
                
                if edit_response.status_code == 200:
                    print(f"   ✅ Кнопку видалено для повідомлення {message_id}")
                else:
                    print(f"   ❌ Помилка редагування: {edit_response.text}")
            else:
                print(f"   ❌ Немає інформації про повідомлення в callback")
    
    except Exception as e:
        print(f"❌ Помилка обробки callback: {e}")
        import traceback
        traceback.print_exc()

def get_updates():
    """Отримує оновлення від Telegram"""
    global last_update_id
    proxies = get_proxies()
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    
    params = {
        "timeout": 30,
        "offset": last_update_id + 1 if last_update_id > 0 else None,
        "allowed_updates": ["callback_query"]
    }
    
    print(f"\n🔄 Перевіряємо оновлення Telegram (offset: {last_update_id})...")
    
    try:
        response = requests.get(url, params=params, proxies=proxies, timeout=35)
        data = response.json()
        
        if data.get("ok"):
            updates = data.get("result", [])
            if updates:
                print(f"📨 Отримано {len(updates)} оновлень")
                
                for update in updates:
                    update_id = update["update_id"]
                    print(f"\n📦 Оновлення #{update_id}")
                    
                    if update_id > last_update_id:
                        last_update_id = update_id
                        print(f"   Оновлено offset до {last_update_id}")
                    
                    if "callback_query" in update:
                        print(f"   ⚡ Знайдено callback_query!")
                        process_callback(update["callback_query"])
                    else:
                        print(f"   Інший тип оновлення: {list(update.keys())}")
            else:
                print("📭 Нових оновлень немає")
        else:
            print(f"❌ Помилка отримання оновлень: {data}")
    
    except requests.exceptions.Timeout:
        print("⏱️ Таймаут очікування оновлень (нормально для long polling)")
        pass
    except Exception as e:
        print(f"❌ Помилка отримання оновлень: {e}")

def polling_worker():
    """Функція для постійного опитування Telegram API"""
    print("🔄 Запущено поток для обробки callback-запитів")
    while True:
        try:
            get_updates()
            time.sleep(1)  # Невелика затримка між запитами
        except Exception as e:
            print(f"❌ Помилка в polling_worker: {e}")
            time.sleep(5)

def main():
    print("=" * 60)
    print("ЗАПУСК БОТА МОНІТОРИНГУ OLX")
    print("=" * 60)
    
    # Виводимо інформацію про проксі
    proxies = get_proxies()
    if proxies:
        print(f"\n🌐 Використовуються проксі: {proxies}")
        print(f"   HTTP_PROXY: {HTTP_PROXY}")
        print(f"   HTTPS_PROXY: {HTTPS_PROXY}")
    else:
        print("\n🌐 Проксі не налаштовані, використовується пряме з'єднання")
    
    # Спочатку видаляємо webhook
    print("\n🔍 Перевіряємо та видаляємо webhook...")
    if not delete_webhook():
        print("⚠️ Не вдалося видалити webhook, але спробуємо продовжити...")
    
    time.sleep(2)  # Чекаємо після видалення webhook
    
    # Перевіряємо підключення до Telegram API
    print("\n🔍 Перевіряємо підключення до Telegram API...")
    try:
        test_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMe"
        test_response = requests.get(test_url, proxies=proxies, timeout=30)
        if test_response.status_code == 200:
            bot_info = test_response.json()
            print(f"✅ Бот підключено: @{bot_info['result']['username']}")
        else:
            print(f"❌ Помилка підключення: {test_response.text}")
            print("   Спробуйте перевірити проксі або токен")
            return
    except requests.exceptions.Timeout:
        print("❌ Таймаут при підключенні до Telegram")
        print("   Можливо, проксі не працюють")
        return
    except requests.exceptions.ConnectionError as e:
        print(f"❌ Помилка з'єднання з Telegram: {e}")
        print("   Перевірте проксі та інтернет-з'єднання")
        return
    except Exception as e:
        print(f"❌ Помилка підключення: {e}")
        return
    
    # Запускаємо окремий потік для обробки callback-запитів
    polling_thread = threading.Thread(target=polling_worker, daemon=True)
    polling_thread.start()
    print("✅ Потік для обробки кнопок запущено")
    
    # Відправляємо тестове повідомлення
    print("\n📱 Надсилаємо тестове повідомлення в Telegram...")
    send_telegram_message("🤖 Бот моніторингу OLX запущено!\n\nТестове повідомлення для перевірки зв'язку.")
    
    seen_ids = load_seen_ids()
    print(f"📂 Завантажено {len(seen_ids)} ID з файлу {SEEN_FILE}")

    while True:
        print(f"\n{'='*60}")
        print(f"🕐 Перевірка о {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}")
        
        # Парсимо нові оголошення
        current_ads = parse_olx_page()

        if not current_ads:
            print("⚠️ Не вдалося отримати оголошення.")
            time.sleep(CHECK_INTERVAL)
            continue

        # Шукаємо нові оголошення
        new_ads_found = False
        new_count = 0
        
        for ad in current_ads:
            if ad['id'] not in seen_ids:
                print(f"\n📢 НОВЕ ОГОЛОШЕННЯ #{new_count+1}")
                print(f"   📌 {ad['title']}")
                print(f"   💰 {ad['price']}")
                print(f"   🆔 {ad['id']}")
                
                # Надсилаємо оголошення з кнопкою
                send_ad_with_button(ad)
                seen_ids.add(ad['id'])
                new_ads_found = True
                new_count += 1
                time.sleep(2)

        if new_ads_found:
            save_seen_ids(seen_ids)
            print(f"\n💾 Збережено {len(seen_ids)} ID у файл")
            print(f"✅ Додано {new_count} нових оголошень")
        else:
            print("\n📭 Нових оголошень не знайдено")

        print(f"\n📊 Всього в базі: {len(seen_ids)} оголошень.")
        print(f"⏰ Наступна перевірка через {CHECK_INTERVAL//60} хвилин")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Бот зупинений користувачем")
    except Exception as e:
        print(f"\n❌ Критична помилка: {e}")
        import traceback
        traceback.print_exc()