FROM python:3.12-slim

WORKDIR /app

COPY src/ ./src/

ENV PORT=8080 \
    PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["python", "src/server.py"]