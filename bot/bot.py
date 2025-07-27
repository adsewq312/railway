import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters.command import Command
from aiogram.filters import CommandObject
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, Message, CallbackQuery, FSInputFile
from website.models import db, User, Game, Team, TeamMember, Question, Answer, Round, TelegramCode, Quiz
from website.views.auth import generate_code
from website.views.quiz_parser import parse_quiz_content
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import text
import contextlib
import asyncio
from functools import wraps
from sqlalchemy.orm import scoped_session, sessionmaker
from datetime import datetime
from aiogram.types import Message, CallbackQuery
import tempfile
from docx import Document

# Импортируем константы статусов игры
GAME_STATUS_SETUP = 'setup'
GAME_STATUS_READY = 'ready'
GAME_STATUS_ACTIVE = 'active'
GAME_STATUS_PAUSED = 'paused'
GAME_STATUS_FINISHED = 'finished'

# Константы для callback data
START_GAME = 'start_game'
NEXT_QUESTION = 'next_question'
ASK_QUESTION = 'ask_question'
PAUSE_GAME = 'pause_game'
RESUME_GAME = 'resume_game'
END_GAME = 'end_game'
APPROVE_ANSWER = 'approve_answer'
REJECT_ANSWER = 'reject_answer'

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Глобальные переменные для бота и диспетчера
bot = None
dp = None
flask_app = None

def with_app_context(func):
    """Декоратор для работы с контекстом приложения Flask"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Ошибка в обработчике {func.__name__}: {e}")
            # Если это сообщение, отправляем ответ об ошибке
            if len(args) > 0 and isinstance(args[0], types.Message):
                await args[0].answer("Произошла ошибка при обработке команды. Попробуйте позже.")
            # Если это callback_query, отвечаем на него
            elif len(args) > 0 and isinstance(args[0], types.CallbackQuery):
                await args[0].answer("Произошла ошибка. Попробуйте позже.", show_alert=True)
    return wrapper

def create_bot(app):
    """Создание и настройка бота с контекстом приложения"""
    try:
        # Проверка наличия токена
        bot_token = os.getenv('BOT_TOKEN')
        if not bot_token:
            raise ValueError("BOT_TOKEN не найден в переменных окружения")

        # Сохраняем ссылку на приложение
        global flask_app
        flask_app = app

        # Инициализация бота и диспетчера
        global bot, dp
        bot = Bot(token=bot_token)
        dp = Dispatcher()

        # Регистрируем все обработчики
        register_handlers(dp)
        
        logger.info("Бот успешно инициализирован")
        return bot, dp

    except Exception as e:
        logger.error(f"Ошибка при инициализации бота: {e}")
        raise

def register_handlers(dp: Dispatcher):
    """Регистрация всех обработчиков команд и callback-запросов"""
    # Регистрируем обработчики команд
    dp.message.register(cmd_start, Command("start"))
    dp.message.register(cmd_login, Command("login"))
    dp.message.register(cmd_join, Command("join"))
    dp.message.register(cmd_upload_quiz, Command("upload_quiz"))
    
    # Регистрируем обработчик файлов
    dp.message.register(process_quiz_file, lambda msg: msg.document is not None)
    
    # Регистрируем базовые обработчики
    dp.callback_query.register(process_join_game, lambda c: c.data == "join_game")
    dp.callback_query.register(process_auto_join, lambda c: c.data.startswith("auto_join:"))
    dp.message.register(process_game_code, lambda m: hasattr(m, 'text') and len(m.text) == 6 and m.text.isupper())
    dp.callback_query.register(process_join_team, lambda c: c.data.startswith("join_team:"))
    
    # Регистрируем обработчики управления игрой
    dp.callback_query.register(process_ready_game, lambda c: c.data.startswith("ready_game:"))
    dp.callback_query.register(process_start_game, lambda c: c.data.startswith(f"{START_GAME}:"))
    dp.callback_query.register(process_next_question, lambda c: c.data.startswith(f"{NEXT_QUESTION}:"))
    dp.callback_query.register(process_ask_question, lambda c: c.data.startswith(f"{ASK_QUESTION}:"))
    dp.callback_query.register(process_pause_game, lambda c: c.data.startswith(f"{PAUSE_GAME}:"))
    dp.callback_query.register(process_resume_game, lambda c: c.data.startswith(f"{RESUME_GAME}:"))
    dp.callback_query.register(process_finish_game, lambda c: c.data.startswith(f"{END_GAME}:"))
    
    # Регистрируем обработчики ответов
    dp.callback_query.register(process_answer_choice, lambda c: c.data.startswith("answer:"))
    dp.callback_query.register(process_answer_review, lambda c: c.data and (
        c.data.startswith(f"{APPROVE_ANSWER}:") or 
        c.data.startswith(f"{REJECT_ANSWER}:")
    ))
    
    # Регистрируем обработчик текстовых ответов (должен быть последним)
    dp.message.register(process_answer, lambda msg: msg.text and not msg.text.startswith('/'))

@with_app_context
async def cmd_start(message: types.Message):
    """Обработчик команды /start"""
    logger.info(f"Получена команда /start от пользователя {message.from_user.id}")
    
    # Проверяем, является ли пользователь администратором
    admin_id = os.getenv('ADMIN_USER_ID')
    logger.info(f"ADMIN_USER_ID из .env: {admin_id}")
    logger.info(f"ID пользователя: {message.from_user.id}, тип: {type(message.from_user.id)}")
    logger.info(f"Сравнение: {str(message.from_user.id)} == {admin_id}")
    
    is_admin = admin_id and str(message.from_user.id) == admin_id
    logger.info(f"is_admin: {is_admin}")
    
    try:
        # Начинаем новую транзакцию
        db.session.begin_nested()
        
        user = User.query.filter_by(telegram_id=message.from_user.id).first()
        if not user:
            # Создаем пользователя с соответствующей ролью
            user = User(
                telegram_id=message.from_user.id,
                username=message.from_user.username or str(message.from_user.id),
                role="admin" if is_admin else "player"  # Устанавливаем роль admin для админа
            )
            db.session.add(user)
            logger.info(f"Создан новый пользователь: {user.username} с ролью {user.role}")
        elif is_admin and user.role != 'admin':
            # Если пользователь уже существует и это админ, но роль не admin
            logger.info(f"Обновляем роль пользователя {user.username} с {user.role} на admin")
            user.role = 'admin'
        else:
            logger.info(f"Пользователь {user.username} уже существует с ролью {user.role}")
            if is_admin:
                logger.info("Пользователь является админом, но роль уже установлена правильно")
            else:
                logger.info("Пользователь не является админом")
        
        # Фиксируем изменения
        db.session.commit()
        
        # Проверяем, является ли пользователь модератором или админом
        if user.role in ['admin', 'moderator']:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🎮 Управление играми",
                            callback_data="manage_games"
                        )
                    ]
                ]
            )
        else:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🎮 Присоединиться к игре",
                            callback_data="join_game"
                        )
                    ]
                ]
            )
        
        role_text = {
            "admin": "Администратор",
            "moderator": "Модератор",
            "player": "Игрок"
        }.get(user.role, "Неизвестная роль")
        
        await message.answer(
            f"Привет! Ваша роль: {role_text}\n"
            f"Ваш внутренний ID: {user.id}\n\n"
            "Выберите действие:",
            reply_markup=keyboard
        )
        
    except Exception as e:
        # В случае ошибки откатываем транзакцию
        db.session.rollback()
        logger.error(f"Ошибка в обработчике cmd_start: {e}")
        # Отправляем сообщение об ошибке пользователю
        await message.answer(
            "Произошла ошибка при обработке команды. "
            "Пожалуйста, попробуйте позже или обратитесь к администратору."
        )

@with_app_context
async def cmd_login(message: types.Message):
    """Обработчик команды /login"""
    logger.info(f"Получена команда /login от пользователя {message.from_user.id}")
    
    try:
        # Проверяем, является ли пользователь администратором по ADMIN_USER_ID
        admin_id = os.getenv('ADMIN_USER_ID')
        if not admin_id or str(message.from_user.id) != admin_id:
            logger.warning(f"Пользователь {message.from_user.id} не является администратором")
            await message.answer("У вас нет прав для входа в админ-панель.")
            return
        
        # Начинаем транзакцию
        db.session.begin_nested()
        
        # Получаем или создаем пользователя
        user = User.query.filter_by(telegram_id=message.from_user.id).first()
        if not user:
            # Создаем администратора
            user = User(
                telegram_id=message.from_user.id,
                username=message.from_user.username or f"admin_{message.from_user.id}",
                role="admin"
            )
            db.session.add(user)
            logger.info(f"Создан новый администратор: {user.username}")
        elif user.role != 'admin':
            # Обновляем роль до админа, если это необходимо
            user.role = 'admin'
            logger.info(f"Обновлена роль пользователя {user.username} до admin")
        
        # Генерируем код
        code = generate_code()
        logger.info(f"Сгенерирован код {code} для пользователя {message.from_user.id}")
        
        # Проверяем существующие коды
        existing_codes = TelegramCode.query.filter_by(telegram_id=message.from_user.id, is_used=False).all()
        if existing_codes:
            logger.info(f"Найдены существующие неиспользованные коды: {[code.code for code in existing_codes]}")
            # Отмечаем старые коды как использованные
            for old_code in existing_codes:
                old_code.is_used = True
            logger.info("Старые коды помечены как использованные")
        
        # Сохраняем новый код в базу
        telegram_code = TelegramCode(
            code=code,
            telegram_id=message.from_user.id
        )
        db.session.add(telegram_code)
        
        # Фиксируем все изменения
        db.session.commit()
        logger.info(f"Код {code} успешно сохранен в базе")
        
        # Отправляем код пользователю
        await message.answer(
            f"Ваш код для входа: {code}\n\n"
            "Код действителен только для одного входа.\n"
            "Введите его на странице входа в админ-панель."
        )
        
    except Exception as e:
        # В случае ошибки откатываем транзакцию
        db.session.rollback()
        logger.error(f"Ошибка в обработчике cmd_login: {e}")
        await message.answer(
            "Произошла ошибка при генерации кода. "
            "Пожалуйста, попробуйте позже или обратитесь к администратору."
        )

@with_app_context
async def process_join_game(callback_query: types.CallbackQuery):
    """Обработчик запроса на присоединение к игре"""
    logger.info(f"Получен запрос на присоединение к игре от пользователя {callback_query.from_user.id}")
    
    await callback_query.message.edit_text(
        "Введите код для присоединения к игре (6 символов).\n"
        "Код можно получить у администратора или модератора игры."
    )
    await callback_query.answer()

@with_app_context
async def process_auto_join(callback_query: types.CallbackQuery):
    """Обработчик автоматического присоединения к игре"""
    join_code = callback_query.data.split(':')[1]
    
    # Создаем фейковое сообщение с командой
    message = types.Message(
        message_id=0,
        date=datetime.now(),
        chat=callback_query.message.chat,
        from_user=callback_query.from_user,
        text=f"/join {join_code}",
        bot=callback_query.bot,
        conf={'skip_validation': True}
    )
    
    # Создаем объект команды
    command = CommandObject(
        prefix="/",
        command="join",
        args=join_code
    )
    
    # Вызываем обработчик команды join
    await cmd_join(message, command)
    
    await callback_query.answer()

@with_app_context
async def process_game_code(message: Message):
    """Обработчик ввода кода квиза"""
    logger.info(f"Получен код квиза от пользователя {message.from_user.id}: {message.text}")
    
    # Получаем пользователя по telegram_id
    user = User.query.filter_by(telegram_id=message.from_user.id).first()
    if not user:
        await message.answer("Ошибка: пользователь не найден.")
        return
    
    # Ищем игру по коду
    game = Game.query.filter_by(join_code=message.text).first()
    
    if not game:
        await message.answer("Квиз с таким кодом не найден.")
        return

    # Проверяем статус игры
    if game.status != Game.STATUS_READY:
        await message.answer(
            "Этот квиз еще не готов к приему игроков.\n"
            "Дождитесь, пока администратор подготовит квиз."
        )
        return
    
    # Ищем команду игрока
    team_member = TeamMember.query.filter_by(user_id=user.id)\
        .join(TeamMember.team)\
        .join(Team.games)\
        .filter(Game.id == game.id)\
        .first()
    
    if not team_member:
        await message.answer(
            "Вы не были добавлены ни в одну команду этого квиза.\n"
            "Обратитесь к администратору или модератору квиза."
        )
        return
    
    # Если пользователь уже присоединился к игре
    if team_member.joined_at:
        await message.answer(
            f"Вы уже присоединились к квизу в команде {team_member.team.name}.\n"
            f"Ожидайте начала квиза!"
        )
        return
        
    # Отмечаем время присоединения
    team_member.joined_at = datetime.utcnow()
    db.session.commit()
    
    # Отправляем уведомление через WebSocket
    from website.socket import socketio
    socketio.emit('player_joined', {
        'user_id': user.id,
        'username': user.username,
        'team_id': team_member.team_id
    }, room=f'game_{game.id}')
    
    # Получаем информацию о раундах
    total_rounds = Round.query.filter_by(quiz_id=game.quiz_id).count()
    first_round = Round.query.filter_by(quiz_id=game.quiz_id, order=1).first()
    questions_in_first_round = Question.query.filter_by(round_id=first_round.id).count() if first_round else 0
    
    await message.answer(
        f"Вы успешно присоединились к квизу в команде {team_member.team.name}!\n\n"
        f"📚 Всего раундов: {total_rounds}\n"
        f"❓ Вопросов в первом раунде: {questions_in_first_round}\n\n"
        "Ожидайте начала квиза."
    )

@with_app_context
async def cmd_join(message: types.Message, command: CommandObject):
    """Обработчик команды /join для присоединения к игровой комнате"""
    try:
        logger.info(f"Получен запрос на присоединение к игре от пользователя {message.from_user.id}")
        
        if not command.args:
            await message.answer("Укажите код игровой комнаты после команды /join")
            return

        # Разбираем аргументы команды
        args = command.args.strip().split()
        join_code = args[0].upper()
        
        logger.info(f"Код для присоединения: {join_code}")
        
        # Ищем игру по коду
        game = Game.query.filter_by(join_code=join_code).first()
        if not game:
            logger.warning(f"Игра с кодом {join_code} не найдена")
            await message.answer("Игра с указанным кодом не найдена")
            return
            
        logger.info(f"Найдена игра {game.id}, статус: '{game.status}'")
        if game.status != Game.STATUS_READY:
            logger.warning(f"Игра с кодом {join_code} имеет статус {game.status}")
            await message.answer(
                "Эта игра еще не готова к приему игроков.\n"
                "Дождитесь, пока администратор подготовит игру."
            )
            return

        # Получаем пользователя
        user = User.query.filter_by(telegram_id=message.from_user.id).first()
        if not user:
            logger.warning(f"Пользователь с telegram_id {message.from_user.id} не найден")
            await message.answer(
                "Для участия в игре необходимо сначала авторизоваться.\n"
                "Используйте команду /login для входа в систему."
            )
            return

        # Проверяем, является ли пользователь админом/модером
        is_game_admin = user.role in ['admin', 'moderator'] and game.moderator_id == user.id
        
        if is_game_admin:
            # Создаем клавиатуру для управления игрой
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="▶️ Начать квиз",
                            callback_data=f"{START_GAME}:{game.id}"
                        )
                    ]
                ]
            )
            await message.answer(
                "Вы присоединились как модератор квиза.\n"
                "Используйте кнопки ниже для управления квизом:",
                reply_markup=keyboard
            )
            return

        # Ищем команду игрока
        team_member = TeamMember.query.filter_by(user_id=user.id)\
            .join(TeamMember.team)\
            .join(Team.games)\
            .filter(Game.id == game.id)\
            .first()
        
        if not team_member:
            logger.warning(f"Пользователь {user.id} не найден ни в одной команде игры {game.id}")
            await message.answer(
                "Вы не были добавлены ни в одну команду этой игры.\n"
                "Обратитесь к администратору или модератору игры."
            )
            return
            
        # Если пользователь уже присоединился к игре
        if team_member.joined_at:
            await message.answer(
                f"Вы уже присоединились к игре в команде {team_member.team.name}.\n"
                f"Ожидайте начала игры!"
            )
            return
            
        # Отмечаем время присоединения
        team_member.joined_at = datetime.utcnow()
        db.session.commit()
        
        # Отправляем уведомление через WebSocket
        from website.socket import socketio
        socketio.emit('player_joined', {
            'user_id': user.id,
            'username': user.username,
            'team_id': team_member.team_id
        }, room=f'game_{game.id}')
        
        await message.answer(
            f"Вы успешно присоединились к команде {team_member.team.name}!\n"
            "Ожидайте начала игры."
        )
        
    except Exception as e:
        logger.error(f"Ошибка при обработке команды /join: {e}")
        await message.answer("Произошла ошибка при обработке команды")

@with_app_context
async def process_join_team(callback_query: types.CallbackQuery):
    """Обработчик присоединения к команде"""
    team_id = int(callback_query.data.split(':')[1])
    
    # Получаем пользователя по telegram_id
    user = User.query.filter_by(telegram_id=callback_query.from_user.id).first()
    if not user:
        await callback_query.answer("Ошибка: пользователь не найден", show_alert=True)
        return
    
    # Получаем команду
    team = Team.query.get(team_id)
    if not team:
        await callback_query.answer("Команда не найдена", show_alert=True)
        return
    
    # Проверяем, не состоит ли пользователь уже в команде
    existing_member = TeamMember.query.filter_by(
        team_id=team.id,
        user_id=user.id
    ).first()
    
    if existing_member:
        await callback_query.answer("Вы уже состоите в этой команде", show_alert=True)
        return
    
    # Добавляем пользователя в команду
    team_member = TeamMember(team_id=team.id, user_id=user.id)
    db.session.add(team_member)
    db.session.commit()
    
    await callback_query.message.edit_text(
        f"Вы успешно присоединились к команде {team.name}!"
    )
    await callback_query.answer()

@with_app_context
async def process_ready_game(callback_query: types.CallbackQuery):
    """Обработчик подготовки квиза к началу"""
    game_id = int(callback_query.data.split(':')[1])
    
    game = Game.query.get(game_id)
    if not game:
        await callback_query.answer("Квиз не найден", show_alert=True)
        return
    
    if game.moderator_id != User.query.filter_by(telegram_id=callback_query.from_user.id).first().id:
        await callback_query.answer("У вас нет прав для управления этим квизом", show_alert=True)
        return
    
    # Проверяем, есть ли команды и игроки
    if not game.teams:
        await callback_query.answer("Нельзя начать квиз без команд", show_alert=True)
        return
    
    for team in game.teams:
        if not team.members:
            await callback_query.answer(f"В команде {team.name} нет игроков", show_alert=True)
            return
    
    # Меняем статус игры на READY
    game.status = Game.STATUS_READY
    db.session.commit()
    
    # Создаем клавиатуру для управления игрой
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="▶️ Начать квиз",
                    callback_data=f"{START_GAME}:{game_id}"
                )
            ]
        ]
    )
    
    # Отправляем сообщение с информацией о командах
    teams_info = "\n\n".join([
        f"Команда: {team.name}\n"
        f"Капитан: {team.captain.username}\n"
        f"Игроки: {', '.join(member.user.username for member in team.members)}"
        for team in game.teams
    ])
    
    await callback_query.message.edit_text(
        f"Квиз готов к началу!\n\n"
        f"Название: {game.quiz.title}\n\n"
        f"Команды:\n{teams_info}",
        reply_markup=keyboard
    )
    
    await callback_query.answer()

@with_app_context
async def process_start_game(callback_query: types.CallbackQuery):
    """Обработчик нажатия кнопки начала игры"""
    game_id = int(callback_query.data.split(':')[1])
    user = User.query.filter_by(telegram_id=callback_query.from_user.id).first()
    
    if not user or user.role not in ['admin', 'moderator']:
        await callback_query.answer("У вас нет прав для управления квизом", show_alert=True)
        return
        
    game = Game.query.get(game_id)
    if not game or game.moderator_id != user.id:
        await callback_query.answer("Квиз не найден или вы не являетесь его модератором", show_alert=True)
        return
        
    if game.status != Game.STATUS_READY:
        await callback_query.answer("Квиз не готов к началу", show_alert=True)
        return
        
    try:
        # Меняем статус игры
        game.status = Game.STATUS_ACTIVE
        game.started_at = datetime.utcnow()
        
        # Берем первый вопрос
        first_round = Round.query.filter_by(quiz_id=game.quiz_id, order=1).first()
        if first_round:
            first_question = Question.query.filter_by(round_id=first_round.id, order=1).first()
            if first_question:
                game.current_question_id = first_question.id
            
        db.session.commit()
        
        # Отправляем уведомление через WebSocket
        from website.socket import socketio, broadcast_game_state
        broadcast_game_state(game.id)
        
        # Получаем информацию о раундах
        total_rounds = Round.query.filter_by(quiz_id=game.quiz_id).count()
        questions_in_first_round = Question.query.filter_by(round_id=first_round.id).count()
        
        # Отправляем сообщение всем участникам
        for team in game.teams:
            for member in team.members:
                if member.joined_at:  # Отправляем только тем, кто присоединился
                    await bot.send_message(
                        member.user.telegram_id,
                        f"🎮 Квиз «{game.quiz.title}» начинается!\n\n"
                        f"🎯 Вы играете за команду «{team.name}»\n"
                        f"📚 Всего раундов: {total_rounds}\n"
                        f"❓ Вопросов в первом раунде: {questions_in_first_round}\n\n"
                        f"👥 Ваши товарищи по команде:\n"
                        + "\n".join(f"• {m.user.username}" for m in team.members if m.id != member.id)
                        + "\n\n"
                        f"Ждите первый вопрос от модератора..."
                    )
        
        # Создаем панель управления для модератора
        await update_moderator_panel(game, callback_query.message)
        
    except Exception as e:
        logger.error(f"Ошибка при начале квиза: {e}")
        await callback_query.answer("Произошла ошибка при начале квиза", show_alert=True)

@with_app_context
async def process_next_round(callback_query: types.CallbackQuery):
    """Обработчик перехода к следующему раунду"""
    game_id = int(callback_query.data.split(':')[1])
    
    game = Game.query.get(game_id)
    if not game or game.status != Game.STATUS_ACTIVE:
        await callback_query.answer("Игра не найдена или не активна", show_alert=True)
        return
    
    # Получаем текущий раунд
    current_round = None
    if game.current_question_id:
        current_question = Question.query.get(game.current_question_id)
        current_round = current_question.round
    
    # Получаем следующий раунд
    next_round = None
    if current_round:
        next_round = Round.query.filter_by(
            quiz_id=game.quiz_id,
            order=current_round.order + 1
        ).first()
    else:
        # Если текущего раунда нет, берем первый
        next_round = Round.query.filter_by(
            quiz_id=game.quiz_id,
            order=1
        ).first()
    
    if not next_round:
        await callback_query.answer("Нет следующего раунда", show_alert=True)
        return
    
    # Берем первый вопрос следующего раунда
    next_question = Question.query.filter_by(
        round_id=next_round.id,
        order=1
    ).first()
    
    if not next_question:
        await callback_query.answer("В следующем раунде нет вопросов", show_alert=True)
        return
    
    # Обновляем текущий вопрос
    game.current_question_id = next_question.id
    db.session.commit()
    
    # Оповещаем все команды о начале нового раунда
    for team in game.teams:
        for member in team.members:
            try:
                await bot.send_message(
                    member.user.telegram_id,
                    f"Начался новый раунд: {next_round.title}\n"
                    f"Приготовьтесь к ответам!"
                )
            except Exception as e:
                logger.error(f"Ошибка при отправке уведомления игроку {member.user.username}: {e}")
    
    await callback_query.answer("Начат новый раунд")

@with_app_context
async def process_pause_game(callback_query: types.CallbackQuery):
    """Обработчик постановки игры на паузу"""
    game_id = int(callback_query.data.split(':')[1])
    
    game = Game.query.get(game_id)
    if not game or game.status != Game.STATUS_ACTIVE:
        await callback_query.answer("Игра не найдена или не активна", show_alert=True)
        return
    
    # Меняем статус игры
    game.status = Game.STATUS_PAUSED
    db.session.commit()
    
    # Создаем клавиатуру для управления игрой
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="▶️ Продолжить",
                    callback_data=f"resume_game:{game_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏹️ Завершить",
                    callback_data=f"finish_game:{game_id}"
                )
            ]
        ]
    )
    
    await callback_query.message.edit_text(
        f"Игра приостановлена\n"
        f"Квиз: {game.quiz.title}",
        reply_markup=keyboard
    )
    
    # Оповещаем все команды о паузе
    for team in game.teams:
        for member in team.members:
            try:
                await bot.send_message(
                    member.user.telegram_id,
                    "Игра приостановлена. Ожидайте продолжения."
                )
            except Exception as e:
                logger.error(f"Ошибка при отправке уведомления игроку {member.user.username}: {e}")
    
    await callback_query.answer()

@with_app_context
async def process_resume_game(callback_query: types.CallbackQuery):
    """Обработчик возобновления игры"""
    game_id = int(callback_query.data.split(':')[1])
    
    game = Game.query.get(game_id)
    if not game or game.status != Game.STATUS_PAUSED:
        await callback_query.answer("Игра не найдена или не на паузе", show_alert=True)
        return
    
    # Меняем статус игры
    game.status = Game.STATUS_ACTIVE
    db.session.commit()
    
    # Создаем клавиатуру для управления игрой
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏭️ Следующий раунд",
                    callback_data=f"next_round:{game_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏸️ Пауза",
                    callback_data=f"pause_game:{game_id}"
                )
            ]
        ]
    )
    
    await callback_query.message.edit_text(
        f"Игра продолжается\n"
        f"Квиз: {game.quiz.title}\n"
        "Используйте кнопки для управления игрой.",
        reply_markup=keyboard
    )
    
    # Оповещаем все команды о возобновлении
    for team in game.teams:
        for member in team.members:
            try:
                await bot.send_message(
                    member.user.telegram_id,
                    "Игра продолжается!"
                )
            except Exception as e:
                logger.error(f"Ошибка при отправке уведомления игроку {member.user.username}: {e}")
    
    await callback_query.answer()

@with_app_context
async def process_finish_game(callback_query: types.CallbackQuery):
    """Обработчик завершения игры"""
    game_id = int(callback_query.data.split(':')[1])
    
    game = Game.query.get(game_id)
    if not game or game.status not in [Game.STATUS_ACTIVE, Game.STATUS_PAUSED]:
        await callback_query.answer("Игра не найдена или не может быть завершена", show_alert=True)
        return
    
    # Меняем статус игры
    game.status = Game.STATUS_FINISHED
    db.session.commit()
    
    await callback_query.message.edit_text(
        f"Игра завершена\n"
        f"Квиз: {game.quiz.title}"
    )
    
    # Оповещаем все команды о завершении
    for team in game.teams:
        for member in team.members:
            try:
                await bot.send_message(
                    member.user.telegram_id,
                    "Игра завершена. Спасибо за участие!"
                )
            except Exception as e:
                logger.error(f"Ошибка при отправке уведомления игроку {member.user.username}: {e}")
    
    await callback_query.answer()

async def echo(message: types.Message):
    """Эхо-обработчик для всех остальных сообщений"""
    try:
        logger.info(f"Получено сообщение от пользователя {message.from_user.id}: {message.text}")
        
        # Создаем клавиатуру с основными действиями
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎮 Присоединиться к игре",
                        callback_data="join_game"
                    )
                ]
            ]
        )
        
        await message.answer(
            "Извините, я не понимаю эту команду.\n"
            "Доступные команды:\n"
            "/start - Начать работу с ботом\n"
            "/login - Получить код для входа в админ-панель",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Ошибка при обработке сообщения: {e}")

async def start_bot(bot: Bot, dp: Dispatcher):
    try:
        logger.info("Запуск бота...")
        # Запускаем бота
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
        raise

def format_scoreboard(game: Game) -> str:
    """Форматирует таблицу результатов"""
    scores = []
    for team in game.teams:
        total_score = sum(answer.score or 0 for answer in team.answers)
        scores.append((team.name, total_score))
    
    # Сортируем команды по убыванию очков
    scores.sort(key=lambda x: x[1], reverse=True)
    
    result = "📊 Текущий счет:\n\n"
    for i, (team_name, score) in enumerate(scores, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "▫️"
        result += f"{medal} {team_name}: {score} очков\n"
    
    return result

def get_quiz_progress(game: Game) -> str:
    """Формирует информацию о прогрессе квиза"""
    if not game.current_question:
        return "❌ Нет активного вопроса"
        
    current_round = game.current_question.round
    current_question = game.current_question
    
    # Получаем общую информацию о раундах
    total_rounds = Round.query.filter_by(quiz_id=game.quiz_id).count()
    questions_in_current_round = Question.query.filter_by(round_id=current_round.id).count()
    
    # Получаем следующий вопрос в текущем раунде
    next_question_same_round = Question.query.filter(
        Question.round_id == current_round.id,
        Question.order > current_question.order
    ).order_by(Question.order).first()
    
    # Получаем первый вопрос следующего раунда
    next_round = Round.query.filter(
        Round.quiz_id == game.quiz_id,
        Round.order > current_round.order
    ).order_by(Round.order).first()
    
    next_question_next_round = Question.query.filter(
        Question.round_id == next_round.id
    ).order_by(Question.order).first() if next_round else None
    
    progress = (
        f"📍 Текущее положение:\n"
        f"Раунд {current_round.order} из {total_rounds}: {current_round.title}\n"
        f"Вопрос {current_question.order} из {questions_in_current_round}\n\n"
    )
    
    if next_question_same_round:
        progress += (
            f"⏭️ Следующий вопрос:\n"
            f"Останемся в текущем раунде\n"
            f"Вопрос {next_question_same_round.order} из {questions_in_current_round}\n"
            f"Тип: {'С вариантами ответов' if next_question_same_round.type == 'multiple_choice' else 'Свободный ответ'}\n\n"
        )
    elif next_round and next_question_next_round:
        questions_in_next_round = Question.query.filter_by(round_id=next_round.id).count()
        progress += (
            f"📚 Следующий раунд:\n"
            f"Переходим к раунду {next_round.order} из {total_rounds}: {next_round.title}\n"
            f"Начнем с вопроса 1 из {questions_in_next_round}\n"
            f"Тип: {'С вариантами ответов' if next_question_next_round.type == 'multiple_choice' else 'Свободный ответ'}\n\n"
        )
    else:
        progress += "🏁 Это последний вопрос квиза!\n\n"
    
    return progress

async def update_moderator_panel(game: Game, message: Message = None):
    """Обновляет панель управления модератора"""
    progress = get_quiz_progress(game)
    scoreboard = format_scoreboard(game)
    
    # Формируем текст сообщения
    text = (
        f"🎮 Управление квизом «{game.quiz.title}»\n\n"
        f"{progress}\n"
        f"{scoreboard}"
    )
    
    # Создаем клавиатуру
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📢 Задать вопрос",
                    callback_data=f"{ASK_QUESTION}:{game.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏭️ Следующий вопрос",
                    callback_data=f"{NEXT_QUESTION}:{game.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏸️ Пауза",
                    callback_data=f"{PAUSE_GAME}:{game.id}"
                ),
                InlineKeyboardButton(
                    text="⏹️ Завершить",
                    callback_data=f"{END_GAME}:{game.id}"
                )
            ]
        ]
    )
    
    if message:
        await message.edit_text(text, reply_markup=keyboard)
    else:
        await bot.send_message(game.moderator.telegram_id, text, reply_markup=keyboard)

async def send_question(game: Game):
    """Отправка текущего вопроса всем участникам"""
    if not game.current_question:
        return
        
    question = game.current_question
    round_info = f"Раунд {question.round.order}" if question.round else ""
    
    # Формируем текст вопроса
    question_text = (
        f"❓ {round_info}\n"
        f"Вопрос {question.order}:\n\n"
        f"{question.text}"
    )
    
    # Получаем текущий счет
    scoreboard = format_scoreboard(game)
    
    # Если есть варианты ответов, создаем клавиатуру
    if question.type == 'multiple_choice' and question.options:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=option,
                        callback_data=f"answer:{game.id}:{question.id}:{i}"
                    )
                ] for i, option in enumerate(question.options)
            ]
        )
        
        # Отправляем вопрос с вариантами ответов
        for team in game.teams:
            for member in team.members:
                if member.joined_at:
                    await bot.send_message(
                        member.user.telegram_id,
                        f"{question_text}\n\n{scoreboard}",
                        reply_markup=keyboard
                    )
    else:
        # Отправляем вопрос без вариантов
        for team in game.teams:
            for member in team.members:
                if member.joined_at:
                    await bot.send_message(
                        member.user.telegram_id,
                        f"{question_text}\n\n"
                        f"✍️ Напишите ваш ответ в чат.\n\n"
                        f"{scoreboard}"
                    )

@with_app_context
async def process_ask_question(callback_query: CallbackQuery):
    """Обработчик кнопки 'Задать вопрос'"""
    game_id = int(callback_query.data.split(':')[1])
    
    user = User.query.filter_by(telegram_id=callback_query.from_user.id).first()
    if not user or user.role not in ['admin', 'moderator']:
        await callback_query.answer("У вас нет прав для управления квизом", show_alert=True)
        return
        
    game = Game.query.get(game_id)
    if not game or game.moderator_id != user.id:
        await callback_query.answer("Квиз не найден или вы не являетесь его модератором", show_alert=True)
        return
        
    if game.status != Game.STATUS_ACTIVE:
        await callback_query.answer("Квиз не активен", show_alert=True)
        return
        
    try:
        # Отправляем текущий вопрос
        await send_question(game)
        await callback_query.answer("Вопрос отправлен участникам")
        
        # Обновляем панель модератора
        await update_moderator_panel(game, callback_query.message)
        
    except Exception as e:
        logger.error(f"Ошибка при отправке вопроса: {e}")
        await callback_query.answer("Произошла ошибка при отправке вопроса", show_alert=True)

@with_app_context
async def process_next_question(callback_query: CallbackQuery):
    """Обработчик перехода к следующему вопросу"""
    game_id = int(callback_query.data.split(':')[1])
    
    user = User.query.filter_by(telegram_id=callback_query.from_user.id).first()
    if not user or user.role not in ['admin', 'moderator']:
        await callback_query.answer("У вас нет прав для управления квизом", show_alert=True)
        return
        
    game = Game.query.get(game_id)
    if not game or game.moderator_id != user.id:
        await callback_query.answer("Квиз не найден или вы не являетесь его модератором", show_alert=True)
        return
        
    if game.status != Game.STATUS_ACTIVE:
        await callback_query.answer("Квиз не активен", show_alert=True)
        return
        
    try:
        current_question = game.current_question
        if not current_question:
            await callback_query.answer("Текущий вопрос не найден", show_alert=True)
            return
            
        current_round = current_question.round
        
        # Ищем следующий вопрос в текущем раунде
        next_question = Question.query.filter(
            Question.round_id == current_round.id,
            Question.order > current_question.order
        ).order_by(Question.order).first()
        
        if not next_question:
            # Ищем следующий раунд
            next_round = Round.query.filter(
                Round.quiz_id == game.quiz_id,
                Round.order > current_round.order
            ).order_by(Round.order).first()
            
            if next_round:
                # Берем первый вопрос следующего раунда
                next_question = Question.query.filter_by(
                    round_id=next_round.id
                ).order_by(Question.order).first()
        
        if not next_question:
            # Это был последний вопрос
            game.status = Game.STATUS_FINISHED
            game.finished_at = datetime.utcnow()
            db.session.commit()
            
            final_scoreboard = format_scoreboard(game)
            
            # Отправляем уведомление о завершении игры
            for team in game.teams:
                for member in team.members:
                    if member.joined_at:
                        await bot.send_message(
                            member.user.telegram_id,
                            f"🎯 Квиз завершен!\n\n"
                            f"Финальный счет:\n{final_scoreboard}\n"
                            f"Спасибо за участие! 🎉"
                        )
            
            await callback_query.message.edit_text(
                f"🏁 Квиз «{game.quiz.title}» завершен!\n\n"
                f"Финальный счет:\n{final_scoreboard}",
                reply_markup=None
            )
            return
            
        # Переходим к следующему вопросу
        game.current_question_id = next_question.id
        db.session.commit()
        
        # Уведомляем о переходе к следующему вопросу/раунду
        if next_question.round_id != current_round.id:
            for team in game.teams:
                for member in team.members:
                    if member.joined_at:
                        await bot.send_message(
                            member.user.telegram_id,
                            f"📚 Начинается новый раунд!\n"
                            f"Раунд {next_question.round.order}: {next_question.round.title}"
                        )
        
        # Обновляем панель модератора
        await update_moderator_panel(game, callback_query.message)
        
        await callback_query.answer(
            "Переход к следующему вопросу выполнен. "
            "Нажмите 'Задать вопрос', чтобы отправить его участникам."
        )
        
    except Exception as e:
        logger.error(f"Ошибка при переходе к следующему вопросу: {e}")
        await callback_query.answer("Произошла ошибка", show_alert=True)

@with_app_context
async def process_answer_choice(callback_query: CallbackQuery):
    """Обработка ответа с выбором варианта"""
    _, game_id, question_id, option_idx = callback_query.data.split(':')
    game_id, question_id, option_idx = map(int, [game_id, question_id, option_idx])
    
    user = User.query.filter_by(telegram_id=callback_query.from_user.id).first()
    if not user:
        await callback_query.answer("Пользователь не найден", show_alert=True)
        return
        
    team_member = TeamMember.query.filter_by(user_id=user.id)\
        .join(TeamMember.team)\
        .join(Team.games)\
        .filter(Game.id == game_id)\
        .first()
        
    if not team_member or not team_member.joined_at:
        await callback_query.answer("Вы не являетесь участником этой игры", show_alert=True)
        return
        
    game = Game.query.get(game_id)
    if not game or game.status != Game.STATUS_ACTIVE:
        await callback_query.answer("Игра не найдена или не активна", show_alert=True)
        return
        
    question = Question.query.get(question_id)
    if not question or question.id != game.current_question_id:
        await callback_query.answer("Этот вопрос уже не активен", show_alert=True)
        return
        
    # Создаем ответ
    answer = Answer(
        game_id=game_id,
        team_id=team_member.team_id,
        question_id=question_id,
        user_id=user.id,
        answer_text=question.options[option_idx]
    )
    db.session.add(answer)
    
    # Если это вопрос с автоматической проверкой
    if question.correct_option is not None:
        answer.score = 1.0 if option_idx == question.correct_option else 0.0
        
    db.session.commit()
    
    # Отправляем уведомление модератору
    await bot.send_message(
        game.moderator.telegram_id,
        f"Получен ответ от команды {team_member.team.name}:\n"
        f"Игрок: {user.username}\n"
        f"Ответ: {question.options[option_idx]}\n"
        f"{'✅ Верно' if answer.score == 1.0 else '❌ Неверно' if answer.score == 0.0 else '⏳ Ожидает проверки'}"
    )
    
    await callback_query.answer("Ваш ответ принят!")

@with_app_context
async def process_answer(message: Message):
    """Обработка текстового ответа на вопрос"""
    user = User.query.filter_by(telegram_id=message.from_user.id).first()
    if not user:
        return
        
    # Ищем активную игру пользователя
    team_member = TeamMember.query.filter_by(user_id=user.id)\
        .join(TeamMember.team)\
        .join(Team.games)\
        .filter(Game.status == Game.STATUS_ACTIVE)\
        .first()
        
    if not team_member or not team_member.joined_at:
        return
        
    game = team_member.team.games[0]  # Берем первую активную игру
    
    if not game.current_question:
        await message.answer("Сейчас нет активного вопроса.")
        return
        
    # Создаем ответ
    answer = Answer(
        game_id=game.id,
        team_id=team_member.team_id,
        question_id=game.current_question_id,
        user_id=user.id,
        answer_text=message.text
    )
    db.session.add(answer)
    db.session.commit()
    
    # Отправляем уведомление модератору с кнопками проверки
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Принять",
                    callback_data=f"{APPROVE_ANSWER}:{answer.id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"{REJECT_ANSWER}:{answer.id}"
                )
            ]
        ]
    )
    
    await bot.send_message(
        game.moderator.telegram_id,
        f"Ответ от команды {team_member.team.name}:\n"
        f"Игрок: {user.username}\n"
        f"Ответ: {message.text}",
        reply_markup=keyboard
    )
    
    await message.answer("Ваш ответ принят и отправлен на проверку модератору.")

@with_app_context
async def process_answer_review(callback_query: CallbackQuery):
    """Обработчик проверки ответа модератором"""
    action, answer_id = callback_query.data.split(':')
    answer_id = int(answer_id)
    
    user = User.query.filter_by(telegram_id=callback_query.from_user.id).first()
    if not user or user.role not in ['admin', 'moderator']:
        await callback_query.answer("У вас нет прав для проверки ответов", show_alert=True)
        return
        
    answer = Answer.query.get(answer_id)
    if not answer:
        await callback_query.answer("Ответ не найден", show_alert=True)
        return
        
    game = Game.query.get(answer.game_id)
    if not game or game.moderator_id != user.id:
        await callback_query.answer("У вас нет прав для проверки этого ответа", show_alert=True)
        return
        
    # Устанавливаем оценку
    answer.score = 1.0 if action == APPROVE_ANSWER else 0.0
    db.session.commit()
    
    # Получаем текущий счет
    scoreboard = format_scoreboard(game)
    
    # Обновляем сообщение с ответом
    await callback_query.message.edit_text(
        callback_query.message.text + f"\n\n{'✅ Принят' if action == APPROVE_ANSWER else '❌ Отклонен'}",
        reply_markup=None
    )
    
    # Отправляем уведомление команде
    team = Team.query.get(answer.team_id)
    for member in team.members:
        if member.joined_at:
            await bot.send_message(
                member.user.telegram_id,
                f"Результат проверки ответа на вопрос {answer.question.order}:\n"
                f"{'✅ Ответ принят!' if action == APPROVE_ANSWER else '❌ Ответ отклонен'}\n\n"
                f"{scoreboard}"
            )
    
    # Обновляем счет на сайте
    from website.socket import broadcast_scoreboard
    broadcast_scoreboard(game.id)
    
    await callback_query.answer("Ответ проверен") 

@with_app_context
async def cmd_upload_quiz(message: Message):
    """Обработчик команды /upload_quiz"""
    user = User.query.filter_by(telegram_id=message.from_user.id).first()
    if not user or user.role not in ['admin', 'moderator']:
        await message.answer("У вас нет прав для загрузки квизов.")
        return

    await message.answer(
        "Отправьте файл квиза в формате .txt или .docx.\n\n"
        "Файл должен соответствовать формату:\n"
        "# Название квиза\n"
        "Описание квиза\n\n"
        "## Раунд 1: Название\n"
        "1. Вопрос\n"
        "Тип: multiple_choice/open_answer\n"
        "Ответ: правильный ответ\n"
        "Варианты: вариант1;вариант2;вариант3 (для multiple_choice)\n"
        "Баллы: количество баллов\n"
        "Время: время в секундах"
    )

@with_app_context
async def process_quiz_file(message: Message):
    """Обработчик загрузки файла квиза"""
    if not message.document:
        return

    user = User.query.filter_by(telegram_id=message.from_user.id).first()
    if not user or user.role not in ['admin', 'moderator']:
        return

    file_name = message.document.file_name.lower()
    if not (file_name.endswith('.txt') or file_name.endswith('.docx')):
        await message.answer("Поддерживаются только файлы .txt и .docx")
        return

    try:
        # Скачиваем файл во временный байтовый буфер
        file = await bot.get_file(message.document.file_id)
        file_bytes = await bot.download_file(file.file_path)
        
        # Читаем содержимое
        if file_name.endswith('.txt'):
            content = file_bytes.decode('utf-8')
        else:  # .docx
            # Сохраняем bytes во временный файл только для docx
            with tempfile.NamedTemporaryFile(suffix='.docx', delete=True) as tmp_file:
                tmp_file.write(file_bytes)
                tmp_file.flush()  # Убеждаемся, что все данные записаны
                doc = Document(tmp_file.name)
                content = '\n'.join(paragraph.text for paragraph in doc.paragraphs)
        
        if not content:
            raise ValueError("Не удалось прочитать содержимое файла")
            
        # Парсим содержимое
        quiz = parse_quiz_content(content, user.id)
        
        # Отправляем подтверждение
        rounds_count = len(quiz.rounds)
        questions_count = sum(len(round.questions) for round in quiz.rounds)
        
        # Формируем статистику по типам вопросов в каждом раунде
        round_stats = []
        for i, round_obj in enumerate(quiz.rounds):
            multiple_choice = sum(1 for q in round_obj.questions if q.type == 'multiple_choice')
            open_answer = sum(1 for q in round_obj.questions if q.type == 'open_answer')
            round_stats.append(
                f"Раунд {i+1}: {len(round_obj.questions)} вопр. "
                f"({multiple_choice} тестовых, {open_answer} открытых)"
            )
        
        await message.answer(
            f"✅ Квиз успешно загружен!\n\n"
            f"📝 Название: {quiz.title}\n"
            f"📚 Количество раундов: {rounds_count}\n"
            f"❓ Общее количество вопросов: {questions_count}\n\n"
            f"Статистика по раундам:\n" + 
            "\n".join(round_stats)
        )
            
    except Exception as e:
        logger.error(f"Ошибка при загрузке квиза: {str(e)}")
        await message.answer(
            f"❌ Ошибка при загрузке квиза: {str(e)}\n"
            "Убедитесь, что файл соответствует формату и кодировке UTF-8"
        ) 