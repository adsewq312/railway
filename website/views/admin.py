import os
import random
import string
import asyncio
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from ..models import db, User, Quiz, Game, Team, Round, Question, TeamMember, Answer
from .quiz_parser import parse_quiz_file
from sqlalchemy import text
import tempfile
from docx import Document

# Создаем Blueprint с указанием URL-префикса
admin = Blueprint('admin', __name__, url_prefix='/admin')

@admin.route('/')
@login_required
def index():
    # Проверка роли
    if current_user.role not in ['admin', 'moderator']:
        return "Доступ запрещен", 403
    
    # Получение статистики
    quizzes_count = Quiz.query.count()
    active_games = Game.query.filter_by(status='active').count()
    teams_count = Team.query.count()
    
    return render_template(
        'admin/index.html',
        user=current_user,
        stats={
            'quizzes': quizzes_count,
            'active_games': active_games,
            'teams': teams_count
        }
    )

@admin.route('/quizzes')
@login_required
def quizzes():
    if current_user.role not in ['admin', 'moderator']:
        return "Доступ запрещен", 403
    
    quizzes = Quiz.query.all()
    return render_template('admin/quizzes.html', quizzes=quizzes)

@admin.route('/quizzes/create', methods=['POST'])
@login_required
def create_quiz():
    if current_user.role not in ['admin', 'moderator']:
        return "Доступ запрещен", 403
    
    try:
        title = request.form.get('title')
        if not title:
            flash('Название квиза обязательно', 'danger')
            return redirect(url_for('admin.quizzes'))
        
        # Создаем новый квиз
        quiz = Quiz(title=title, created_by=current_user.id)
        db.session.add(quiz)
        db.session.flush()  # Получаем ID квиза
        
        # Получаем данные о раундах
        rounds_data = []
        for key in request.form:
            if key.startswith('rounds[') and '][title]' in key:
                round_index = int(key.split('[')[1].split(']')[0])
                round_title = request.form[key]
                questions = []
                
                # Собираем вопросы для текущего раунда
                i = 0
                while f'rounds[{round_index}][questions][{i}][text]' in request.form:
                    question_text = request.form[f'rounds[{round_index}][questions][{i}][text]']
                    question_type = request.form[f'rounds[{round_index}][questions][{i}][type]']
                    
                    # Обработка разных типов вопросов
                    if question_type == 'multiple_choice':
                        # Получаем варианты ответов
                        options = request.form.getlist(f'rounds[{round_index}][questions][{i}][options][]')
                        correct_option = int(request.form[f'rounds[{round_index}][questions][{i}][correct_option]'])
                        
                        questions.append({
                            'text': question_text,
                            'type': question_type,
                            'options': options,
                            'answer': options[correct_option],
                            'order': i + 1
                        })
                    else:
                        # Для вопросов с ручным вводом
                        question_answer = request.form[f'rounds[{round_index}][questions][{i}][answer]']
                        questions.append({
                            'text': question_text,
                            'type': question_type,
                            'answer': question_answer,
                            'order': i + 1
                        })
                    i += 1
                
                if questions:  # Добавляем раунд только если есть вопросы
                    rounds_data.append({
                        'title': round_title,
                        'order': round_index + 1,
                        'questions': questions
                    })
        
        # Создаем раунды и вопросы
        for round_data in rounds_data:
            quiz_round = Round(
                quiz_id=quiz.id,
                title=round_data['title'],
                order=round_data['order']
            )
            db.session.add(quiz_round)
            db.session.flush()  # Получаем ID раунда
            
            for question_data in round_data['questions']:
                question = Question(
                    round_id=quiz_round.id,
                    text=question_data['text'],
                    type=question_data['type'],
                    correct_answer=question_data['answer'],
                    options=question_data.get('options'),
                    order=question_data['order']
                )
                db.session.add(question)
        
        db.session.commit()
        flash('Квиз успешно создан', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Ошибка при создании квиза: {str(e)}', 'danger')
    
    return redirect(url_for('admin.quizzes'))

@admin.route('/quizzes/upload', methods=['POST'])
def upload_quiz():
    """Загрузка квиза из файла"""
    if 'file' not in request.files:
        return jsonify({'error': 'Файл не найден'}), 400
        
    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'Файл не выбран'}), 400
        
    filename = file.filename.lower()
    if not (filename.endswith('.txt') or filename.endswith('.docx')):
        return jsonify({'error': 'Поддерживаются только файлы .txt и .docx'}), 400

    try:
        # Сохраняем файл во временную директорию
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(filename)[1]) as tmp_file:
            file.save(tmp_file.name)
            
            # Читаем содержимое файла
            if filename.endswith('.txt'):
                with open(tmp_file.name, 'r', encoding='utf-8') as f:
                    content = f.read()
            else:  # .docx
                doc = Document(tmp_file.name)
                content = '\n'.join(paragraph.text for paragraph in doc.paragraphs)
            
            # Удаляем временный файл
            os.unlink(tmp_file.name)
            
            # Парсим содержимое
            quiz = parse_quiz_content(content, current_user.id)
            
            return jsonify({
                'success': True,
                'quiz': {
                    'id': quiz.id,
                    'title': quiz.title,
                    'rounds_count': len(quiz.rounds),
                    'questions_count': sum(len(round.questions) for round in quiz.rounds)
                }
            })
            
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@admin.route('/quizzes/<int:quiz_id>/delete', methods=['POST'])
@login_required
def delete_quiz(quiz_id):
    if current_user.role not in ['admin', 'moderator']:
        return jsonify({'error': 'Доступ запрещен'}), 403
    
    try:
        quiz = Quiz.query.get_or_404(quiz_id)
        
        # Получаем все связанные игры
        games = Game.query.filter_by(quiz_id=quiz.id).all()
        
        for game in games:
            # Удаляем все ответы в игре
            Answer.query.filter_by(game_id=game.id).delete(synchronize_session='fetch')
            
            # Очищаем связи с командами
            game.teams = []
            db.session.flush()
        
        # Удаляем все игры
        Game.query.filter_by(quiz_id=quiz.id).delete(synchronize_session='fetch')
        db.session.flush()
        
        # Удаляем все вопросы и раунды
        for round in quiz.rounds:
            # Удаляем все ответы на вопросы этого раунда
            for question in round.questions:
                Answer.query.filter_by(question_id=question.id).delete(synchronize_session='fetch')
            
            # Удаляем все вопросы раунда
            Question.query.filter_by(round_id=round.id).delete(synchronize_session='fetch')
        
        # Удаляем все раунды
        Round.query.filter_by(quiz_id=quiz.id).delete(synchronize_session='fetch')
        db.session.flush()
        
        # Удаляем сам квиз
        db.session.delete(quiz)
        db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        print(f"Error deleting quiz: {str(e)}")  # Добавляем вывод ошибки в консоль
        return jsonify({'error': str(e)}), 500

@admin.route('/quizzes/<int:quiz_id>/edit')
@login_required
def edit_quiz(quiz_id):
    if current_user.role not in ['admin', 'moderator']:
        return "Доступ запрещен", 403
    
    quiz = Quiz.query.get_or_404(quiz_id)
    return render_template('admin/edit_quiz.html', quiz=quiz)

@admin.route('/quizzes/<int:quiz_id>/update', methods=['POST'])
@login_required
def update_quiz(quiz_id):
    if current_user.role not in ['admin', 'moderator']:
        return "Доступ запрещен", 403
    
    quiz = Quiz.query.get_or_404(quiz_id)
    
    try:
        # Обновляем название квиза
        quiz.title = request.form.get('title')
        
        # Получаем существующие ID раундов и вопросов
        existing_round_ids = {round.id for round in quiz.rounds}
        existing_question_ids = set()
        for round in quiz.rounds:
            existing_question_ids.update(q.id for q in round.questions)
        
        # Собираем новые ID из формы
        form_round_ids = set()
        form_question_ids = set()
        
        # Обрабатываем данные формы
        rounds_data = []
        for key in request.form:
            if key.startswith('rounds[') and '][title]' in key:
                round_index = int(key.split('[')[1].split(']')[0])
                round_id = request.form.get(f'rounds[{round_index}][id]')
                round_title = request.form[key]
                questions = []
                
                # Если есть ID, добавляем его в список
                if round_id and round_id.isdigit():
                    form_round_ids.add(int(round_id))
                
                # Собираем вопросы для текущего раунда
                i = 0
                while f'rounds[{round_index}][questions][{i}][text]' in request.form:
                    question_id = request.form.get(f'rounds[{round_index}][questions][{i}][id]')
                    question_text = request.form[f'rounds[{round_index}][questions][{i}][text]']
                    question_type = request.form[f'rounds[{round_index}][questions][{i}][type]']
                    
                    # Если есть ID, добавляем его в список
                    if question_id and question_id.isdigit():
                        form_question_ids.add(int(question_id))
                    
                    # Обработка разных типов вопросов
                    if question_type == 'multiple_choice':
                        # Получаем варианты ответов
                        options = request.form.getlist(f'rounds[{round_index}][questions][{i}][options][]')
                        correct_option = int(request.form[f'rounds[{round_index}][questions][{i}][correct_option]'])
                        
                        questions.append({
                            'id': question_id if question_id and question_id.isdigit() else None,
                            'text': question_text,
                            'type': question_type,
                            'options': options,
                            'answer': options[correct_option],
                            'order': i + 1
                        })
                    else:
                        # Для вопросов с ручным вводом
                        question_answer = request.form[f'rounds[{round_index}][questions][{i}][answer]']
                        questions.append({
                            'id': question_id if question_id and question_id.isdigit() else None,
                            'text': question_text,
                            'type': question_type,
                            'answer': question_answer,
                            'order': i + 1
                        })
                    i += 1
                
                rounds_data.append({
                    'id': round_id if round_id and round_id.isdigit() else None,
                    'title': round_title,
                    'order': round_index + 1,
                    'questions': questions
                })
        
        # Удаляем раунды и вопросы, которых нет в форме
        rounds_to_delete = existing_round_ids - form_round_ids
        questions_to_delete = existing_question_ids - form_question_ids
        
        if questions_to_delete:
            Question.query.filter(Question.id.in_(questions_to_delete)).delete(synchronize_session=False)
        
        if rounds_to_delete:
            Round.query.filter(Round.id.in_(rounds_to_delete)).delete(synchronize_session=False)
        
        # Обновляем или создаем раунды и вопросы
        for round_data in rounds_data:
            if round_data['id']:
                # Обновляем существующий раунд
                quiz_round = Round.query.get(round_data['id'])
                quiz_round.title = round_data['title']
                quiz_round.order = round_data['order']
            else:
                # Создаем новый раунд
                quiz_round = Round(
                    quiz_id=quiz.id,
                    title=round_data['title'],
                    order=round_data['order']
                )
                db.session.add(quiz_round)
                db.session.flush()  # Получаем ID нового раунда
            
            # Обновляем или создаем вопросы
            for question_data in round_data['questions']:
                if question_data['id']:
                    # Обновляем существующий вопрос
                    question = Question.query.get(question_data['id'])
                    question.text = question_data['text']
                    question.type = question_data['type']
                    question.correct_answer = question_data['answer']
                    question.options = question_data.get('options')
                    question.order = question_data['order']
                else:
                    # Создаем новый вопрос
                    question = Question(
                        round_id=quiz_round.id,
                        text=question_data['text'],
                        type=question_data['type'],
                        correct_answer=question_data['answer'],
                        options=question_data.get('options'),
                        order=question_data['order']
                    )
                    db.session.add(question)
        
        db.session.commit()
        flash('Квиз успешно обновлен', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Ошибка при обновлении квиза: {str(e)}', 'danger')
    
    return redirect(url_for('admin.edit_quiz', quiz_id=quiz_id))

@admin.route('/games')
@login_required
def games():
    if current_user.role not in ['admin', 'moderator']:
        return "Доступ запрещен", 403
    
    games = Game.query.all()
    quizzes = Quiz.query.all()
    moderators = User.query.filter(User.role.in_(['admin', 'moderator'])).all()
    
    return render_template('admin/games.html', 
                         games=games,
                         quizzes=quizzes,
                         moderators=moderators)

def alter_team_table():
    """Изменяет структуру таблицы team, делая captain_id nullable"""
    try:
        # Проверяем текущее состояние колонки
        result = db.session.execute(text("""
            SELECT is_nullable 
            FROM information_schema.columns 
            WHERE table_name = 'team' 
            AND column_name = 'captain_id';
        """))
        is_nullable = result.scalar()
        
        # Если колонка уже nullable, ничего не делаем
        if is_nullable == 'YES':
            print("Колонка captain_id уже nullable")
            return
            
        # Проверяем наличие данных
        result = db.session.execute(text("SELECT COUNT(*) FROM team;"))
        count = result.scalar()
        
        if count > 0:
            # Если есть данные, используем ALTER COLUMN
            db.session.execute(text("""
                ALTER TABLE team 
                ALTER COLUMN captain_id DROP NOT NULL;
            """))
        else:
            # Если данных нет, можно пересоздать таблицу
            db.session.execute(text("""
                DROP TABLE IF EXISTS team CASCADE;
                
                CREATE TABLE team (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    captain_id INTEGER REFERENCES "user"(id),
                    join_code VARCHAR(6) UNIQUE NOT NULL,
                    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            """))
            
        db.session.commit()
        print("Таблица team успешно изменена")
    except Exception as e:
        db.session.rollback()
        print(f"Ошибка при изменении таблицы team: {e}")
        raise

@admin.route('/games/new')
@login_required
def new_game():
    """Страница создания новой игры"""
    if current_user.role not in ['admin', 'moderator']:
        return "Доступ запрещен", 403
    
    try:
        # Проверяем и изменяем структуру таблицы team
        alter_team_table()
    except Exception as e:
        print(f"Ошибка при подготовке базы данных: {e}")
    
    quizzes = Quiz.query.all()
    return render_template('admin/create_game.html', quizzes=quizzes)

@admin.route('/games/create', methods=['POST'])
@login_required
def create_game():
    """API для создания игры"""
    if current_user.role not in ['admin', 'moderator']:
        return jsonify({'error': 'Доступ запрещен'}), 403
    
    try:
        data = request.get_json()
        if not data or 'quiz_id' not in data or 'teams' not in data:
            return jsonify({'error': 'Неверные данные'}), 400

        quiz_id = data['quiz_id']
        teams_data = data['teams']

        # Проверяем существование квиза
        quiz = Quiz.query.get(quiz_id)
        if not quiz:
            return jsonify({'error': 'Квиз не найден'}), 404

        # Генерируем код для игры
        while True:
            join_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            if not Game.query.filter_by(join_code=join_code).first():
                break

        # Создаем игру
        game = Game(
            quiz_id=quiz_id,
            moderator_id=current_user.id,
            join_code=join_code,
            status=Game.STATUS_SETUP
        )
        db.session.add(game)
        db.session.flush()  # Получаем ID игры

        # Создаем команды
        for team_data in teams_data:
            # Генерируем код для команды
            while True:
                team_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
                if not Team.query.filter_by(join_code=team_code).first():
                    break

            # Создаем команду (пока без капитана)
            team = Team(
                name=team_data['name'],
                join_code=team_code,
                captain_id=None  # Капитан будет назначен позже
            )
            db.session.add(team)
            db.session.flush()  # Получаем ID команды

            # Добавляем участников
            for member in team_data['members']:
                team_member = TeamMember(
                    team_id=team.id,
                    user_id=member['id']
                )
                db.session.add(team_member)

            # Добавляем команду в игру
            game.teams.append(team)

        db.session.commit()

        # Отправляем уведомления всем участникам
        try:
            from bot.bot import bot
            import asyncio
            
            async def send_notifications():
                tasks = []
                for team in game.teams:
                    for member in team.members:
                        message = (
                            f"Вы добавлены в команду {team.name} для игры {game.quiz.title}.\n"
                            f"Код для присоединения к игре: {game.join_code}"
                        )
                        tasks.append(bot.send_message(member.user.telegram_id, message))
                
                if tasks:  # Только если есть задачи для выполнения
                    await asyncio.gather(*tasks, return_exceptions=True)

            # Запускаем асинхронную функцию
            asyncio.run(send_notifications())
            
        except Exception as e:
            print(f"Ошибка отправки уведомлений: {e}")

        return jsonify({
            'success': True,
            'redirect_url': url_for('admin.manage_game', game_id=game.id)
        })

    except Exception as e:
        db.session.rollback()
        print(f"Error creating game: {e}")
        return jsonify({'error': str(e)}), 500

@admin.route('/games/<int:game_id>/delete', methods=['POST'])
@login_required
def delete_game(game_id):
    if current_user.role not in ['admin', 'moderator']:
        return jsonify({'error': 'Доступ запрещен'}), 403
    
    game = Game.query.get_or_404(game_id)
    
    try:
        # Удаляем связанные ответы
        Answer.query.filter_by(game_id=game.id).delete()
        
        # Удаляем саму игру
        db.session.delete(game)
        db.session.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@admin.route('/games/<int:game_id>/manage')
@login_required
def manage_game(game_id):
    if current_user.role not in ['admin', 'moderator']:
        return "Доступ запрещен", 403
    
    game = Game.query.get_or_404(game_id)
    return render_template('admin/manage_game.html', game=game)

@admin.route('/games/<int:game_id>/teams/add', methods=['POST'])
@login_required
def add_team_to_game(game_id):
    if current_user.role not in ['admin', 'moderator']:
        return jsonify({'error': 'Доступ запрещен'}), 403
    
    try:
        data = request.get_json()
        if not data or 'name' not in data or 'captain_id' not in data:
            return jsonify({'error': 'Не указаны необходимые данные'}), 400

        game = Game.query.get_or_404(game_id)
        name = data['name'].strip()
        captain_id = data['captain_id']

        # Проверяем существование капитана
        captain = User.query.get(captain_id)
        if not captain:
            return jsonify({'error': 'Пользователь с указанным ID не найден'}), 404

        # Проверяем уникальность названия команды в игре
        existing_team = Team.query.join(Game.teams).filter(
            Game.id == game_id,
            Team.name == name
        ).first()
        if existing_team:
            return jsonify({'error': 'Команда с таким названием уже существует в этой игре'}), 400

        # Проверяем, не состоит ли капитан уже в другой команде этой игры
        existing_member = TeamMember.query.join(Team).join(Game.teams).filter(
            Game.id == game_id,
            TeamMember.user_id == captain_id
        ).first()
        if existing_member:
            return jsonify({'error': 'Этот пользователь уже состоит в другой команде в этой игре'}), 400

        # Генерируем код для присоединения
        while True:
            join_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            if not Team.query.filter_by(join_code=join_code).first():
                break

        # Создаем команду
        team = Team(
            name=name,
            captain_id=captain_id,
            join_code=join_code
        )
        db.session.add(team)
        db.session.flush()  # Получаем ID команды

        # Добавляем капитана как участника
        team_member = TeamMember(team_id=team.id, user_id=captain_id)
        db.session.add(team_member)

        # Добавляем команду в игру
        game.teams.append(team)
        db.session.commit()

        # Отправляем уведомление капитану через бота
        try:
            from bot.bot import bot
            import asyncio
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                loop.run_until_complete(bot.send_message(
                    captain.telegram_id,
                    f"Вы назначены капитаном команды {team.name} в игре {game.quiz.title}.\n"
                    f"Код для присоединения к команде: {team.join_code}"
                ))
            finally:
                loop.close()
        except Exception as e:
            print(f"Ошибка отправки уведомления: {e}")

        return jsonify({
            'success': True,
            'team': {
                'id': team.id,
                'name': team.name,
                'captain': {
                    'id': captain.id,
                    'username': captain.username
                },
                'join_code': team.join_code
            }
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@admin.route('/games/<int:game_id>/teams/<int:team_id>/remove', methods=['POST'])
@login_required
def remove_team_from_game(game_id, team_id):
    if current_user.role not in ['admin', 'moderator']:
        return jsonify({'error': 'Доступ запрещен'}), 403
    
    try:
        game = Game.query.get_or_404(game_id)
        team = Team.query.get_or_404(team_id)
        
        # Проверяем, что команда действительно в этой игре
        if team not in game.teams:
            return jsonify({'error': 'Команда не найдена в этой игре'}), 404
        
        # Удаляем команду из игры
        game.teams.remove(team)
        
        # Удаляем все ответы команды в этой игре
        Answer.query.filter_by(game_id=game.id, team_id=team.id).delete()
        
        # Удаляем участников команды
        TeamMember.query.filter_by(team_id=team.id).delete()
        
        # Удаляем саму команду
        db.session.delete(team)
        
        db.session.commit()
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        print("Error:", str(e))  # Отладочный вывод
        return jsonify({'error': str(e)}), 500

@admin.route('/teams/<int:team_id>/members')
@login_required
def get_team_members(team_id):
    if current_user.role not in ['admin', 'moderator']:
        return jsonify({'error': 'Доступ запрещен'}), 403
    
    try:
        team = Team.query.get_or_404(team_id)
        members = []
        
        for member in team.members:
            user = member.user
            members.append({
                'id': user.id,
                'username': user.username,
                'telegram_id': user.telegram_id,
                'is_captain': user.id == team.captain_id
            })
        
        return jsonify({
            'success': True,
            'members': members
        })
        
    except Exception as e:
        print("Error:", str(e))  # Отладочный вывод
        return jsonify({'error': str(e)}), 500

@admin.route('/teams/<int:team_id>/members/add', methods=['POST'])
@login_required
def add_team_member(team_id):
    if current_user.role not in ['admin', 'moderator']:
        return jsonify({'error': 'Доступ запрещен'}), 403
    
    try:
        data = request.get_json()
        if not data or 'user_id' not in data:
            return jsonify({'error': 'Не указан ID пользователя'}), 400

        team = Team.query.get_or_404(team_id)
        user = User.query.get(data['user_id'])
        
        if not user:
            return jsonify({'error': 'Пользователь не найден'}), 404
            
        # Проверяем, не состоит ли пользователь уже в команде в этой игре
        game_id = db.session.query(Game.id).join(Game.teams).filter(Team.id == team_id).scalar()
        if game_id:
            existing_member = TeamMember.query.join(Team).join(Game.teams).filter(
                Game.id == game_id,
                TeamMember.user_id == user.id
            ).first()
            if existing_member:
                return jsonify({'error': 'Этот пользователь уже состоит в команде в этой игре'}), 400

        # Добавляем пользователя в команду
        team_member = TeamMember(team_id=team_id, user_id=user.id)
        db.session.add(team_member)
        db.session.commit()

        return jsonify({
            'success': True,
            'member': {
                'id': user.id,
                'username': user.username,
                'telegram_id': user.telegram_id,
                'is_captain': user.id == team.captain_id
            }
        })

    except Exception as e:
        db.session.rollback()
        print("Error:", str(e))
        return jsonify({'error': str(e)}), 500

@admin.route('/teams/<int:team_id>/members/<int:user_id>/remove', methods=['POST'])
@login_required
def remove_team_member(team_id, user_id):
    if current_user.role not in ['admin', 'moderator']:
        return jsonify({'error': 'Доступ запрещен'}), 403
    
    try:
        team = Team.query.get_or_404(team_id)
        
        # Проверяем, не пытаемся ли мы удалить капитана
        if team.captain_id == user_id:
            return jsonify({'error': 'Нельзя удалить капитана команды. Сначала назначьте нового капитана.'}), 400

        # Удаляем участника
        member = TeamMember.query.filter_by(team_id=team_id, user_id=user_id).first()
        if not member:
            return jsonify({'error': 'Пользователь не является участником команды'}), 404

        db.session.delete(member)
        db.session.commit()

        return jsonify({'success': True})

    except Exception as e:
        db.session.rollback()
        print("Error:", str(e))
        return jsonify({'error': str(e)}), 500

@admin.route('/teams/<int:team_id>/captain', methods=['POST'])
@login_required
def change_team_captain(team_id):
    if current_user.role not in ['admin', 'moderator']:
        return jsonify({'error': 'Доступ запрещен'}), 403
    
    try:
        data = request.get_json()
        if not data or 'user_id' not in data:
            return jsonify({'error': 'Не указан ID нового капитана'}), 400

        team = Team.query.get_or_404(team_id)
        new_captain = User.query.get(data['user_id'])
        
        if not new_captain:
            return jsonify({'error': 'Пользователь не найден'}), 404

        # Проверяем, является ли пользователь участником команды
        member = TeamMember.query.filter_by(team_id=team_id, user_id=new_captain.id).first()
        if not member:
            return jsonify({'error': 'Пользователь должен быть участником команды, чтобы стать капитаном'}), 400

        # Меняем капитана
        old_captain_id = team.captain_id
        team.captain_id = new_captain.id
        db.session.commit()

        return jsonify({
            'success': True,
            'team': {
                'id': team.id,
                'name': team.name,
                'captain': {
                    'id': new_captain.id,
                    'username': new_captain.username,
                    'telegram_id': new_captain.telegram_id
                },
                'old_captain_id': old_captain_id
            }
        })

    except Exception as e:
        db.session.rollback()
        print("Error:", str(e))
        return jsonify({'error': str(e)}), 500

@admin.route('/teams')
@login_required
def teams():
    if current_user.role not in ['admin', 'moderator']:
        return "Доступ запрещен", 403
    
    teams = Team.query.all()
    return render_template('admin/teams.html', teams=teams) 

@admin.route('/moderators')
@login_required
def moderators():
    if current_user.role != 'admin':
        return "Доступ запрещен", 403
    
    users = User.query.filter(User.role.in_(['admin', 'moderator'])).all()
    return render_template('admin/moderators.html', users=users)

@admin.route('/moderators/add', methods=['POST'])
@login_required
def add_moderator():
    if current_user.role != 'admin':
        return "Доступ запрещен", 403
    
    try:
        user_id = request.form.get('user_id')
        if not user_id:
            flash('ID пользователя обязателен', 'danger')
            return redirect(url_for('admin.moderators'))
        
        user = User.query.get(user_id)
        if not user:
            flash('Пользователь не найден', 'danger')
            return redirect(url_for('admin.moderators'))
        
        if user.role in ['admin', 'moderator']:
            flash('Пользователь уже является администратором или модератором', 'warning')
            return redirect(url_for('admin.moderators'))
        
        user.role = 'moderator'
        db.session.commit()
        flash('Модератор успешно добавлен', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Ошибка при добавлении модератора: {str(e)}', 'danger')
    
    return redirect(url_for('admin.moderators'))

@admin.route('/moderators/<int:user_id>/delete', methods=['POST'])
@login_required
def delete_moderator(user_id):
    if current_user.role != 'admin':
        return jsonify({'error': 'Доступ запрещен'}), 403
    
    try:
        print(f"Attempting to delete moderator with ID: {user_id}")  # Отладочный вывод
        user = User.query.get_or_404(user_id)
        
        if user.role != 'moderator':
            return jsonify({'error': 'Пользователь не является модератором'}), 400
        
        if user.id == current_user.id:
            return jsonify({'error': 'Нельзя удалить самого себя'}), 400
        
        # Передаем все активные игры модератора администратору
        active_games = Game.query.filter_by(
            moderator_id=user.id
        ).filter(Game.status.in_(['created', 'active', 'paused'])).all()
        
        for game in active_games:
            game.moderator_id = current_user.id  # Передаем игру текущему админу
        
        user.role = 'player'
        db.session.commit()
        
        # Если были переданы игры, возвращаем информацию об этом
        if active_games:
            return jsonify({
                'success': True,
                'message': f'Модератор удален. {len(active_games)} активных игр передано администратору.'
            })
        
        return jsonify({'success': True, 'message': 'Модератор удален.'})
        
    except Exception as e:
        db.session.rollback()
        print(f"Error deleting moderator: {str(e)}")  # Отладочный вывод
        return jsonify({'error': str(e)}), 500 

@admin.route('/games/<int:game_id>/room')
@login_required
def game_room(game_id):
    if current_user.role not in ['admin', 'moderator']:
        return "Доступ запрещен", 403
    
    game = Game.query.get_or_404(game_id)
    
    # Проверяем, что игра готова к началу или уже идет
    if game.status == Game.STATUS_SETUP:
        return redirect(url_for('admin.manage_game', game_id=game_id))
    
    return render_template('admin/game_room.html', game=game)

@admin.route('/games/<int:game_id>/ready', methods=['POST'])
@login_required
def ready_game(game_id):
    if current_user.role not in ['admin', 'moderator']:
        return jsonify({'error': 'Доступ запрещен'}), 403
    
    try:
        game = Game.query.get_or_404(game_id)
        print(f"Текущий статус игры {game_id}: '{game.status}'")
        print(f"Константа STATUS_READY: '{Game.STATUS_READY}'")
        
        # Проверяем, есть ли команды и игроки
        if not game.teams:
            return jsonify({'error': 'Нельзя начать игру без команд'}), 400
        
        for team in game.teams:
            if not team.members:
                return jsonify({'error': f'В команде {team.name} нет игроков'}), 400
            if not team.captain_id:
                return jsonify({'error': f'В команде {team.name} не назначен капитан'}), 400
        
        # Меняем статус игры
        game.status = Game.STATUS_READY
        print(f"Новый статус игры {game_id}: '{game.status}'")
        db.session.commit()
        print(f"Статус после коммита: '{game.status}'")
        print(f"Проверка статуса: {game.status == Game.STATUS_READY}")
        print(f"Длина статуса: {len(game.status)}, длина константы: {len(Game.STATUS_READY)}")
        print(f"Байты статуса: {game.status.encode()}")
        print(f"Байты константы: {Game.STATUS_READY.encode()}")

        # Отправляем уведомления всем участникам через бота
        try:
            from bot.bot import bot
            import asyncio
            
            async def send_notifications():
                tasks = []
                for team in game.teams:
                    for member in team.members:
                        tasks.append(bot.send_message(
                            member.user.telegram_id,
                            f"Игра {game.quiz.title} готова к началу!\n"
                            f"Вы уже добавлены в команду {team.name}.\n"
                            f"Для присоединения к игре используйте команду:\n"
                            f"/join {game.join_code}"
                        ))
                
                if tasks:  # Только если есть задачи для выполнения
                    await asyncio.gather(*tasks, return_exceptions=True)
            
            # Запускаем асинхронную функцию
            asyncio.run(send_notifications())
            
        except Exception as e:
            print(f"Ошибка отправки уведомлений: {e}")
        
        # Перенаправляем на страницу игровой комнаты
        return jsonify({
            'success': True,
            'redirect_url': url_for('admin.game_room', game_id=game_id)
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@admin.route('/games/<int:game_id>/start', methods=['POST'])
@login_required
def start_game(game_id):
    """Начинает игру"""
    if current_user.role not in ['admin', 'moderator']:
        return jsonify({'error': 'Доступ запрещен'}), 403
    
    try:
        game = Game.query.get_or_404(game_id)
        
        # Проверяем, что игра готова к началу
        if game.status != Game.STATUS_READY:
            return jsonify({'error': 'Игра не готова к началу'}), 400
        
        # Проверяем, что все команды имеют капитанов
        for team in game.teams:
            if not team.captain_id:
                return jsonify({'error': f'В команде {team.name} не назначен капитан'}), 400
        
        # Меняем статус игры
        game.status = Game.STATUS_ACTIVE
        
        # Берем первый вопрос первого раунда
        first_round = Round.query.filter_by(quiz_id=game.quiz_id, order=1).first()
        if first_round:
            first_question = Question.query.filter_by(round_id=first_round.id, order=1).first()
            if first_question:
                game.current_question_id = first_question.id
        
        db.session.commit()

        # Отправляем уведомления всем участникам через бота
        try:
            from bot.bot import bot
            import asyncio
            
            async def send_notifications():
                tasks = []
                for team in game.teams:
                    for member in team.members:
                        tasks.append(bot.send_message(
                            member.user.telegram_id,
                            f"Игра {game.quiz.title} началась!\n"
                            f"Вы играете за команду {team.name}."
                        ))
                
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
            
            # Запускаем асинхронную функцию
            asyncio.run(send_notifications())
            
        except Exception as e:
            print(f"Ошибка отправки уведомлений: {e}")
        
        return jsonify({
            'success': True,
            'game': {
                'id': game.id,
                'status': game.status,
                'current_question_id': game.current_question_id
            }
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Error starting game: {e}")
        return jsonify({'error': str(e)}), 500

@admin.route('/games/<int:game_id>/teams/<int:team_id>/score', methods=['POST'])
@login_required
def update_team_score(game_id, team_id):
    if current_user.role not in ['admin', 'moderator']:
        return jsonify({'error': 'Доступ запрещен'}), 403
    
    try:
        data = request.get_json()
        if not data or 'score' not in data:
            return jsonify({'error': 'Не указан счет'}), 400
        
        game = Game.query.get_or_404(game_id)
        team = Team.query.get_or_404(team_id)
        
        # Проверяем, что команда участвует в игре
        if team not in game.teams:
            return jsonify({'error': 'Команда не участвует в этой игре'}), 400
        
        # Обновляем счет последнего ответа команды
        answer = Answer.query.filter_by(
            game_id=game_id,
            team_id=team_id,
            question_id=game.current_question_id
        ).order_by(Answer.created_at.desc()).first()
        
        if answer:
            answer.score = float(data['score'])
            db.session.commit()
            
            # Отправляем обновление всем участникам
            from ..socket import broadcast_scoreboard
            broadcast_scoreboard(game_id)
            
            return jsonify({'success': True})
        else:
            return jsonify({'error': 'Ответ не найден'}), 404
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@admin.route('/games/<int:game_id>/next_question', methods=['POST'])
@login_required
def next_question(game_id):
    """Переходит к следующему вопросу"""
    if current_user.role not in ['admin', 'moderator']:
        return jsonify({'error': 'Доступ запрещен'}), 403
    
    try:
        game = Game.query.get_or_404(game_id)
        
        if game.status != Game.STATUS_ACTIVE:
            return jsonify({'error': 'Игра не активна'}), 400
        
        # Получаем текущий раунд и вопрос
        current_round = None
        current_question = None
        if game.current_question_id:
            current_question = Question.query.get(game.current_question_id)
            current_round = current_question.round

        # Ищем следующий вопрос
        next_question = None
        if current_question:
            # Пробуем найти следующий вопрос в текущем раунде
            next_question = Question.query.filter_by(
                round_id=current_round.id,
                order=current_question.order + 1
            ).first()

            if not next_question:
                # Если вопросов в текущем раунде больше нет, переходим к следующему раунду
                next_round = Round.query.filter_by(
                    quiz_id=game.quiz_id,
                    order=current_round.order + 1
                ).first()

                if next_round:
                    next_question = Question.query.filter_by(
                        round_id=next_round.id,
                        order=1
                    ).first()
        else:
            # Если текущего вопроса нет, берем первый вопрос первого раунда
            first_round = Round.query.filter_by(quiz_id=game.quiz_id, order=1).first()
            if first_round:
                next_question = Question.query.filter_by(
                    round_id=first_round.id,
                    order=1
                ).first()

        if not next_question:
            return jsonify({'error': 'Больше нет вопросов'}), 400

        # Обновляем текущий вопрос
        game.current_question_id = next_question.id
        db.session.commit()

        # Получаем информацию о текущем раунде и общем количестве раундов
        total_rounds = Round.query.filter_by(quiz_id=game.quiz_id).count()
        total_questions_in_round = len(next_question.round.questions)

        # Формируем сообщения для разных ролей
        admin_message = (
            f"📍 Текущее положение:\n"
            f"Раунд {next_question.round.order} из {total_rounds}: {next_question.round.title}\n"
            f"Вопрос {next_question.order} из {total_questions_in_round}\n\n"
            f"📚 Следующий раунд:\n"
            f"Переходим к раунду {next_question.round.order + 1} из {total_rounds}: {next_question.round.title}\n"
            f"Начнем с вопроса {next_question.order} из {total_questions_in_round}\n"
            f"Тип: {'Выбор варианта' if next_question.type == 'multiple_choice' else 'Свободный ответ'}"
        )

        player_message = (
            f"📍 Текущий раунд: {next_question.round.title} (Раунд {next_question.round.order} из {total_rounds})\n"
            f"❓ Вопрос {next_question.order} из {total_questions_in_round}\n"
            f"⭐ Тип вопроса: {'Выбор варианта' if next_question.type == 'multiple_choice' else 'Свободный ответ'}\n"
            f"⏱ Время на ответ: {next_question.time_limit} секунд\n"
            f"💎 Баллы за правильный ответ: {next_question.points}"
        )

        # Отправляем уведомления
        try:
            from bot.bot import bot
            import asyncio

            async def send_notifications():
                tasks = []
                for team in game.teams:
                    for member in team.members:
                        # Определяем, какое сообщение отправить
                        message = admin_message if member.user.role in ['admin', 'moderator'] else player_message
                        tasks.append(bot.send_message(member.user.telegram_id, message))

                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

            asyncio.run(send_notifications())

        except Exception as e:
            print(f"Ошибка отправки уведомлений: {e}")

        return jsonify({
            'success': True,
            'question': {
                'id': next_question.id,
                'text': next_question.text,
                'type': next_question.type,
                'options': next_question.options if next_question.type == 'multiple_choice' else None,
                'order': next_question.order,
                'points': next_question.points,
                'time_limit': next_question.time_limit,
                'round': {
                    'id': next_question.round.id,
                    'title': next_question.round.title,
                    'order': next_question.round.order,
                    'total_rounds': total_rounds,
                    'total_questions': total_questions_in_round
                }
            }
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Error getting next question: {e}")
        return jsonify({'error': str(e)}), 500

@admin.route('/teams/<int:team_id>/update', methods=['POST'])
@login_required
def update_team(team_id):
    if current_user.role not in ['admin', 'moderator']:
        return jsonify({'error': 'Доступ запрещен'}), 403
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Данные не предоставлены'}), 400

        team = Team.query.get_or_404(team_id)
        
        # Обновление названия команды
        if 'name' in data:
            new_name = data['name'].strip()
            if not new_name:
                return jsonify({'error': 'Название команды не может быть пустым'}), 400
                
            # Проверяем уникальность имени в рамках игры
            existing_team = Team.query.join(Game.teams).filter(
                Game.id == team.game.id,
                Team.name == new_name,
                Team.id != team_id
            ).first()
            
            if existing_team:
                return jsonify({'error': 'Команда с таким названием уже существует в этой игре'}), 400
                
            team.name = new_name

        db.session.commit()
        
        return jsonify({
            'success': True,
            'team': {
                'id': team.id,
                'name': team.name,
                'captain_id': team.captain_id
            }
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@admin.route('/teams/<int:team_id>/transfer-captain', methods=['POST'])
@login_required
def transfer_team_captain(team_id):
    if current_user.role not in ['admin', 'moderator']:
        return jsonify({'error': 'Доступ запрещен'}), 403
    
    try:
        data = request.get_json()
        if not data or 'new_captain_id' not in data:
            return jsonify({'error': 'ID нового капитана не указан'}), 400

        team = Team.query.get_or_404(team_id)
        new_captain_id = data['new_captain_id']
        
        # Проверяем, что новый капитан существует и является членом команды
        new_captain = User.query.get(new_captain_id)
        if not new_captain:
            return jsonify({'error': 'Пользователь не найден'}), 404
            
        member = TeamMember.query.filter_by(team_id=team_id, user_id=new_captain_id).first()
        if not member:
            return jsonify({'error': 'Пользователь не является членом команды'}), 400

        # Сохраняем старого капитана для ответа
        old_captain_id = team.captain_id
        
        # Отправляем уведомления через бота
        try:
            from bot.bot import bot
            import asyncio
            
            async def send_captain_notifications():
                tasks = []
                
                # Если был старый капитан, отправляем ему уведомление
                if old_captain_id:
                    old_captain = User.query.get(old_captain_id)
                    if old_captain:
                        tasks.append(bot.send_message(
                            old_captain.telegram_id,
                            f"Вы больше не являетесь капитаном команды {team.name}"
                        ))
                
                # Отправляем уведомление новому капитану
                tasks.append(bot.send_message(
                    new_captain.telegram_id,
                    f"Вы назначены новым капитаном команды {team.name}"
                ))
                
                if tasks:  # Только если есть задачи для выполнения
                    await asyncio.gather(*tasks, return_exceptions=True)
            
            # Запускаем асинхронную функцию
            asyncio.run(send_captain_notifications())
                
        except Exception as e:
            print(f"Ошибка отправки уведомления: {e}")

        # Меняем капитана
        team.captain_id = new_captain_id
        db.session.commit()

        return jsonify({
            'success': True,
            'team': {
                'id': team.id,
                'name': team.name,
                'captain': {
                    'id': new_captain.id,
                    'username': new_captain.username
                },
                'old_captain_id': old_captain_id
            }
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@admin.route('/games/<int:game_id>/broadcast', methods=['POST'])
@login_required
def broadcast_message(game_id):
    if current_user.role not in ['admin', 'moderator']:
        return jsonify({'error': 'Доступ запрещен'}), 403
    
    try:
        data = request.get_json()
        if not data or 'message' not in data:
            return jsonify({'error': 'Сообщение не указано'}), 400

        game = Game.query.get_or_404(game_id)
        message = data['message'].strip()
        
        if not message:
            return jsonify({'error': 'Сообщение не может быть пустым'}), 400

        # Отправляем сообщение всем участникам через бота
        from bot.bot import bot
        sent_count = 0
        errors = []
        
        # Создаем и запускаем event loop для асинхронных операций
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # Собираем все задачи отправки сообщений
            tasks = []
            for team in game.teams:
                for member in team.members:
                    tasks.append(bot.send_message(member.user.telegram_id, message))
            
            # Запускаем все задачи параллельно
            results = loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            
            # Подсчитываем успешные отправки и ошибки
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    member = next((m for t in game.teams for m in t.members))[i]
                    errors.append(f"Ошибка отправки {member.user.username}: {str(result)}")
                else:
                    sent_count += 1
                    
        finally:
            loop.close()

        return jsonify({
            'success': True,
            'sent_count': sent_count,
            'errors': errors
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@admin.route('/games/<int:game_id>/scores', methods=['POST'])
@login_required
def update_team_scores(game_id):
    if current_user.role not in ['admin', 'moderator']:
        return jsonify({'error': 'Доступ запрещен'}), 403
    
    try:
        data = request.get_json()
        if not data or 'scores' not in data:
            return jsonify({'error': 'Данные о счете не предоставлены'}), 400

        game = Game.query.get_or_404(game_id)
        scores = data['scores']  # Ожидаем формат: {team_id: score}
        
        for team_id, score in scores.items():
            team = Team.query.get(int(team_id))
            if team and team in game.teams:
                # Обновляем счет команды
                answer = Answer.query.filter_by(
                    game_id=game_id,
                    team_id=team.id,
                    question_id=game.current_question_id
                ).order_by(Answer.created_at.desc()).first()
                
                if answer:
                    answer.score = float(score)
                else:
                    # Создаем новый ответ, если его нет
                    answer = Answer(
                        game_id=game_id,
                        team_id=team.id,
                        question_id=game.current_question_id,
                        score=float(score)
                    )
                    db.session.add(answer)

        db.session.commit()

        # Обновляем таблицу результатов через WebSocket
        from ..socket import broadcast_scoreboard
        broadcast_scoreboard(game_id)

        return jsonify({'success': True})

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500 

@admin.route('/users/<int:user_id>/check')
@login_required
def check_user(user_id):
    """Проверка существования пользователя и получение его username"""
    if current_user.role not in ['admin', 'moderator']:
        return jsonify({'error': 'Доступ запрещен'}), 403
    
    try:
        user = User.query.get(user_id)
        if user:
            return jsonify({
                'exists': True,
                'username': user.username
            })
        return jsonify({'exists': False}), 404
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500 

@admin.route('/games/<int:game_id>/pause', methods=['POST'])
@login_required
def pause_game(game_id):
    """Ставит игру на паузу"""
    if current_user.role not in ['admin', 'moderator']:
        return jsonify({'error': 'Доступ запрещен'}), 403
    
    try:
        game = Game.query.get_or_404(game_id)
        
        if game.status != Game.STATUS_ACTIVE:
            return jsonify({'error': 'Игра не активна'}), 400
        
        game.status = Game.STATUS_PAUSED
        db.session.commit()
        
        # Отправляем уведомления
        try:
            from bot.bot import bot
            import asyncio
            
            async def send_notifications():
                tasks = []
                for team in game.teams:
                    for member in team.members:
                        tasks.append(bot.send_message(
                            member.user.telegram_id,
                            "Игра приостановлена. Ожидайте продолжения."
                        ))
                
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
            
            asyncio.run(send_notifications())
            
        except Exception as e:
            print(f"Ошибка отправки уведомлений: {e}")
        
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@admin.route('/games/<int:game_id>/resume', methods=['POST'])
@login_required
def resume_game(game_id):
    """Возобновляет игру"""
    if current_user.role not in ['admin', 'moderator']:
        return jsonify({'error': 'Доступ запрещен'}), 403
    
    try:
        game = Game.query.get_or_404(game_id)
        
        if game.status != Game.STATUS_PAUSED:
            return jsonify({'error': 'Игра не на паузе'}), 400
        
        game.status = Game.STATUS_ACTIVE
        db.session.commit()
        
        # Отправляем уведомления
        try:
            from bot.bot import bot
            import asyncio
            
            async def send_notifications():
                tasks = []
                for team in game.teams:
                    for member in team.members:
                        tasks.append(bot.send_message(
                            member.user.telegram_id,
                            "Игра продолжается!"
                        ))
                
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
            
            asyncio.run(send_notifications())
            
        except Exception as e:
            print(f"Ошибка отправки уведомлений: {e}")
        
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500 

@admin.route('/quizzes/create_from_text', methods=['POST'])
@login_required
def create_quiz_from_text():
    if current_user.role not in ['admin', 'moderator']:
        return "Доступ запрещен", 403
    
    try:
        quiz_text = request.form.get('quiz_text')
        if not quiz_text:
            flash('Текст квиза обязателен', 'danger')
            return redirect(url_for('admin.quizzes'))
        
        # Используем существующий парсер для обработки текста
        from .quiz_parser import parse_quiz_content
        quiz = parse_quiz_content(quiz_text, current_user.id)
        
        if quiz:
            flash('Квиз успешно создан', 'success')
        else:
            flash('Ошибка при создании квиза', 'danger')
            
    except Exception as e:
        flash(f'Ошибка при создании квиза: {str(e)}', 'danger')
    
    return redirect(url_for('admin.quizzes')) 