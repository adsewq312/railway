import re
from docx import Document
from typing import Dict, List, Optional, Tuple
from ..models import db, Quiz, Round, Question

class QuizParser:
    def __init__(self):
        self.quiz_title = ""
        self.quiz_description = ""
        self.current_round = None
        self.rounds = []

    def parse_txt(self, file_content: str) -> Tuple[str, str, List[Dict]]:
        """Парсит содержимое .txt файла"""
        lines = file_content.split('\n')
        current_question = None
        
        for line_number, line in enumerate(lines):
            line = line.strip()
            if not line or line.startswith('==='): continue
            
            # Парсим название квиза
            if line.startswith('# '):
                self.quiz_title = line[2:].strip()
                print(f"Название квиза: {self.quiz_title}")
                continue
            
            # Парсим описание квиза
            if self.quiz_title and not line.startswith('##') and not self.current_round:
                if not line.startswith('Тип:') and not line.startswith('Ответ:'):
                    self.quiz_description = line.strip()
                    print(f"Описание квиза: {self.quiz_description}")
                continue
            
            # Парсим раунд
            if line.startswith('## '):
                # Сохраняем предыдущий вопрос в текущий раунд, если он есть
                if (current_question and self.current_round and
                    current_question.get('text') and 
                    current_question.get('type') and 
                    current_question.get('correct_answer')):
                    print(f"Сохраняем последний вопрос раунда: {current_question['text']}")
                    self.current_round['questions'].append(current_question)
                    current_question = None
                
                # Создаем новый раунд
                round_data = {
                    'title': line[3:].strip(),
                    'questions': []
                }
                self.rounds.append(round_data)
                self.current_round = round_data
                print(f"\nНовый раунд: {round_data['title']}")
                continue
            
            # Парсим вопрос
            if self.current_round:
                # Новый вопрос начинается с цифры и точки
                if re.match(r'^\d+\.', line):
                    # Сохраняем предыдущий вопрос, если он полностью заполнен
                    if (current_question and 
                        current_question.get('text') and 
                        current_question.get('type') and 
                        current_question.get('correct_answer')):
                        print(f"Сохраняем вопрос: {current_question['text']}")
                        self.current_round['questions'].append(current_question)
                    
                    # Создаем новый вопрос
                    current_question = {
                        'text': line.split('.', 1)[1].strip(),
                        'type': None,
                        'correct_answer': None,
                        'options': [],  # Пустой список по умолчанию
                        'points': 1,
                        'time_limit': 30
                    }
                    print(f"\nНовый вопрос: {current_question['text']}")
                    continue
                
                # Парсим параметры вопроса
                if current_question:
                    if line.startswith('Тип:'):
                        current_question['type'] = line.split(':', 1)[1].strip()
                        print(f"Тип вопроса: {current_question['type']}")
                    elif line.startswith('Ответ:'):
                        current_question['correct_answer'] = line.split(':', 1)[1].strip()
                        print(f"Правильный ответ: {current_question['correct_answer']}")
                    elif line.startswith('Варианты:'):
                        options = line.split(':', 1)[1].strip()
                        current_question['options'] = [opt.strip() for opt in options.split(';')]
                        print(f"Варианты ответов: {current_question['options']}")
                        # Находим индекс правильного ответа
                        if current_question['type'] == 'multiple_choice':
                            try:
                                current_question['correct_option'] = current_question['options'].index(current_question['correct_answer'])
                                print(f"Индекс правильного ответа: {current_question['correct_option']}")
                            except ValueError:
                                current_question['correct_option'] = 0
                    elif line.startswith('Баллы:'):
                        current_question['points'] = float(line.split(':', 1)[1].strip())
                        print(f"Баллы: {current_question['points']}")
                    elif line.startswith('Время:'):
                        current_question['time_limit'] = int(line.split(':', 1)[1].strip())
                        print(f"Время: {current_question['time_limit']}")
        
        # Добавляем последний вопрос, если он есть и полностью заполнен
        if (current_question and self.current_round and
            current_question.get('text') and 
            current_question.get('type') and 
            current_question.get('correct_answer')):
            print(f"\nСохраняем последний вопрос: {current_question['text']}")
            self.current_round['questions'].append(current_question)
        
        print("\nИтоговая структура:")
        for round_data in self.rounds:
            print(f"\nРаунд: {round_data['title']}")
            print(f"Количество вопросов: {len(round_data['questions'])}")
            for q in round_data['questions']:
                print(f"- {q['text']} (Тип: {q['type']})")
        
        return self.quiz_title, self.quiz_description, self.rounds

    def parse_docx(self, file_path: str) -> Tuple[str, str, List[Dict]]:
        """Парсит .docx файл"""
        doc = Document(file_path)
        content = '\n'.join(paragraph.text for paragraph in doc.paragraphs)
        return self.parse_txt(content)

    def save_to_db(self, user_id: int) -> Quiz:
        """Сохраняет распарсенный квиз в базу данных"""
        quiz = Quiz(
            title=self.quiz_title,
            description=self.quiz_description,
            created_by=user_id
        )
        db.session.add(quiz)
        db.session.flush()  # Получаем ID квиза
        
        for round_idx, round_data in enumerate(self.rounds, 1):
            quiz_round = Round(
                quiz_id=quiz.id,
                title=round_data['title'],
                order=round_idx
            )
            db.session.add(quiz_round)
            db.session.flush()  # Получаем ID раунда
            
            for question_idx, question_data in enumerate(round_data['questions'], 1):
                question = Question(
                    round_id=quiz_round.id,
                    text=question_data['text'],
                    type=question_data['type'],
                    correct_answer=question_data['correct_answer'],
                    options=question_data['options'],
                    correct_option=question_data.get('correct_option'),
                    points=question_data['points'],
                    time_limit=question_data['time_limit'],
                    order=question_idx
                )
                db.session.add(question)
        
        db.session.commit()
        return quiz

def parse_quiz_file(file_path: str, file_type: str, user_id: int) -> Optional[Quiz]:
    """
    Парсит файл квиза и сохраняет его в базу данных
    
    Args:
        file_path (str): Путь к файлу
        file_type (str): Тип файла ('txt' или 'docx')
        user_id (int): ID пользователя, создающего квиз
    
    Returns:
        Quiz: Объект квиза или None в случае ошибки
    """
    try:
        parser = QuizParser()
        
        if file_type == 'txt':
            with open(file_path, 'r', encoding='utf-8') as f:
                parser.parse_txt(f.read())
        elif file_type == 'docx':
            parser.parse_docx(file_path)
        else:
            raise ValueError(f"Неподдерживаемый тип файла: {file_type}")
        
        return parser.save_to_db(user_id)
        
    except Exception as e:
        db.session.rollback()
        raise Exception(f"Ошибка при парсинге файла: {str(e)}")

def parse_quiz_content(content: str, user_id: int) -> Optional[Quiz]:
    """
    Парсит содержимое квиза и сохраняет его в базу данных
    
    Args:
        content (str): Содержимое файла
        user_id (int): ID пользователя, создающего квиз
    
    Returns:
        Quiz: Объект квиза или None в случае ошибки
    """
    try:
        parser = QuizParser()
        parser.parse_txt(content)
        return parser.save_to_db(user_id)
    except Exception as e:
        db.session.rollback()
        raise Exception(f"Ошибка при парсинге содержимого: {str(e)}") 