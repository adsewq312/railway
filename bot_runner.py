 import os
import asyncio
import logging
from website import create_app
from bot.bot import create_bot, start_bot
from dotenv import load_dotenv

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()

def run_bot():
    try:
        # Создаем приложение Flask
        app = create_app()
        
        # Активируем контекст приложения
        with app.app_context():
            # Создаем и запускаем бота
            bot, dp = create_bot(app)
            
            # Запускаем бота
            asyncio.run(start_bot(bot, dp))
            
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
        raise

if __name__ == "__main__":
    run_bot()