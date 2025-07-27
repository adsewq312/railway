import random
import string
import logging
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required
from ..models import db, User, TelegramCode

auth = Blueprint('auth', __name__)
logger = logging.getLogger(__name__)

def generate_code(length=6):
    """Генерация случайного кода для входа"""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

@auth.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        code = request.form.get('code')
        logger.info(f"Попытка входа с кодом: {code}")
        
        if not code:
            flash('Введите код', 'danger')
            return redirect(url_for('auth.login'))
        
        # Ищем код в базе
        telegram_code = TelegramCode.query.filter_by(code=code, is_used=False).first()
        
        if not telegram_code:
            logger.warning(f"Код не найден или уже использован: {code}")
            flash('Неверный код или код уже использован', 'danger')
            return redirect(url_for('auth.login'))
        
        logger.info(f"Найден код для telegram_id: {telegram_code.telegram_id}")
        
        # Получаем пользователя
        user = User.query.filter_by(telegram_id=telegram_code.telegram_id).first()
        
        if not user:
            logger.warning(f"Пользователь не найден для telegram_id: {telegram_code.telegram_id}")
            flash('У вас нет прав для входа', 'danger')
            return redirect(url_for('auth.login'))
            
        logger.info(f"Найден пользователь: {user.username}, роль: {user.role}")
        
        if user.role not in ['admin', 'moderator']:
            logger.warning(f"Недостаточно прав у пользователя {user.username} (роль: {user.role})")
            flash('У вас нет прав для входа', 'danger')
            return redirect(url_for('auth.login'))
        
        # Отмечаем код как использованный
        telegram_code.is_used = True
        db.session.commit()
        
        # Выполняем вход
        login_user(user)
        logger.info(f"Успешный вход пользователя {user.username}")
        return redirect(url_for('admin.index'))
    
    return render_template('auth/login.html')

@auth.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login')) 