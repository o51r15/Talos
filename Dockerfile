FROM python:3.12-slim
# No OS packages needed — the Python docker SDK communicates with the
# daemon via the socket directly and does not require the docker CLI binary.

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
