FROM python:3.12-slim

# Install Docker CLI (for docker commit/save/load operations)
RUN apt-get update && apt-get install -y --no-install-recommends \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY . .

# Default config is mounted via volume — create a placeholder
RUN mkdir -p /backups /backups/restore_snapshot

EXPOSE 8000

# Default: start web server
# Override with CLI commands:  docker run ... python main.py backup
CMD ["python", "main.py", "web"]
