# Minimal container for the Jira reporting app.
#   docker build -t lifedata-jira-reports .
#   docker run -p 8000:8000 --env-file .env lifedata-jira-reports
FROM python:3.12-slim

WORKDIR /app

# Install deps first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# App code
COPY . .

EXPOSE 8000

# Read-only Jira creds are injected at runtime via --env-file / orchestrator secrets.
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8000", "app:app"]
