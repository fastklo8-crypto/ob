from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, ChatMemberUpdatedFilter
from aiogram.types import ChatMemberUpdated, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
import asyncio
import logging
from datetime import datetime, date
import json
import os
import aiohttp
from bs4 import BeautifulSoup
import re
import random
import cloudscraper

class CloudFlareParser:
    def __init__(self):
        self.scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'mobile': False,
            }
        )
    
    async def fetch_with_cloudflare(self, url: str):
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, 
            lambda: self.scraper.get(url, timeout=30)
        )
        return response.text

# ========== КОНФИГУРАЦИЯ ==========
TELEGRAM_BOT_TOKEN = "8521669515:AAFMhXlWv_clmqvqN2VrNgXtU-yJdHVKwdc"
# Ваш user ID в Telegram (узнать можно у бота @userinfobot)
YOUR_USER_ID = 1070744113  # Уже указан правильно

# Настройки парсинга OLX - Деснянский район Киева
OLX_BASE_URL = "https://www.olx.ua"
SEARCH_URL = f"{OLX_BASE_URL}/uk/kiev/"  # Все объявления в Киеве
PARAMS = {
    "search[district_id]": "5",  # Деснянский район
    "search[order]": "created_at:desc",  # Сортировка по новизне
}

# ⚠️ ВАЖНО: Увеличиваем задержки для избежания лимитов Telegram
PARSE_INTERVAL = 60  # 1 минута между проверками
MESSAGE_DELAY = 2  # 2 секунды между отправками сообщений

# Лимиты отправки
DAILY_LIMIT = 5000  # План на день - 1000 сообщений

# Файлы для хранения данных
PROCESSED_ADS_FILE = "processed_ads.json"
STATS_FILE = "stats.json"

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# ========== ГЛОБАЛЬНЫЕ ФЛАГИ ==========
is_sending_active = False  # Флаг активности отправки сообщений
# ========== ХРАНИЛИЩЕ ДАННЫХ ==========
class Storage:
    def __init__(self, filename: str):
        self.filename = filename
        self.processed_ads = self._load_processed_ads()
    
    def _load_processed_ads(self) -> set:
        """Загружаем обработанные объявления из файла"""
        if os.path.exists(self.filename):
            try:
                with open(self.filename, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return set(data.get('processed_ads', []))
            except Exception as e:
                logger.error(f"Ошибка загрузки файла: {e}")
        return set()
    
    def save_processed_ads(self):
        """Сохраняем обработанные объявления в файл"""
        try:
            data = {'processed_ads': list(self.processed_ads)}
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения файла: {e}")
    
    def add_processed_ad(self, ad_id: str):
        """Добавляем ID объявления в обработанные"""
        self.processed_ads.add(ad_id)
    
    def is_processed(self, ad_id: str) -> bool:
        """Проверяем, было ли объявление обработано"""
        return ad_id in self.processed_ads
# ========== ДОБАВЛЯЕМ В НАЧАЛО ФАЙЛА ПЕРЕД Storage КЛАССОМ ==========

# Менеджер для отслеживания пользователей OLX (продавцов)
class OlxUserManager:
    def __init__(self, filename: str = "olx_users.json"):
        self.filename = filename
        self.olx_users = self._load_olx_users()
    
    def _load_olx_users(self) -> dict:
        """Загружаем пользователей OLX из файла"""
        if os.path.exists(self.filename):
            try:
                with open(self.filename, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Ошибка загрузки пользователей OLX: {e}")
        return {}
    
    def save_olx_users(self):
        """Сохраняем пользователей OLX в файл"""
        try:
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump(self.olx_users, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения пользователей OLX: {e}")
    
    def add_olx_user(self, user_id: str, ad_id: str):
        """Добавляем пользователя OLX с его объявлением"""
        if user_id not in self.olx_users:
            self.olx_users[user_id] = {
                'first_ad_id': ad_id,
                'first_ad_time': str(datetime.now()),
                'last_seen': str(datetime.now()),
                'ad_count': 1,
                'sent_ads': [ad_id]
            }
        else:
            self.olx_users[user_id]['last_seen'] = str(datetime.now())
            self.olx_users[user_id]['ad_count'] += 1
            
        self.save_olx_users()
    
    def has_sent_ad_for_user(self, user_id: str, ad_id: str) -> bool:
        """Проверяем, отправляли ли мы уже объявление от этого пользователя"""
        if user_id in self.olx_users:
            # Проверяем, отправляли ли мы это конкретное объявление
            if ad_id in self.olx_users[user_id]['sent_ads']:
                return True
        return False
    
    def can_send_ad_for_user(self, user_id: str) -> bool:
        """Можно ли отправлять объявление от этого пользователя"""
        # Если это новый пользователь - можно отправлять
        if user_id not in self.olx_users:
            return True
        
        # Проверяем, есть ли у пользователя уже отправленные объявления
        user_data = self.olx_users[user_id]
        
        # Если у пользователя есть только первое объявление (ad_count = 1)
        # И это объявление еще не отправлялось конкретно этому пользователю Telegram
        if user_data['ad_count'] == 1:
            # Проверяем, не отправляли ли мы уже это объявление
            return not self.has_sent_ad_for_user(user_id, user_data.get('first_ad_id', ''))
        
        # Для пользователей с несколькими объявлениями используем другую логику
        # Например, отправляем только если не отправляли в последние 24 часа
        last_seen_str = user_data.get('last_seen', '')
        if last_seen_str:
            try:
                last_seen = datetime.fromisoformat(last_seen_str)
                time_diff = datetime.now() - last_seen
                # Если с последней отправки прошло больше 24 часов - можно отправлять снова
                if time_diff.total_seconds() > 8640000000:  # 24 часа
                    return True
            except:
                pass
        
        return False
    
    def mark_ad_sent_for_user(self, user_id: str, ad_id: str):
        """Отмечаем, что объявление от пользователя отправлено"""
        if user_id in self.olx_users:
            if ad_id not in self.olx_users[user_id]['sent_ads']:
                self.olx_users[user_id]['sent_ads'].append(ad_id)
        else:
            self.olx_users[user_id] = {
                'first_ad_id': ad_id,
                'first_ad_time': str(datetime.now()),
                'last_seen': str(datetime.now()),
                'ad_count': 1,
                'sent_ads': [ad_id]
            }
        
        self.save_olx_users()
    
    def get_user_stats(self, user_id: str) -> dict:
        """Получаем статистику по пользователю OLX"""
        if user_id in self.olx_users:
            return self.olx_users[user_id]
        return {}

# ========== МЕНЕДЖЕР ПРОСМОТРЕННЫХ ОБЪЯВЛЕНИЙ ==========
class ViewedAdsManager:
    def __init__(self, filename: str = "viewed_ads.json"):
        self.filename = filename
        self.viewed_ads = self._load_viewed_ads()
    
    def _load_viewed_ads(self) -> dict:
        """Загружаем просмотренные объявления из файла"""
        if os.path.exists(self.filename):
            try:
                with open(self.filename, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Ошибка загрузки просмотренных объявлений: {e}")
        return {}
    
    def save_viewed_ads(self):
        """Сохраняем просмотренные объявления в файл"""
        try:
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump(self.viewed_ads, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения просмотренных объявлений: {e}")
    
    def mark_as_viewed(self, ad_id: str, message_id: int, user_id: int):
        """Помечаем объявление как просмотренное"""
        key = f"{user_id}:{message_id}:{ad_id}"
        if key not in self.viewed_ads:
            self.viewed_ads[key] = {
                'viewed_at': str(datetime.now()),
                'ad_id': ad_id,
                'message_id': message_id,
                'user_id': user_id
            }
            self.save_viewed_ads()
            return True
        return False
    
    def is_viewed(self, ad_id: str, message_id: int, user_id: int) -> bool:
        """Проверяем, было ли объявление просмотрено"""
        key = f"{user_id}:{message_id}:{ad_id}"
        return key in self.viewed_ads

# ========== МЕНЕДЖЕР СТАТИСТИКИ ==========
class StatsManager:
    def __init__(self, filename: str):
        self.filename = filename
        self.stats = self._load_stats()
    
    def _load_stats(self) -> dict:
        """Загружаем статистику из файла"""
        if os.path.exists(self.filename):
            try:
                with open(self.filename, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Ошибка загрузки статистики: {e}")
        
        # Стандартная структура статистики
        return {
            'daily': {
                'date': str(date.today()),
                'sent': 0,
                'failed': 0,
                'resent': 0,
                'viewed': 0
            },
            'total_sent': 0,
            'total_failed': 0,
            'total_resent': 0,
            'total_viewed': 0,
            'last_ads': []
        }
    
    def save_stats(self):
        """Сохраняем статистику в файл"""
        try:
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump(self.stats, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения статистики: {e}")
    
    def reset_daily_if_needed(self):
        """Сбрасываем дневную статистику если сменился день"""
        today = str(date.today())
        if self.stats['daily']['date'] != today:
            self.stats['daily'] = {
                'date': today,
                'sent': 0,
                'failed': 0,
                'resent': 0,
                'viewed': 0
            }
            self.save_stats()
    
    def increment_sent(self):
        """Увеличиваем счетчик отправленных"""
        self.reset_daily_if_needed()
        self.stats['daily']['sent'] += 1
        self.stats['total_sent'] += 1
        self.save_stats()
    
    def increment_failed(self):
        """Увеличиваем счетчик неудачных отправок"""
        self.reset_daily_if_needed()
        self.stats['daily']['failed'] += 1
        self.stats['total_failed'] += 1
        self.save_stats()
    
    def increment_resent(self):
        """Увеличиваем счетчик повторных отправок"""
        self.reset_daily_if_needed()
        self.stats['daily']['resent'] += 1
        self.stats['total_resent'] += 1
        self.save_stats()
    
    def increment_viewed(self):
        """Увеличиваем счетчик просмотренных"""
        self.reset_daily_if_needed()
        self.stats['daily']['viewed'] += 1
        self.stats['total_viewed'] += 1
        self.save_stats()
    
    def get_daily_stats(self) -> dict:
        """Получаем дневную статистику"""
        self.reset_daily_if_needed()
        return self.stats['daily']
    
    def get_remaining_daily(self) -> int:
        """Сколько осталось отправить сегодня"""
        self.reset_daily_if_needed()
        sent_today = self.stats['daily']['sent']
        remaining = max(0, DAILY_LIMIT - sent_today)
        return remaining
    
    def add_sent_ad(self, ad_id: str, message_id: int, user_id: int, link: str, title: str, olx_user_id: str = ""):
        """Добавляем отправленное объявление в историю"""
        ad_info = {
            'ad_id': ad_id,
            'message_id': message_id,
            'user_id': user_id,
            'link': link,
            'title': title,
            'sent_time': str(datetime.now()),
            'resent_count': 0,
            'olx_user_id': olx_user_id  
        }
        
        self.stats['last_ads'].insert(0, ad_info)
        if len(self.stats['last_ads']) > 20:
            self.stats['last_ads'] = self.stats['last_ads'][:20]
        
        self.save_stats()
    
    def get_ad_by_message_id(self, message_id: int, user_id: int):
        """Находим объявление по ID сообщения"""
        for ad in self.stats['last_ads']:
            if ad['message_id'] == message_id and str(ad['user_id']) == str(user_id):
                return ad
        return None
    
    def get_ad_by_id(self, ad_id: str, user_id: int):
        """Находим объявление по ID объявления"""
        for ad in self.stats['last_ads']:
            if ad['ad_id'] == ad_id and str(ad['user_id']) == str(user_id):
                return ad
        return None

# Инициализация хранилищ
storage = Storage(PROCESSED_ADS_FILE)
stats_manager = StatsManager(STATS_FILE)
viewed_manager = ViewedAdsManager()

# Словарь для хранения ссылок на объявления (временно в памяти)
ad_links_cache = {}

# ========== Список User-Agent для ротации ==========
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1',
]

def get_random_headers():
    """Генерирует случайные заголовки для запросов"""
    return {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
        'DNT': '1',
    }

# ========== ПАРСЕР OLX ==========
import cloudscraper
from fake_useragent import UserAgent
olx_user_manager = OlxUserManager()

# ========== МОДИФИЦИРУЕМ КЛАСС OLXAPI ==========

class OLXAPI:
    def __init__(self):
        self.scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'mobile': False
            }
        )
        self.ua = UserAgent()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass
    
    async def fetch_page(self, url: str, params: dict = None) -> str:
        """Получаем HTML страницу с использованием cloudscraper"""
        try:
            # Добавляем случайную задержку
            await asyncio.sleep(random.uniform(2, 4))
            
            # Формируем полный URL с параметрами
            full_url = url
            if params:
                import urllib.parse
                full_url = f"{url}?{urllib.parse.urlencode(params)}"
            
            # Используем cloudscraper для обхода Cloudflare
            headers = {
                'User-Agent': self.ua.random,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'uk-UA,uk;q=0.9,ru;q=0.8,en-US;q=0.7,en;q=0.6',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
                'Cache-Control': 'max-age=0',
                'DNT': '1',
                'Referer': 'https://www.olx.ua/',
            }
            
            # Используем asyncio.to_thread для запуска синхронного cloudscraper
            loop = asyncio.get_event_loop()
            html = await loop.run_in_executor(
                None,
                lambda: self.scraper.get(
                    full_url,
                    headers=headers,
                    timeout=30
                ).text
            )
            
            return html
            
        except Exception as e:
            logger.error(f"Ошибка при запросе {url}: {e}")
            return ""
    
    def parse_ads_from_html(self, html: str) -> list:
        """Парсим список объявлений из HTML"""
        ads = []
        if not html:
            return ads
        
        try:
            soup = BeautifulSoup(html, 'lxml')
            
            # Пробуем разные селекторы для поиска объявлений
            ad_cards = soup.find_all('div', {'data-cy': 'l-card'})
            
            # Если не нашли, пробуем другие селекторы
            if not ad_cards:
                ad_cards = soup.find_all('div', class_=re.compile(r'css-'))
            
            if not ad_cards:
                ad_cards = soup.find_all('div', class_=re.compile(r'offer-wrapper'))
            
            for card in ad_cards:
                try:
                    # Ищем ссылку
                    link_tag = card.find('a', href=True)
                    if not link_tag:
                        continue
                    
                    link = link_tag['href']
                    if not link.startswith('http'):
                        link = OLX_BASE_URL + link
                    
                    # Извлекаем ID объявления
                    ad_id = ""
                    if '/obyavlenie/' in link:
                        parts = link.split('/obyavlenie/')[-1]
                        if '-ID' in parts.upper():
                            ad_id = parts.split('-ID')[-1].split('.')[0].strip()
                        else:
                            ad_id = parts.split('-')[-1].split('.')[0].strip()
                    
                    if not ad_id or len(ad_id) < 3:
                        # Пробуем извлечь из URL другим способом
                        match = re.search(r'ID([A-Z0-9]+)', link.upper())
                        if match:
                            ad_id = match.group(1)
                        else:
                            continue
                    
                    # Извлекаем заголовок
                    title = "Объявление с OLX"
                    title_tag = card.find('h6') or card.find('strong') or card.find('span', class_=re.compile(r'title'))
                    if title_tag:
                        title = title_tag.text.strip()
                    
                    title = re.sub(r'\s+', ' ', title).strip()
                    title = title[:100]  # Обрезаем слишком длинные заголовки
                    
                    # Сохраняем ссылку в кэш
                    ad_links_cache[ad_id] = link
                    
                    # Инициализируем olx_user_id как пустую строку
                    olx_user_id = ""
                    
                    # Пробуем найти ссылку на пользователя в карточке
                    user_tag = card.find('a', href=re.compile(r'/list/user/'))
                    if user_tag and 'href' in user_tag.attrs:
                        href = user_tag['href']
                        match = re.search(r'/list/user/([^/?#]+)', href)
                        if match:
                            olx_user_id = match.group(1)
                    
                    ads.append({
                        'id': ad_id,
                        'title': title,
                        'link': link,
                        'olx_user_id': olx_user_id  # Добавляем поле, даже если пустое
                    })
                    
                except Exception as e:
                    logger.debug(f"Ошибка парсинга карточки: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Ошибка парсинга HTML: {e}")
        
        return ads
    
    async def get_new_ads(self) -> list:
        """Получаем новые объявления с фильтрацией по пользователям"""
        logger.info(f"Запрос к OLX: {SEARCH_URL} с параметрами {PARAMS}")
        
        # Пробуем несколько вариантов URL
        urls_to_try = [
            SEARCH_URL,
            "https://www.olx.ua/uk/list/",
            "https://www.olx.ua/d/uk/"
        ]
        
        html = ""
        for url in urls_to_try:
            html = await self.fetch_page(url, params=PARAMS)
            
            if html and len(html) > 5000:
                logger.info(f"Успешно получили данные с {url}")
                break
            else:
                logger.warning(f"Не удалось получить данные с {url}, пробуем следующий...")
        
        if not html or len(html) < 5000:
            logger.error("Не удалось получить HTML страницу со всех URL")
            return []
        
        all_ads = self.parse_ads_from_html(html)
        new_ads = []
        
        logger.info(f"На странице найдено {len(all_ads)} объявлений")
        
        for ad in all_ads:
            # Проверяем, было ли объявление уже обработано
            if storage.is_processed(ad['id']):
                continue
            
            # Если у объявления нет ID пользователя OLX в карточке,
            # парсим страницу объявления для получения ID пользователя
            if not ad.get('olx_user_id'):  # Используем get() для безопасного доступа
                try:
                    ad_details = await self.fetch_ad_details(ad['link'])
                    if ad_details.get('olx_user_id'):
                        ad['olx_user_id'] = ad_details['olx_user_id']
                        logger.info(f"Найден ID пользователя OLX для объявления {ad['id']}: {ad['olx_user_id']}")
                except Exception as e:
                    logger.warning(f"Не удалось получить ID пользователя для объявления {ad['id']}: {e}")
            
            # Проверяем, можно ли отправлять объявление от этого пользователя
            if ad.get('olx_user_id'):  # Используем get() для безопасного доступа
                # Если уже отправляли объявление от этого пользователя - пропускаем
                if not olx_user_manager.can_send_ad_for_user(ad['olx_user_id']):
                    logger.info(f"Пропускаем объявление {ad['id']} от пользователя {ad['olx_user_id']} - уже отправляли")
                    
                    # Но все равно помечаем как обработанное, чтобы не парсить снова
                    storage.add_processed_ad(ad['id'])
                    continue
            
            # Добавляем объявление в список для отправки
            new_ads.append(ad)
            storage.add_processed_ad(ad['id'])
            
            # Если у объявления есть ID пользователя, отмечаем что от этого пользователя будем отправлять
            if ad.get('olx_user_id'):  # Используем get() для безопасного доступа
                olx_user_manager.add_olx_user(ad['olx_user_id'], ad['id'])
        
        if new_ads:
            storage.save_processed_ads()
            logger.info(f"Добавлено {len(new_ads)} новых объявлений в обработку")
        
        return new_ads
    
    async def fetch_ad_details(self, url: str) -> dict:
        """Получаем детальную информацию об объявлении"""
        try:
            html = await self.fetch_page(url)
            if not html:
                return {}
            
            soup = BeautifulSoup(html, 'lxml')
            
            # Извлекаем ID пользователя OLX
            olx_user_id = ""
            
            # Пробуем разные способы извлечения ID пользователя
            # 1. Ищем ссылку на профиль пользователя
            user_link = soup.find('a', href=re.compile(r'/list/user/'))
            if user_link and 'href' in user_link.attrs:
                href = user_link['href']
                match = re.search(r'/list/user/([^/?#]+)', href)
                if match:
                    olx_user_id = match.group(1)
            
            # 2. Ищем в данных объявления
            if not olx_user_id:
                script_tags = soup.find_all('script')
                for script in script_tags:
                    if script.string:
                        # Ищем в JSON данных
                        matches = re.findall(r'"user_id"\s*:\s*"([^"]+)"', script.string)
                        if matches:
                            olx_user_id = matches[0]
                            break
                        
                        matches = re.findall(r'"sellerId"\s*:\s*"([^"]+)"', script.string)
                        if matches:
                            olx_user_id = matches[0]
                            break
            
            # 3. Ищем в meta-тегах
            if not olx_user_id:
                meta_tags = soup.find_all('meta')
                for meta in meta_tags:
                    if 'property' in meta.attrs and 'content' in meta.attrs:
                        if 'user_id' in meta.attrs.get('property', '').lower():
                            olx_user_id = meta.attrs['content']
                            break
            
            return {
                'olx_user_id': olx_user_id,
                'url': url
            }
            
        except Exception as e:
            logger.error(f"Ошибка при получении деталей объявления {url}: {e}")
            return {}
        

def create_ad_keyboard(ad_id: str, message_id: int, user_id: int) -> InlineKeyboardMarkup:
    """Создаем клавиатуру для объявления - ОДНА КНОПКА, которая меняется"""
    builder = InlineKeyboardBuilder()
    
    # Проверяем, было ли объявление уже просмотрено
    is_viewed = viewed_manager.is_viewed(ad_id, message_id, user_id)
    
    if is_viewed:
        # Если просмотрено - показываем галочку (callback кнопка)
        builder.row(
            InlineKeyboardButton(
                text="✅ Прочитано",
                callback_data=f"vi:{ad_id}:{message_id}"
            )
        )
    else:
        # Если не просмотрено - показываем кнопку для открытия
        builder.row(
            InlineKeyboardButton(
                text="📱 Открыть объявление",
                callback_data=f"oa:{ad_id}:{message_id}"
            )
        )
    
    # Добавляем статистику
    remaining = stats_manager.get_remaining_daily()
    sent_today = stats_manager.get_daily_stats()['sent']
    
    builder.row(
        InlineKeyboardButton(
            text=f"📊 {sent_today}/{DAILY_LIMIT}",
            callback_data="si"
        )
    )
    
    return builder.as_markup()


# ========== ОТПРАВКА ОБЪЯВЛЕНИЙ В ЛИЧКУ ==========
async def send_ad_to_user(ad: dict, user_id: int, retry_count: int = 3) -> bool:
    """Отправляем ссылку на объявление в личные сообщения с фильтрацией по пользователям OLX"""
    for attempt in range(retry_count):
        try:
            # Проверяем дневной лимит
            remaining = stats_manager.get_remaining_daily()
            if remaining <= 0:
                logger.warning(f"⚠️ Достигнут дневной лимит ({DAILY_LIMIT} сообщений)")
                return False
            
            # Проверяем, можно ли отправлять от этого пользователя OLX
            if ad.get('olx_user_id'):
                if not olx_user_manager.can_send_ad_for_user(ad['olx_user_id']):
                    logger.info(f"⚠️ Пропускаем объявление {ad['id']} - уже отправляли от пользователя {ad['olx_user_id']}")
                    return False
            
            # Форматируем сообщение с информацией о пользователе OLX
            if ad.get('olx_user_id'):
                message_text = (
                    f"{ad['link']}\n\n"
                    f"👤 Продавец OLX: {ad['olx_user_id']}\n"
                    f"📝 {ad['title']}"
                )
            else:
                message_text = f"{ad['link']}\n\n📝 {ad['title']}"
            
            # Создаем клавиатуру с правильным message_id (пока 0, потом обновим)
            keyboard = create_ad_keyboard(ad['id'], 0, user_id)
            
            # Отправляем сообщение в личные сообщения
            sent_message = await bot.send_message(
                chat_id=user_id,
                text=message_text,
                disable_web_page_preview=False,
                reply_markup=keyboard
            )
            
            # Теперь обновляем клавиатуру с правильным message_id
            updated_keyboard = create_ad_keyboard(ad['id'], sent_message.message_id, user_id)
            
            try:
                await bot.edit_message_reply_markup(
                    chat_id=user_id,
                    message_id=sent_message.message_id,
                    reply_markup=updated_keyboard
                )
            except TelegramBadRequest as e:
                logger.warning(f"Не удалось обновить клавиатуру: {e}")
            
            logger.info(f"✅ Отправлена ссылка пользователю {user_id}: {ad['id']} - {ad['title'][:50]}...")
            
            # Обновляем статистику
            stats_manager.increment_sent()
            stats_manager.add_sent_ad(
                ad_id=ad['id'],
                message_id=sent_message.message_id,
                user_id=user_id,
                link=ad['link'],
                title=ad['title'],
                olx_user_id=ad.get('olx_user_id', '')
            )
            
            # Если у объявления есть ID пользователя OLX, отмечаем что отправили
            if ad.get('olx_user_id'):
                olx_user_manager.mark_ad_sent_for_user(ad['olx_user_id'], ad['id'])
                logger.info(f"📝 Отметили объявление от пользователя OLX: {ad['olx_user_id']}")
            
            return True
            
        except Exception as e:
            error_msg = str(e)
            if "retry after" in error_msg:
                match = re.search(r'retry after (\d+)', error_msg)
                if match:
                    wait_time = int(match.group(1))
                    logger.warning(f"⚠️ Лимит Telegram. Ждем {wait_time} секунд...")
                    await asyncio.sleep(wait_time + 1)
                    continue
            elif "Flood control" in error_msg:
                logger.warning(f"⚠️ Flood control. Ждем 5 секунд...")
                await asyncio.sleep(5)
                continue
            elif "BUTTON_DATA_INVALID" in error_msg:
                logger.error(f"❌ Ошибка в данных кнопки. Пробуем упрощенную версию...")
                # Пробуем отправить без сложной клавиатуры
                try:
                    return await send_ad_simple(ad, user_id)
                except Exception as e2:
                    logger.error(f"❌ Ошибка при простой отправке: {e2}")
                    if attempt < retry_count - 1:
                        await asyncio.sleep(2)
                    continue
            elif "Forbidden: bot was blocked by the user" in error_msg:
                logger.error(f"❌ Бот заблокирован пользователем {user_id}")
                return False
            elif "chat not found" in error_msg.lower():
                logger.error(f"❌ Чат не найден или пользователь не начал диалог с ботом: {user_id}")
                return False
            else:
                logger.error(f"❌ Ошибка отправки объявления {ad['id']} (попытка {attempt + 1}/{retry_count}): {e}")
                if attempt < retry_count - 1:
                    await asyncio.sleep(2)
    
    stats_manager.increment_failed()
    logger.error(f"❌ Не удалось отправить объявление {ad['id']} после {retry_count} попыток")
    
    # Даже если не удалось отправить, но ID пользователя есть - отмечаем
    if ad.get('olx_user_id'):
        olx_user_manager.mark_ad_sent_for_user(ad['olx_user_id'], ad['id'])
        logger.info(f"📝 Объявление не отправлено, но пользователь OLX отмечен: {ad['olx_user_id']}")
    
    return False

async def send_ad_simple(ad: dict, user_id: int) -> bool:
    """Простая отправка объявления без сложной клавиатуры"""
    try:
        # Проверяем дневной лимит
        remaining = stats_manager.get_remaining_daily()
        if remaining <= 0:
            return False
        
        # Форматируем сообщение с информацией о пользователе OLX
        if ad.get('olx_user_id'):
            message_text = (
                f"{ad['link']}\n\n"
                f"👤 Продавец OLX: {ad['olx_user_id']}\n"
                f"📝 {ad['title']}"
            )
        else:
            message_text = f"{ad['link']}\n\n📝 {ad['title']}"
        
        # Простая клавиатура с URL кнопкой
        builder = InlineKeyboardBuilder()
        
        # Кнопка для открытия (URL кнопка)
        builder.row(
            InlineKeyboardButton(
                text="📱 Открыть объявление",
                url=ad['link']
            )
        )
        
        # Кнопка для пометки как прочитанного
        builder.row(
            InlineKeyboardButton(
                text="✅ Отметить как прочитанное",
                callback_data=f"mr:{ad['id']}"
            )
        )
        
        keyboard = builder.as_markup()
        
        sent_message = await bot.send_message(
            chat_id=user_id,
            text=message_text,
            disable_web_page_preview=False,
            reply_markup=keyboard
        )
        
        logger.info(f"✅ Отправлена простая ссылка пользователю {user_id}: {ad['id']}")
        
        # Обновляем статистику
        stats_manager.increment_sent()
        stats_manager.add_sent_ad(
            ad_id=ad['id'],
            message_id=sent_message.message_id,
            user_id=user_id,
            link=ad['link'],
            title=ad['title'],
            olx_user_id=ad.get('olx_user_id', '')
        )
        
        # Если у объявления есть ID пользователя OLX, отмечаем что отправили
        if ad.get('olx_user_id'):
            olx_user_manager.mark_ad_sent_for_user(ad['olx_user_id'], ad['id'])
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка при простой отправке: {e}")
        
        # Даже если не удалось отправить, но ID пользователя есть - отмечаем
        if ad.get('olx_user_id'):
            olx_user_manager.mark_ad_sent_for_user(ad['olx_user_id'], ad['id'])
        
        raise


@dp.callback_query(F.data.startswith("oa:"))  # open_ad
async def handle_open_ad_callback(callback: CallbackQuery):
    """Обработчик для кнопки "Открыть объявление" - меняет только кнопку"""
    try:
        data_parts = callback.data.split(":")
        if len(data_parts) < 3:
            await callback.answer("❌ Ошибка данных", show_alert=True)
            return
        
        ad_id = data_parts[1]
        message_id = int(data_parts[2])
        user_id = callback.from_user.id
        
        # Отмечаем объявление как просмотренное
        viewed_manager.mark_as_viewed(ad_id, message_id, user_id)
        stats_manager.increment_viewed()
        
        # Обновляем клавиатуру - меняем на "✅ Прочитано"
        keyboard = create_ad_keyboard(ad_id, message_id, user_id)
        
        try:
            await bot.edit_message_reply_markup(
                chat_id=user_id,
                message_id=message_id,
                reply_markup=keyboard
            )
        except Exception as e:
            logger.error(f"Ошибка при обновлении клавиатуры: {e}")
        
        # ТОЛЬКО уведомление, без отправки сообщения со ссылкой
        await callback.answer("✅ Объявление отмечено как прочитанное!", show_alert=False)
        
    except Exception as e:
        logger.error(f"Ошибка обработки open_ad callback: {e}")
        await callback.answer("❌ Произошла ошибка", show_alert=True)

@dp.callback_query(F.data.startswith("mr:"))  # mark_read
async def handle_mark_read_callback(callback: CallbackQuery):
    """Обработчик для кнопки "Отметить как прочитанное" в простом режиме"""
    try:
        data_parts = callback.data.split(":")
        if len(data_parts) < 2:
            await callback.answer("❌ Ошибка данных", show_alert=True)
            return
        
        ad_id = data_parts[1]
        user_id = callback.from_user.id
        
        # Отмечаем как просмотренное
        viewed_manager.mark_as_viewed(ad_id, callback.message.message_id, user_id)
        stats_manager.increment_viewed()
        
        await callback.answer("✅ Объявление отмечено как прочитанное!")
        
    except Exception as e:
        logger.error(f"Ошибка обработки mark_read callback: {e}")
        await callback.answer("❌ Произошла ошибка", show_alert=True)

@dp.callback_query(F.data.startswith("vi:"))  # viewed_info
async def handle_viewed_info_callback(callback: CallbackQuery):
    """Информация о просмотренном объявлении"""
    try:
        data_parts = callback.data.split(":")
        if len(data_parts) < 3:
            await callback.answer("❌ Ошибка данных", show_alert=True)
            return
        
        ad_id = data_parts[1]
        
        info_text = (
            f"📊 Информация:\n\n"
            f"🆔 ID объявления: {ad_id}\n"
            f"✅ Статус: Прочитано\n"
            f"👤 Вы отметили это объявление как прочитанное\n"
        )
        
        await callback.answer(info_text, show_alert=True)
        
    except Exception as e:
        logger.error(f"Ошибка обработки viewed_info callback: {e}")
        await callback.answer("❌ Произошла ошибка", show_alert=True)

@dp.callback_query(F.data == "si")  # stats_info
async def handle_stats_callback(callback: CallbackQuery):
    """Обработка нажатия кнопки статистики"""
    try:
        daily_stats = stats_manager.get_daily_stats()
        remaining = stats_manager.get_remaining_daily()
        total_sent = stats_manager.stats['total_sent']
        total_resent = stats_manager.stats['total_resent']
        total_viewed = stats_manager.stats['total_viewed']
        
        stats_text = (
            f"📊 Статистика за {daily_stats['date']}:\n\n"
            f"✅ Отправлено сегодня: {daily_stats['sent']}/{DAILY_LIMIT}\n"
            f"👁️ Прочитано сегодня: {daily_stats['viewed']}\n"
            f"🔄 Повторно отправлено: {daily_stats['resent']}\n"
            f"❌ Неудачных отправок: {daily_stats['failed']}\n"
            f"📈 Осталось отправить: {remaining}\n\n"
            f"📋 Всего отправлено: {total_sent}\n"
            f"👁️ Всего прочитано: {total_viewed}\n"
            f"🔄 Всего повторных: {total_resent}"
        )
        
        await callback.answer(stats_text, show_alert=True)
        
    except Exception as e:
        logger.error(f"Ошибка показа статистики: {e}")
        await callback.answer("❌ Ошибка получения статистики", show_alert=True)


# ========== ОСНОВНОЙ ЦИКЛ ПАРСИНГА ==========
async def parse_and_send_olx_ads():
    """Парсинг OLX и отправка объявлений в личные сообщения"""
    logger.info("🚀 Запуск парсера OLX (Деснянский район Киева)...")
    logger.info(f"📍 Район: Деснянский (ID: 5)")
    logger.info(f"🔄 Интервал проверки: {PARSE_INTERVAL} секунд")
    logger.info(f"⏱️ Задержка между сообщениями: {MESSAGE_DELAY} секунд")
    logger.info(f"📊 Дневной лимит: {DAILY_LIMIT} сообщений")
    logger.info(f"👤 Отправка пользователю: {YOUR_USER_ID}")
    
    await asyncio.sleep(3)
    
    while True:
        try:
            async with OLXAPI() as parser:
                logger.info("🔍 Поиск новых объявлений...")
                new_ads = await parser.get_new_ads()
                
                if new_ads:
                    logger.info(f"🎯 Найдено {len(new_ads)} новых объявлений")
                    
                    remaining = stats_manager.get_remaining_daily()
                    if remaining <= 0:
                        logger.warning(f"⚠️ Достигнут дневной лимит. Пропускаем отправку.")
                        await asyncio.sleep(PARSE_INTERVAL)
                        continue
                    
                    # УБРАНО ОГРАНИЧЕНИЕ 10 ЗА РАЗ
                    max_to_send = min(len(new_ads), remaining)  # Отправляем все, но не больше лимита
                    if len(new_ads) > max_to_send:
                        logger.info(f"⚠️ Ограничиваем до {max_to_send} объявлений из-за дневного лимита")
                        new_ads = new_ads[:max_to_send]
                    
                    sent_count = 0
                    failed_count = 0
                    
                    for i, ad in enumerate(new_ads, 1):        
                        logger.info(f"📤 Отправка {i}/{len(new_ads)} пользователю {YOUR_USER_ID}: {ad['id']}")
                        
                        if await send_ad_to_user(ad, YOUR_USER_ID):
                            sent_count += 1
                        else:
                            failed_count += 1
                        
                        if i < len(new_ads):
                            await asyncio.sleep(MESSAGE_DELAY)
                    
                    logger.info(f"📊 Итог: отправлено {sent_count}, не отправлено {failed_count}")
                    
                    if failed_count > 0:
                        await asyncio.sleep(30)
                
                else:
                    logger.info("📭 Новых объявлений не найдено")
                
                logger.info(f"⏳ Ожидание {PARSE_INTERVAL} секунд до следующей проверки...")
                await asyncio.sleep(PARSE_INTERVAL)
                
        except Exception as e:
            logger.error(f"⚠️ Ошибка в основном цикле парсинга: {e}")
            await asyncio.sleep(30)

async def main():
    print(f"🤖 Бот для парсинга OLX запускается...")
    print(f"📍 Район: Деснянский (ID: 5)")
    print(f"🔄 Интервал проверки: {PARSE_INTERVAL} секунд")
    print(f"⏱️ Задержка между сообщениями: {MESSAGE_DELAY} секунд")
    print(f"📊 Дневной лимит: {DAILY_LIMIT} сообщений")
    print(f"👤 Отправка пользователю: {YOUR_USER_ID}")
    print(f"🚦 Статус рассылки: {'Активна' if is_sending_active else 'Приостановлена'}")
    print("\n⚠️ Используются улучшенные методы обхода блокировок")
    print("⏳ Ожидание запуска...")
    
    # Запускаем фоновую задачу парсинга
    asyncio.create_task(parse_and_send_olx_ads())
    
    # Запускаем бота
    try:
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")

if __name__ == "__main__":
    print("⚠️ Перед запуском убедитесь, что другие экземпляры бота закрыты!")
    print("Запуск через 3 секунды...")
    asyncio.run(main())