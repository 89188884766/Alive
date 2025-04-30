import asyncio
import json
import logging
import os
import random
import re
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple, Set

import aiohttp
import pandas as pd
from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton

from database import Database
from dotenv import load_dotenv
import openai

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%H:%M:%S")

# Regular expressions
PRICE_RE = re.compile(r"(\d[\d\s]*)(?=\s*₽)")
OFFERS_RE = re.compile(r"Предложени[йя]:?\s*(\d+)", re.I)

# Load router API key from .env
load_dotenv()
ROUTER_API_KEY = os.getenv("ROUTER_API_KEY")
if ROUTER_API_KEY is None:
    raise ValueError("ROUTER_API_KEY not found. Please check your .env file.")

# Initialize OpenAI router client
router_client = openai.OpenAI(
    api_key=ROUTER_API_KEY,
    base_url="https://router.requesty.ai/v1",
    default_headers={"Authorization": f"Bearer {ROUTER_API_KEY}"}
)

class Signal:
    def __init__(self):
        self._callbacks = []
    def connect(self, callback):
        self._callbacks.append(callback)
    def emit(self, *args, **kwargs):
        for cb in list(self._callbacks):
            cb(*args, **kwargs)

class KworkParser:
    """Kwork project parser with UI integration."""
    
    def __init__(self, db_path: str = "kwork_seen.db", tg_token: Optional[str] = None):
        """Initialize the parser.
        
        Args:
            db_path: Path to the SQLite database file
            tg_token: Telegram bot token
        """
        # Initialize custom signals
        self.progress_updated = Signal()
        self.status_updated = Signal()
        self.project_found = Signal()
        self.parsing_started = Signal()
        self.parsing_stopped = Signal()
        
        # Initialize Telegram Bot
        self.bot = Bot(tg_token or os.getenv("TG_TOKEN", "7679170440:AAHtYy2dV0ZCiUfmO19cFeSyNaTPf3fEv34"))

        # Configuration
        self.db_path = db_path
        self.tg_token = tg_token or os.getenv("TG_TOKEN", "7679170440:AAHtYy2dV0ZCiUfmO19cFeSyNaTPf3fEv34")
        self.kwork_url = "https://kwork.ru/projects?a=1&kworks-filters%5B%5D=0"
        self.pages_to_check = 10  # Default to 10 pages
        self.check_interval = (15, 30)  # sec. between checks (min, max)
        
        # Hardcoded chat IDs
        self.chat_id = int(os.getenv("CHAT_ID", "1501487914"))
        self.friend_id = 1185188202  # ID моего кента
        self.other_id = 2093566409  # дополнительный получатель
        # Active chat IDs list
        self.active_chat_ids = [self.chat_id, self.friend_id, self.other_id]
        
        # State
        self.running = False
        self.progress = 0
        self.total_projects = 0
        self.current_page = 0
        self.save_expensive = False  # Flag to save expensive projects
        self.expensive_projects = []  # List to collect expensive projects
        
        # Database
        self.db = Database(db_path)
        # Router client and cache for callback handling
        self.router_client = router_client
        self.projects_cache: Dict[str, Any] = {}
        self.listener_task = None
        
        # Cookies for API requests
        self.cookies = [
            {
                "name": "yuidss",
                "value": "8075353501744915811",
                "domain": ".yandex.ru",
                "path": "/",    
                "expires": 1776673667.213,
                "httpOnly": False,
                "secure": True
            },
            {
                "name": "userId",
                "value": "19856765",
                "domain": "kwork.ru",
                "path": "/",
                "expires": 1776673753.051,
                "httpOnly": False,
                "secure": False
            },
            {
                "name": "uad",
                "value": "1985676568022f5681ae8374205467",
                "domain": "kwork.ru",
                "path": "/",
                "expires": 1776532454.156,
                "httpOnly": True,
                "secure": True
            },
            {
                "name": "slrememberme",
                "value": "19856765_%242y%2410%247HKrxCJgZaoM8y65jUz2E.kV0Wvk742M6gS0U9gVHdTX95d%2F8xV0G",
                "domain": "kwork.ru",
                "path": "/",
                "expires": 1776532600.184,
                "httpOnly": True,
                "secure": True
            },
            {
                "name": "_kmwl",
                "value": "1",
                "domain": "kwork.ru",
                "path": "/",
                "expires": 1776673654.263,
                "httpOnly": True,
                "secure": True
            },
            {
                "name": "_kmid",
                "value": "7c9420899107a03e606e77b3807f06f4",
                "domain": "kwork.ru",
                "path": "/",
                "expires": 1776452067.243,
                "httpOnly": True,
                "secure": True
            }
        ]
        
        # Headers for API requests
        self.api_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "multipart/form-data; boundary=----geckoformboundary6256e7a5bfbf2e2dcf82a01b32fc4abb",
            "Origin": "https://kwork.ru",
            "Connection": "keep-alive",
        }
    
    async def start(self):
        """Start the parser."""
        if self.running:
            return
        
        self.running = True
        self.parsing_started.emit()
        # Start listening for Telegram callback queries
        self.listener_task = asyncio.create_task(self._listen_callbacks())
        await self._run_parser()
    
    def stop(self):
        """Stop the parser."""
        self.running = False
        # Stop callback listener
        if self.listener_task:
            self.listener_task.cancel()
        self.status_updated.emit("Остановка парсера...")
        self.parsing_stopped.emit()
    
    def set_pages_to_check(self, pages: int):
        """Set the number of pages to check.
        
        Args:
            pages: Number of pages to check
        """
        self.pages_to_check = max(1, min(pages, 20))  # Limit to 1-20 pages
    
    def set_active_chat_ids(self, ids: List[int]):
        """Set active chat IDs."""
        self.active_chat_ids = ids
    
    async def _run_parser(self):
        """Main parser loop."""
        bot = Bot(self.tg_token)
        
        self.status_updated.emit("Парсер запущен")
        
        while self.running:
            # Reset collected expensive projects if saving enabled
            if self.save_expensive:
                self.expensive_projects = []
            
            self.status_updated.emit(f"Начинаю проверку {self.pages_to_check} страниц проектов через API")
            logging.info(f"Начинаю проверку {self.pages_to_check} страниц проектов через API")
            
            # Reset progress
            self.progress = 0
            self.total_projects = 0
            self.current_page = 0
            
            # Asynchronously gather projects from all pages
            tasks = []
            for i in range(1, self.pages_to_check + 1):
                tasks.append(self._process_page(i))
            
            # Wait for all tasks to complete
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Collect all projects
            all_projects = []
            for result in results:
                if isinstance(result, list):
                    all_projects.extend(result)
                else:
                    logging.error(f"Ошибка при сборе проектов через API: {result}")
            
            self.total_projects = len(all_projects)
            self.status_updated.emit(f"Всего найдено проектов через API: {self.total_projects}")
            logging.info(f"Всего найдено проектов через API: {self.total_projects}")
            
            # If no projects found, wait and try again
            if not all_projects:
                logging.warning("Не найдено ни одного проекта через API")
                sleep_time = random.randint(*self.check_interval)
                self.status_updated.emit(f"Жду {sleep_time} сек до следующей попытки")
                logging.info(f"Жду {sleep_time} сек до следующей попытки")
                await asyncio.sleep(sleep_time)
                continue
            
            # Process projects
            for proj in all_projects:
                if not self.running:
                    break
                
                # Skip if already seen
                if self.db.is_project_seen(proj["id"]):
                    continue
                
                # Check offers count
                try:
                    offers_count = int(proj['offers']) if proj['offers'].isdigit() else float('inf')
                    if offers_count > 2:
                        logging.info(f"Пропускаю заказ {proj['id']} с {offers_count} предложениями (больше 2)")
                        continue
                except (ValueError, AttributeError):
                    logging.debug(f"Не удалось определить количество предложений для {proj['id']}: {proj['offers']}")
                
                # Sanitize data
                safe_title = self._sanitize_html(proj['title'])
                safe_desc = self._sanitize_html(proj['description'])
                safe_price = self._sanitize_html(proj['price'])
                safe_offers = self._sanitize_html(proj['offers'])
                
                # Check if project is expensive (≥25000₽) and save feature is enabled
                try:
                    price_value = float(proj['price'].replace(" ", ""))
                    if self.save_expensive and price_value >= 25000:
                        self.expensive_projects.append(proj)
                except (ValueError, AttributeError):
                    pass  # Skip if price can't be converted to float
                
                # Format message
                msg = (
                    f"<b>{safe_title}</b>\n"
                    f"{safe_desc}\n\n"
                    f"💰 <b>{safe_price} ₽</b> | 📨 Предложений: {safe_offers}\n"
                    f"{proj['link']}"
                )
                
                try:
                    # Cache project and prepare inline button
                    self.projects_cache[proj["id"]] = proj
                    keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("Сформировать отклик", callback_data=proj["id"])]
                    ])
                    # Send to all active chat IDs
                    for chat_id in self.active_chat_ids:
                        try:
                            await self.bot.send_message(
                                chat_id,
                                msg,
                                parse_mode="HTML",
                                disable_web_page_preview=True,
                                reply_markup=keyboard
                            )
                        except Exception as e:
                            logging.error(f"Ошибка отправки сообщения в чат {chat_id}: {e}")
                    
                    # Mark as seen
                    self.db.mark_project_seen(proj["id"])
                    
                    # Emit signal for UI
                    self.project_found.emit(proj)
                    
                except Exception as e:
                    logging.error(f"Ошибка отправки сообщения для {proj['id']}: {e}")
                    # Save problematic message for debugging
                    with open(f"error_message_{proj['id']}.txt", "w", encoding="utf-8") as f:
                        f.write(msg)
            
            # Wait before next check
            if self.running:
                # Save collected expensive projects if enabled
                if self.save_expensive and self.expensive_projects:
                    self._save_expensive_projects()
                
                sleep_time = random.randint(*self.check_interval)
                self.status_updated.emit(f"Жду {sleep_time} сек до следующей проверки")
                logging.info(f"Жду {sleep_time} сек до следующей проверки")
                await asyncio.sleep(sleep_time)
    
    async def _process_page(self, page_number: int) -> List[Dict[str, Any]]:
        """Process a single page of projects.
        
        Args:
            page_number: Page number to process
            
        Returns:
            List of projects
        """
        self.current_page = page_number
        self.status_updated.emit(f"Обработка страницы {page_number} из {self.pages_to_check}")
        
        # Format page URL
        if "?" in self.kwork_url:
            page_url = f"{self.kwork_url}&page={page_number}"
        else:
            page_url = f"{self.kwork_url}?page={page_number}"
        
        logging.info(f"Запрос данных через API для страницы: {page_url}")
        
        # Fetch data from API
        json_data = await self._fetch_projects_from_api(page_number)
        
        # If failed to get data, return empty list
        if not json_data:
            logging.error(f"Не удалось получить данные через API для страницы {page_number}")
            return []
        
        # Save JSON for debugging (only for first page)
        if page_number == 1:
            try:
                with open("debug_api.json", "w", encoding="utf-8") as f:
                    json.dump(json_data, f, indent=2, ensure_ascii=False)
                logging.info("Сохранен debug_api.json для анализа структуры")
            except Exception as e:
                logging.error(f"Не удалось сохранить отладочный JSON: {e}")
        
        # Parse projects from JSON
        projects = self._parse_projects_from_json(json_data, page_url)
        # Проверка совпадения количества
        try:
            raw_count = len(json_data.get("data", {}).get("pagination", {}).get("data", []))
            parsed_count = len(projects)
            if parsed_count != raw_count:
                logging.warning(f"Страница {page_number}: спаршено {parsed_count}/{raw_count} проектов")
        except Exception:
            logging.error(f"Не удалось проверить количество проектов на странице {page_number}")
        
        # Update progress
        self.progress += 1
        self.progress_updated.emit(self.progress, self.pages_to_check)
        
        return projects
    
    async def _fetch_projects_from_api(self, page_number: int) -> Optional[Dict[str, Any]]:
        """Fetch projects from Kwork API for the specified page.
        
        Args:
            page_number: Page number to fetch
            
        Returns:
            JSON data or None if failed
        """
        url = "https://kwork.ru/projects"
        
        # Format referer URL and headers
        referer_url = f"https://kwork.ru/projects?a=1&page={page_number}"
        headers = self.api_headers.copy()
        headers["Referer"] = referer_url
        
        # Add cookies to headers
        cookies_str = "; ".join([f"{cookie['name']}={cookie['value']}" for cookie in self.cookies if cookie['domain'] == 'kwork.ru'])
        if cookies_str:
            headers["Cookie"] = cookies_str
        
        # Format data for POST request
        data = (
            "------geckoformboundary6256e7a5bfbf2e2dcf82a01b32fc4abb\r\n"
            "Content-Disposition: form-data; name=\"a\"\r\n"
            "\r\n"
            "1\r\n"
            "------geckoformboundary6256e7a5bfbf2e2dcf82a01b32fc4abb\r\n"
            f"Content-Disposition: form-data; name=\"page\"\r\n"
            "\r\n"
            f"{page_number}\r\n"
            "------geckoformboundary6256e7a5bfbf2e2dcf82a01b32fc4abb--\r\n"
        )
        
        try:
            # Use aiohttp for asynchronous request
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, data=data) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logging.error(f"Ошибка API: {response.status}")
                        return None
        except Exception as e:
            logging.error(f"Ошибка при запросе к API: {e}")
            return None
    
    def _parse_projects_from_json(self, json_data: Dict[str, Any], page_url: str) -> List[Dict[str, Any]]:
        """Parse projects from JSON data.
        
        Args:
            json_data: JSON data from API
            page_url: URL of the page
            
        Returns:
            List of projects
        """
        projects = []
        
        # Check JSON structure
        if not json_data or "data" not in json_data or "pagination" not in json_data["data"] or "data" not in json_data["data"]["pagination"]:
            logging.error("Неверная структура JSON-ответа")
            return []
        
        # Get list of projects
        wants = json_data["data"]["pagination"]["data"]
        logging.info(f"Найдено проектов в JSON: {len(wants)}")
        
        for want in wants:
            try:
                # Extract ID
                pid = str(want.get("id", ""))
                if not pid:
                    logging.error("Не найден ID проекта в JSON")
                    continue
                
                # Extract basic data
                title = want.get("name", "").strip()
                link = f"https://kwork.ru/projects/{pid}"
                desc = want.get("description", "").strip() if want.get("description") else "—"
                
                # Extract price
                price_raw = want.get("priceLimit", "")
                price = str(price_raw) if price_raw else "—"
                
                # Extract offers count
                offers_count = want.get("kwork_count", 0)
                offers = str(offers_count)
                
                projects.append({
                    "id": pid,
                    "title": title,
                    "link": link,
                    "description": desc,
                    "price": price,
                    "offers": offers,
                    "page": page_url,
                })
            except Exception as e:
                logging.error(f"Ошибка при обработке проекта из JSON: {e}")
        
        return projects
    
    async def _send_notification(self, msg: str, project: Dict[str, Any]):
        """Send notification to all active chat IDs.
        
        Args:
            msg: Message to send
            project: Project data
        """
        # Cache project and prepare inline button
        self.projects_cache[project["id"]] = project
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Сформировать отклик", callback_data=project["id"])]
        ])
        # Send to all active chat IDs
        for chat_id in self.active_chat_ids:
            try:
                await self.bot.send_message(
                    chat_id,
                    msg,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    reply_markup=keyboard
                )
            except Exception as e:
                logging.error(f"Ошибка отправки сообщения в чат {chat_id}: {e}")

    def _sanitize_html(self, text: str) -> str:
        """Clean text from unsafe HTML tags for safe sending to Telegram.
        
        Args:
            text: Text to sanitize
            
        Returns:
            Sanitized text
        """
        if not text:
            return "—"
        
        # Replace angle brackets that may be part of HTML tags
        text = text.replace("<", "&lt;").replace(">", "&gt;")
        
        # Remove control characters and null bytes
        return ''.join(c for c in text if ord(c) >= 32 or c == '\n')

    def _save_expensive_projects(self):
        """Save projects with price ≥25000₽ to a nicely formatted Excel table."""
        try:
            if not self.expensive_projects:
                return
            
            # Create DataFrame from collected projects
            df = pd.DataFrame([
                {
                    'ID': p['id'], 
                    'Название': p['title'], 
                    'Описание': p['description'],
                    'Цена (₽)': p['price'], 
                    'Предложений': p['offers'],
                    'Ссылка': p['link']
                } for p in self.expensive_projects
            ])
            
            # Format the DataFrame for better display
            df = df.sort_values('Цена (₽)', ascending=False)
            
            # Create a timestamp for the filename
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
            filename = f"expensive_projects_{timestamp}.xlsx"
            
            # Save to Excel with styling
            with pd.ExcelWriter(filename, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Дорогие проекты')
                
                # Get the worksheet and set column widths
                worksheet = writer.sheets['Дорогие проекты']
                worksheet.column_dimensions['A'].width = 10  # ID
                worksheet.column_dimensions['B'].width = 40  # Название
                worksheet.column_dimensions['C'].width = 60  # Описание
                worksheet.column_dimensions['D'].width = 15  # Цена
                worksheet.column_dimensions['E'].width = 15  # Предложений
                worksheet.column_dimensions['F'].width = 30  # Ссылка
            
            logging.info(f"Сохранено {len(self.expensive_projects)} дорогих проектов в {filename}")
            self.status_updated.emit(f"Сохранено {len(self.expensive_projects)} дорогих проектов в {filename}")
            
        except Exception as e:
            logging.error(f"Ошибка при сохранении дорогих проектов: {e}")

    def set_save_expensive(self, enabled: bool):
        """Enable or disable saving expensive projects to table.
        
        Args:
            enabled: True to enable, False to disable
        """
        self.save_expensive = enabled
        status = "включено" if enabled else "отключено"
        logging.info(f"Сохранение дорогих проектов {status}")
        self.status_updated.emit(f"Сохранение дорогих проектов {status}")

    async def _listen_callbacks(self):
        """Listener for Telegram callback queries to generate AI responses."""
        offset = None
        try:
            while self.running:
                try:
                    updates = await self.bot.get_updates(offset=offset, timeout=30)
                    for upd in updates:
                        offset = upd.update_id + 1
                        if upd.callback_query:
                            await self._handle_callback(upd.callback_query)
                except Exception as e:
                    logging.error(f"Ошибка обработки обновлений Telegram: {e}")
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logging.info("Callback listener stopped")

    async def _handle_callback(self, callback_query):
        """Handle callback query by sending description to AI and returning response."""
        project_id = callback_query.data
        await self.bot.answer_callback_query(callback_query.id)
        project = self.projects_cache.get(project_id)
        if not project:
            return
        prompt = project['description']
        try:
            response = await asyncio.to_thread(
                lambda: self.router_client.chat.completions.create(
                    model="openai/chatgpt-4o-latest",
                    messages=[{"role":"user", "content": prompt}]
                )
            )
            ai_text = response.choices[0].message.content
        except Exception as e:
            ai_text = f"Ошибка AI: {e}"
        await self.bot.send_message(callback_query.message.chat.id, ai_text)