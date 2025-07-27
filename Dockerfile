FROM python:3.11-slim

WORKDIR /app

# Копируем файлы зависимостей
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код приложения
COPY . .

# Переменная для выбора, что запускать (web или bot)
ENV SERVICE_TYPE=web

# Запускаем нужный сервис
CMD if [ "$SERVICE_TYPE" = "web" ]; then \
        python wsgi.py; \
    else \
        python bot_runner.py; \
    fi 