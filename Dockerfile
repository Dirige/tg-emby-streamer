FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libffi-dev \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY scripts/ ./scripts/
COPY run.py .

RUN mkdir -p /app/data

ENV CONFIG_PATH=/app/config.yaml

EXPOSE 8001

CMD ["python", "run.py"]
