import os
from flask import Flask, redirect, url_for
from flask_login import LoginManager
from .models import db, User
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool
import time
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def wait_for_db(engine, max_attempts=5, wait_seconds=5):
    """Ожидание доступности базы данных"""
    for attempt in range(max_attempts):
        try:
            # Проверяем соединение
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                connection.commit()
            return True
        except Exception as e:
            if attempt < max_attempts - 1:
                logger.warning(f"Попытка подключения к БД {attempt + 1} не удалась: {e}")
                time.sleep(wait_seconds)
            else:
                logger.error(f"Не удалось подключиться к БД после {max_attempts} попыток")
                raise

def create_db(app):
    """Создание базы данных и таблиц, если они не существуют"""
    with app.app_context():
        try:
            # Проверяем, существует ли таблица пользователей
            inspector = db.inspect(db.engine)
            tables_exist = inspector.get_table_names()
            
            if not tables_exist:
                # Создаем все таблицы
                db.create_all()
                
                # Создаем первого админа
                admin_telegram_id = int(os.getenv('ADMIN_USER_ID'))
                admin = User.query.filter_by(telegram_id=admin_telegram_id).first()
                
                if not admin:
                    admin = User(
                        id=1,  # Внутренний ID = 1 для первого админа
                        telegram_id=admin_telegram_id,
                        username="admin",
                        role="admin"
                    )
                    db.session.add(admin)
                    try:
                        db.session.commit()
                        logger.info("База данных успешно создана")
                    except Exception as e:
                        db.session.rollback()
                        logger.error(f"Ошибка при создании админа: {e}")
                        raise
        except Exception as e:
            logger.error(f"Ошибка при создании базы данных: {e}")
            raise

def create_app():
    app = Flask(__name__)
    
    # Конфигурация приложения
    app.config['SECRET_KEY'] = os.urandom(24)
    
    # Настройка подключения к PostgreSQL с пулом соединений
    database_url = os.getenv('DATABASE_URL')
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_size': 5,  # максимальное количество постоянных соединений
        'max_overflow': 10,  # максимальное количество временных соединений
        'pool_timeout': 30,  # время ожидания доступного соединения
        'pool_recycle': 1800,  # пересоздание соединений каждые 30 минут
        'pool_pre_ping': True  # проверка соединения перед использованием
    }
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    # Создание директории для загрузок
    uploads_dir = os.path.join(app.root_path, 'uploads')
    if not os.path.exists(uploads_dir):
        os.makedirs(uploads_dir)
    app.config['UPLOAD_FOLDER'] = uploads_dir
    
    # Инициализация базы данных
    db.init_app(app)
    
    # Проверка подключения к базе данных
    engine = create_engine(
        database_url,
        poolclass=QueuePool,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
        pool_recycle=1800,
        pool_pre_ping=True
    )
    
    # Ожидание доступности базы данных
    wait_for_db(engine)
    
    # Создание базы данных при первом запуске
    try:
        create_db(app)
    except Exception as e:
        logger.error(f"Ошибка при инициализации базы данных: {e}")
        raise
    
    # Настройка Flask-Login
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    
    @login_manager.user_loader
    def load_user(user_id):
        try:
            return User.query.get(int(user_id))
        except Exception as e:
            logger.error(f"Ошибка при загрузке пользователя: {e}")
            return None

    # Обработчик корневого URL
    @app.route('/')
    def index():
        return redirect(url_for('admin.index'))
    
    # Регистрация blueprints
    from .views.auth import auth as auth_blueprint
    app.register_blueprint(auth_blueprint, url_prefix='/auth')
    
    from .views.admin import admin as admin_blueprint
    app.register_blueprint(admin_blueprint, url_prefix='/admin')

    return app 