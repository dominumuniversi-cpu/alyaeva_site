FROM python:3.12-slim

WORKDIR /app

# Зависимости отдельным слоем — пересборка кода не тащит повторную установку
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Пользователь без прав root
RUN useradd -m -u 10001 appuser && mkdir -p /data/uploads && chown -R appuser /data
USER appuser

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
