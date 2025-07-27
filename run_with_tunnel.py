import os
from pyngrok import ngrok
from dotenv import load_dotenv
from website import create_app
from website.socket import socketio
from bot.bot import create_bot, start_bot
import asyncio
import threading
import signal
import sys

# Загрузка переменных окружения
load_dotenv()

# Проверка наличия необходимых переменных окружения
required_vars = ['BOT_TOKEN', 'DATABASE_URL', 'ADMIN_USER_ID']
missing_vars = [var for var in required_vars if not os.getenv(var)]
if missing_vars:
    raise ValueError(f"Отсутствуют необходимые переменные окружения: {', '.join(missing_vars)}")

# Создание Flask приложения
app = create_app()

def run_bot_forever():
    """Запуск бота в отдельном потоке"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    with app.app_context():
        bot, dp = create_bot(app)
        loop.run_until_complete(start_bot(bot, dp))

def cleanup(tunnel):
    """Очистка ресурсов при завершении"""
    print("\nЗавершение работы...")
    ngrok.disconnect(tunnel.public_url)
    sys.exit(0)

def signal_handler(signum, frame, tunnel):
    """Обработчик сигналов для корректного завершения"""
    cleanup(tunnel)

if __name__ == '__main__':
    try:
        # Запускаем ngrok туннель
        port = 5000
        tunnel = ngrok.connect(port)
        public_url = tunnel.public_url
        print(f"\n🌐 Публичный URL: {public_url}")
        print("Скопируйте этот URL, чтобы открыть приложение на другом устройстве")
        print("Для остановки нажмите Ctrl+C\n")

        # Регистрируем обработчики сигналов
        signal.signal(signal.SIGINT, lambda s, f: signal_handler(s, f, tunnel))
        signal.signal(signal.SIGTERM, lambda s, f: signal_handler(s, f, tunnel))
        
        # Запускаем бота в отдельном потоке
        bot_thread = threading.Thread(target=run_bot_forever)
        bot_thread.daemon = True
        bot_thread.start()
        
        # Запускаем Flask приложение
        socketio.run(app, port=port, debug=False)
        
    except Exception as e:
        print(f"Ошибка при запуске: {e}")
        if 'tunnel' in locals():
            cleanup(tunnel) 