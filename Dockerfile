FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY run.py generate_session.py scan_history.py fix_strm.py check_db.py ./
COPY .env.example ./

# Create data directories
RUN mkdir -p /app/data /app/cache /app/strm /app/sing-box

EXPOSE 8001

CMD ["python", "run.py"]
