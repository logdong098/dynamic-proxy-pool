import os
import asyncio
import logging
from typing import Optional
from telethon import TelegramClient, events

from config import settings
from storage import storage
from checker import checker
from extractor import extract_proxies_from_text

logger = logging.getLogger("scraper.telegram_userbot")


class TelegramUserBotScraper:
    def __init__(self):
        self.api_id = settings.TG_API_ID
        self.api_hash = settings.TG_API_HASH
        self.session_path = settings.TG_SESSION_PATH
        self.target_chats = settings.tg_target_chat_list
        self.client: Optional[TelegramClient] = None
        self.is_running = False

    def is_configured(self) -> bool:
        return bool(self.api_id and self.api_hash)

    async def start(self):
        """Start the Telethon UserBot to listen to chats."""
        if not self.is_configured():
            logger.info("Telegram UserBot: TG_API_ID or TG_API_HASH not set. UserBot collector disabled.")
            return

        # Ensure directory for session file exists
        os.makedirs(os.path.dirname(os.path.abspath(self.session_path)), exist_ok=True)

        try:
            self.client = TelegramClient(self.session_path, self.api_id, self.api_hash)
            await self.client.connect()

            if not await self.client.is_user_authorized():
                logger.warning(
                    f"Telegram UserBot: Session '{self.session_path}' is not authorized. "
                    "Run 'python login_userbot.py' to authenticate once before running the bot."
                )
                return

            self.is_running = True
            logger.info("Telegram UserBot: Successfully connected and authorized.")

            # Register message listener
            @self.client.on(events.NewMessage)
            async def handle_new_message(event):
                try:
                    chat = await event.get_chat()
                    chat_title = getattr(chat, 'title', None) or getattr(chat, 'username', 'UnknownChat')

                    # Filter target chats if specified
                    if self.target_chats:
                        chat_id_str = str(getattr(chat, 'id', ''))
                        chat_username = str(getattr(chat, 'username', '') or '')
                        matched = any(
                            t in [chat_title, chat_id_str, chat_username, f"@{chat_username}"]
                            for t in self.target_chats
                        )
                        if not matched:
                            return

                    logger.info(f"Telegram UserBot: Received message from '{chat_title}'")
                    new_proxies = []

                    # 1. Process document/file attachments (.txt, .json, .csv, .log)
                    if event.message.file and event.message.file.name:
                        filename = event.message.file.name.lower()
                        if any(filename.endswith(ext) for ext in [".txt", ".json", ".csv", ".log", ".yaml"]):
                            logger.info(f"Telegram UserBot: Downloading file attachment '{filename}' from {chat_title}")
                            data = await event.message.download_media(bytes)
                            if data:
                                try:
                                    text_content = data.decode("utf-8", errors="ignore")
                                    new_proxies = extract_proxies_from_text(
                                        text_content,
                                        source=f"tg_file:{chat_title}:{filename}"
                                    )
                                except Exception as err:
                                    logger.error(f"Error decoding attachment {filename}: {err}")

                    # 2. Process message text
                    if not new_proxies and event.raw_text:
                        new_proxies = extract_proxies_from_text(
                            event.raw_text,
                            source=f"tg_msg:{chat_title}"
                        )

                    # 3. Store and immediately schedule check for newly arrived proxies
                    if new_proxies:
                        logger.info(f"Telegram UserBot: Extracted {len(new_proxies)} proxies from {chat_title}")
                        await storage.upsert_many(new_proxies)
                        # Immediately check up to 30 new proxies asynchronously
                        asyncio.create_task(checker.check_batch(new_proxies[:30]))

                except Exception as e:
                    logger.error(f"Telegram UserBot message handler error: {e}")

            # Keep client running in background
            await self.client.run_until_disconnected()

        except Exception as e:
            logger.error(f"Telegram UserBot failed to run: {e}")

    async def stop(self):
        if self.client and self.is_running:
            await self.client.disconnect()
            self.is_running = False
            logger.info("Telegram UserBot stopped.")


tg_userbot = TelegramUserBotScraper()
