# Argus

**Multi-agent coding assistant with self-auditing superpowers.**

Argus is a terminal-based AI coding assistant that doesn't just write code — it hunts bugs in its own output before you see it. Parallel specialist agents (Security, Bugs, Performance, Tests) audit every change, while a Challenger agent pokes holes in the plan before a single line is written.

## Quick Start

```bash
# Clone and configure
cp .env.example .env
# Add your ANTHROPIC_API_KEY to .env

# Run with Docker
docker compose up

# Or run directly
pip install -r requirements.txt
python main.py
```

## Usage

```
argus > fix the login endpoint to validate email format
argus > audit src/
argus > stats
argus > exit
```

## Architecture

```
User Input → Orchestrator
               ├── Coding Mode: Explorer → Challenger → Coder → Auto-Audit
               └── Audit Mode:  Explorer → [Security | Bugs | Perf | Tests] → Report
```

## Configuration

All settings in `argus.yaml`. API keys in `.env`.

## Demo

```bash
# Run Argus against the demo buggy app
python main.py
argus > audit demo/buggy_app
```
