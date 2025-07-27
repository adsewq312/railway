from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_login import current_user
from .models import db, Game, User, TeamMember, Team
from datetime import datetime

socketio = SocketIO()

@socketio.on('connect')
def handle_connect():
    """Обработка подключения клиента"""
    if not current_user.is_authenticated:
        return False

@socketio.on('join_game_room')
def on_join_game_room(data):
    """Обработчик присоединения к игровой комнате"""
    if not current_user.is_authenticated:
        return
    
    game_id = data.get('game_id')
    if not game_id:
        return
    
    # Получаем игру и проверяем её статус
    game = Game.query.get(game_id)
    if not game:
        emit('error', {'message': 'Игра не найдена'})
        return
        
    if game.status not in ['setup', 'ready']:
        emit('error', {'message': 'Невозможно присоединиться к игре, которая уже началась'})
        return
    
    # Присоединяемся к комнате игры
    room = f"game_{game_id}"
    join_room(room)
    
    # Если это игрок (не админ/модератор), отправляем уведомление о присоединении
    if current_user.role == 'player':
        # Проверяем, является ли пользователь участником игры
        team_member = TeamMember.query.join(TeamMember.team).join(Game.teams).filter(
            Game.id == game_id,
            TeamMember.user_id == current_user.id
        ).first()
        
        if team_member:
            emit('player_joined', {
                'user_id': current_user.id,
                'username': current_user.username,
                'team_id': team_member.team_id
            }, room=room)

@socketio.on('leave_game_room')
def handle_leave_game_room(data):
    """Обработка выхода из игровой комнаты"""
    if not current_user.is_authenticated:
        return
    
    game_id = data.get('game_id')
    if not game_id:
        return
    
    # Покидаем комнату игры
    room = f'game_{game_id}'
    leave_room(room)
    
    # Отправляем сообщение о выходе
    emit('chat_message', {
        'type': 'system',
        'sender': 'Система',
        'message': f'{current_user.username} покинул игру',
        'timestamp': datetime.utcnow().isoformat()
    }, room=room)

@socketio.on('send_message')
def handle_send_message(data):
    """Обработка отправки сообщения в чат"""
    if not current_user.is_authenticated:
        return
    
    game_id = data.get('game_id')
    message = data.get('message')
    
    if not game_id or not message:
        return
    
    # Проверяем, что пользователь имеет доступ к игре
    game = Game.query.get(game_id)
    if not game:
        return
    
    if current_user.role not in ['admin', 'moderator'] and \
       not TeamMember.query.join(TeamMember.team).filter(
           TeamMember.team.in_([t.id for t in game.teams]),
           TeamMember.user_id == current_user.id
       ).first():
        return
    
    # Отправляем сообщение всем в комнате
    room = f'game_{game_id}'
    emit('chat_message', {
        'type': 'moderator' if current_user.role in ['admin', 'moderator'] else 'player',
        'sender': current_user.username,
        'message': message,
        'timestamp': datetime.utcnow().isoformat()
    }, room=room)

@socketio.on('disconnect')
def on_disconnect():
    """Обработчик отключения от сервера"""
    if not current_user.is_authenticated:
        return
    
    # Находим все игры, в которых участвует пользователь
    team_members = TeamMember.query\
        .join(TeamMember.team)\
        .join(Team.games)\
        .filter(
            TeamMember.user_id == current_user.id,
            Game.status.in_(['setup', 'ready', 'active'])
        ).all()
    
    for team_member in team_members:
        # Получаем игру через связь с командой
        games = team_member.team.games
        for game in games:
            if game.status in ['setup', 'ready', 'active']:
                room = f"game_{game.id}"
                emit('player_left', {
                    'user_id': current_user.id,
                    'username': current_user.username,
                    'team_id': team_member.team_id
                }, room=room)
                leave_room(room)

def broadcast_game_state(game_id):
    """Отправляет обновление состояния игры всем участникам"""
    game = Game.query.get(game_id)
    if not game:
        return
    
    room = f"game_{game_id}"
    emit('game_state', {
        'status': game.status,
        'current_question_id': game.current_question_id
    }, room=room, namespace='/')

def broadcast_scoreboard(game_id):
    """Отправляет обновление таблицы результатов всем участникам"""
    game = Game.query.get(game_id)
    if not game:
        return
    
    scores = []
    for team in game.teams:
        scores.append({
            'team_id': team.id,
            'name': team.name,
            'total_score': sum(answer.score or 0 for answer in team.answers)
        })
    
    room = f"game_{game_id}"
    emit('scoreboard_update', {'scores': scores}, room=room, namespace='/') 