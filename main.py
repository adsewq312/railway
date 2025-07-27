import os
from dotenv import load_dotenv
from flask import Flask, redirect, url_for, render_template
from flask_login import LoginManager, current_user
from website.models import db, User
from website.views.auth import auth
from website.views.admin import admin
from bot.bot import create_bot, start_bot
from website.socket import socketio
import asyncio
import threading
import eventlet
import signal
import sys
from threading import Thread
from contextvars import ContextVar
import contextvars

# Загрузка переменных окружения
load_dotenv()

# Проверка наличия необходимых переменных окружения
required_vars = ['BOT_TOKEN', 'DATABASE_URL', 'ADMIN_USER_ID']
missing_vars = [var for var in required_vars if not os.getenv(var)]
if missing_vars:
    raise ValueError(f"Отсутствуют необходимые переменные окружения: {', '.join(missing_vars)}")

# Создание Flask приложения
app = Flask(__name__, 
           template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'website', 'templates'),
           static_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'website', 'static'))

app.config['SECRET_KEY'] = 'dev'  # Используем фиксированный ключ для разработки
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///quiz.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Инициализация расширений
db.init_app(app)
socketio.init_app(app, async_mode='threading')

# Настройка Flask-Login
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Регистрация Blueprint'ов
app.register_blueprint(auth)
app.register_blueprint(admin)

# Обработчик корневого URL
@app.route('/')
def index():
    return render_template('index.html')

# Создание таблиц базы данных
with app.app_context():
    db.create_all()

# Глобальные переменные для управления ботом
bot = None
dp = None
bot_task = None
bot_thread = None

def run_bot_forever():
    """Запуск бота в отдельном потоке"""
    # Создаем новый event loop для этого потока
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Активируем контекст приложения
    app.app_context().push()
    
    try:
        loop.run_until_complete(start_bot(bot, dp))
    except Exception as e:
        print(f"Ошибка в работе бота: {e}")
    finally:
        loop.close()

def cleanup():
    """Очистка ресурсов при завершении"""
    global bot_task, bot_thread
    if bot_thread:
        bot_thread.join(timeout=1)
    sys.exit(0)

def signal_handler(signum, frame):
    """Обработчик сигналов для корректного завершения"""
    print("Получен сигнал завершения, останавливаем приложение...")
    cleanup()

if __name__ == '__main__':
    # Регистрируем обработчики сигналов
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # Создаем бота
        with app.app_context():
            bot, dp = create_bot(app)
        
        # Запускаем бота в отдельном потоке
        bot_thread = Thread(target=run_bot_forever)
        bot_thread.daemon = True
        bot_thread.start()
        
        # Запускаем Flask приложение
        socketio.run(app, debug=True, use_reloader=False)
        
    except KeyboardInterrupt:
        cleanup()
    except Exception as e:
        print(f"Ошибка при запуске приложения: {e}")
        cleanup() 