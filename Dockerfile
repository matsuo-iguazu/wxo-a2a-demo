FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agent_server.py .
COPY static/ static/
CMD ["sh", "-c", "uvicorn agent_server:app --host 0.0.0.0 --port ${PORT:-8080}"]
