FROM python:3.12-slim

WORKDIR /app

# System deps: git for bash tool, bash itself for command execution
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    bash \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Ensure the spinner (\r-overwrite) works in the container terminal
ENV TERM=xterm-256color

# User's project directory is mounted here at runtime
VOLUME ["/project"]
WORKDIR /project

ENTRYPOINT ["python", "/app/main.py"]
