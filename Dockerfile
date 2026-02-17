FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY static ./static
COPY db ./db
COPY items ./items
COPY config ./config
RUN apt-get update && apt-get install -y sqlite3 && rm -rf /var/lib/apt/lists/*

ENV DB_DIR=/data
VOLUME ["/data"]

CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8000"]
