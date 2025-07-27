from website import create_app
from website.models import db, User
from sqlalchemy import text

app = create_app()

with app.app_context():
    try:
        # Удаляем таблицу user
        db.session.execute(text('DROP TABLE IF EXISTS "user" CASCADE;'))
        db.session.commit()
        print("Старая таблица user удалена")
        
        # Создаем таблицу заново
        db.create_all()
        print("Таблица user создана заново с правильным типом данных")
        
    except Exception as e:
        print(f"Ошибка: {e}")
        db.session.rollback() 