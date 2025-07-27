from website import create_app
from website.models import db
from sqlalchemy import text

app = create_app()

with app.app_context():
    try:
        # Изменяем тип столбца на BIGINT
        db.session.execute(text('ALTER TABLE "user" ALTER COLUMN telegram_id TYPE BIGINT;'))
        db.session.commit()
        print("Тип столбца telegram_id успешно изменен на BIGINT")
    except Exception as e:
        print(f"Ошибка при изменении типа столбца: {e}")
        db.session.rollback() 