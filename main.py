#!/usr/bin/env python3
"""
CLI entrypoint for KworkParser without GUI.
"""
import asyncio
import os
import argparse
import logging
from parser_core import KworkParser
from database import Database

def parse_args():
    parser = argparse.ArgumentParser(description="Kwork parser CLI без GUI")
    parser.add_argument("--pages", type=int, default=10, help="Количество страниц для проверки (1-20)")
    parser.add_argument("--save-expensive", action="store_true", help="Сохранять дорогие проекты в Excel")
    parser.add_argument("--db-path", default="kwork_seen.db", help="Путь к файлу SQLite БД")
    parser.add_argument("--tg-token", default=None, help="Токен Telegram бота (ENV TG_TOKEN или здесь)")
    parser.add_argument("--chat-ids", nargs="+", type=int, default=None, help="Список chat_id для уведомлений")
    return parser.parse_args()

async def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    # Инициализация БД и парсера
    db = Database(db_path=args.db_path)
    parser = KworkParser(db_path=args.db_path, tg_token=args.tg_token)

    # Настройки парсера
    parser.set_pages_to_check(args.pages)
    if args.chat_ids:
        parser.set_active_chat_ids(args.chat_ids)
    if args.save_expensive:
        parser.set_save_expensive(True)

    try:
        await parser.start()
    except KeyboardInterrupt:
        print("Парсер остановлен пользователем")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logging.error(f"Ошибка в работе парсера: {e}")
        exit(1) 