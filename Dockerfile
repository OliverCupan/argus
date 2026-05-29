FROM python:3.12-slim

WORKDIR /app

# Install system deps for bash tool execution
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Project directory mounted at /project
VOLUME ["/project"]
WORKDIR /project

ENTRYPOINT ["python", "/app/main.py"]
