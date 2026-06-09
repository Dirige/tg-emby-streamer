FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends     gcc     curl     && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir python-multipart

COPY . .

RUN mkdir -p /app/data /app/cache /app/strm /app/sing-box

EXPOSE 8001

CMD ["python", "run.py"]
