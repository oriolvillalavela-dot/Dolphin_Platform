FROM python:3.10-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libglib2.0-0 libsm6 libxrender1 libxext6 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY start.sh ./start.sh

# ensure LF line endings + executable bit (safe if already LF)
RUN sed -i 's/\r$//' /app/start.sh && chmod +x /app/start.sh

ENV PYTHONPATH=/app
ENV PORT=8011
EXPOSE 8011

CMD ["./start.sh"]


