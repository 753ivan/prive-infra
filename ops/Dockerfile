FROM python:3.12-slim
WORKDIR /srv
# ffmpeg — кадры из видео-кружка для сверки лица; tesseract-ocr — код с селфи (тариф $29).
# Без них оба шага молча уходят на ручную проверку оператором.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg tesseract-ocr && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY scripts ./scripts
ENV TZ=UTC
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
