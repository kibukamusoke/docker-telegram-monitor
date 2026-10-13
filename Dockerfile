FROM python:3.13-alpine
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY detector.py config.json ./
CMD ["python", "-u", "/app/detector.py"]
