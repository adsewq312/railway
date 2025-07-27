from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
import random
import string

db = SQLAlchemy()

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    telegram_id = db.Column(db.BigInteger, unique=True, nullable=False)  # Изменено с Integer на BigInteger
    role = db.Column(db.String(20), default='player')  # admin, moderator, player
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Game(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    quiz_id = db.Column(db.Integer, db.ForeignKey('quiz.id'), nullable=False)
    moderator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    join_code = db.Column(db.String(6), unique=True, nullable=False)
    room_code = db.Column(db.String(6), unique=True)  # Код для присоединения к игровой комнате
    status = db.Column(db.String(20), default='setup')  # setup, ready, active, paused, finished
    current_question_id = db.Column(db.Integer, db.ForeignKey('question.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    started_at = db.Column(db.DateTime)
    finished_at = db.Column(db.DateTime)

    # Константы для статусов
    STATUS_SETUP = 'setup'      # Настройка игры (создание команд)
    STATUS_READY = 'ready'      # Готова к началу (ожидание игроков)
    STATUS_ACTIVE = 'active'    # Игра идет
    STATUS_PAUSED = 'paused'    # Игра на паузе
    STATUS_FINISHED = 'finished' # Игра завершена

    quiz = db.relationship('Quiz', backref=db.backref('games', lazy=True))
    moderator = db.relationship('User', backref=db.backref('moderated_games', lazy=True))
    teams = db.relationship('Team', secondary='game_teams', backref=db.backref('games', lazy=True))
    current_question = db.relationship('Question')

    def generate_room_code(self):
        """Генерирует уникальный код для игровой комнаты"""
        while True:
            code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            if not Game.query.filter_by(room_code=code).first():
                self.room_code = code
                return code

class Quiz(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)  # Добавляем поле для описания
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    rounds = db.relationship('Round', backref='quiz', lazy=True, order_by='Round.order')
    author = db.relationship('User', backref=db.backref('created_quizzes', lazy=True))

class Round(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    quiz_id = db.Column(db.Integer, db.ForeignKey('quiz.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    order = db.Column(db.Integer, nullable=False)

    questions = db.relationship('Question', backref='round', lazy=True, order_by='Question.order')

class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    round_id = db.Column(db.Integer, db.ForeignKey('round.id'), nullable=False)
    text = db.Column(db.Text, nullable=False)
    type = db.Column(db.String(20), default='open')  # open, multiple_choice
    options = db.Column(db.JSON)  # Для вопросов с вариантами ответов
    correct_answer = db.Column(db.Text, nullable=False)
    correct_option = db.Column(db.Integer)  # Индекс правильного ответа для multiple_choice
    points = db.Column(db.Float, default=1.0)  # Количество баллов за вопрос
    time_limit = db.Column(db.Integer, default=30)  # Время на ответ в секундах
    order = db.Column(db.Integer, nullable=False)

class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    captain_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)  # Разрешаем NULL
    join_code = db.Column(db.String(6), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    captain = db.relationship('User', backref=db.backref('captain_of_teams', lazy=True))
    members = db.relationship('TeamMember', backref='team', lazy=True)

class TeamMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    joined_at = db.Column(db.DateTime, nullable=True)  # Убираем default, делаем nullable

    user = db.relationship('User', backref=db.backref('team_memberships', lazy=True))

class Answer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    game_id = db.Column(db.Integer, db.ForeignKey('game.id'), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey('question.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    answer_text = db.Column(db.Text)
    score = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    game = db.relationship('Game', backref=db.backref('answers', lazy=True))
    team = db.relationship('Team', backref=db.backref('answers', lazy=True))
    question = db.relationship('Question', backref=db.backref('answers', lazy=True))
    user = db.relationship('User', backref=db.backref('answers', lazy=True))

# Таблица для связи многие-ко-многим между играми и командами
game_teams = db.Table('game_teams',
    db.Column('game_id', db.Integer, db.ForeignKey('game.id'), primary_key=True),
    db.Column('team_id', db.Integer, db.ForeignKey('team.id'), primary_key=True)
)

class TelegramCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(6), unique=True, nullable=False)
    telegram_id = db.Column(db.Integer, nullable=False)
    is_used = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    used_at = db.Column(db.DateTime) 