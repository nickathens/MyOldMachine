#!/bin/bash
# MyOldMachine Setup Script
# Run this after cloning the repo to configure your bot.
# Works on macOS, Linux, and WSL.

set -e

# Cross-platform sed -i (macOS requires '' arg, Linux doesn't)
sedi() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "$@"
    else
        sed -i "$@"
    fi
}

# Write a key=value pair to .env safely (handles special chars in values)
set_env() {
    local key="$1"
    local value="$2"
    # Remove existing line and append new one
    grep -v "^${key}=" .env > .env.tmp || true
    echo "${key}=${value}" >> .env.tmp
    mv .env.tmp .env
}

echo "=== MyOldMachine Setup ==="
echo ""

# Check for .env
if [ -f .env ]; then
    echo ".env file already exists. Skipping setup."
    echo "Edit .env to change settings, then run: docker compose up -d"
    exit 0
fi

# Copy template
cp .env.example .env

# Create data directory (needed for Docker volume mount)
mkdir -p data

echo "Let's configure your bot."
echo ""

# Telegram token
echo "1. Telegram Bot Token"
echo "   Get one from @BotFather on Telegram."
read -p "   Paste your token: " TELEGRAM_TOKEN
if [ -n "$TELEGRAM_TOKEN" ]; then
    set_env "TELEGRAM_BOT_TOKEN" "$TELEGRAM_TOKEN"
fi

echo ""

# LLM Provider
echo "2. LLM Provider"
echo "   Options: claude, openai, gemini, ollama, openrouter"
echo "   (ollama = free, runs locally)"
read -p "   Provider [claude]: " LLM_PROVIDER
LLM_PROVIDER=${LLM_PROVIDER:-claude}
set_env "LLM_PROVIDER" "$LLM_PROVIDER"

# Model and API key based on provider
case $LLM_PROVIDER in
    claude)
        read -p "   Model [claude-sonnet-4-20250514]: " MODEL
        MODEL=${MODEL:-claude-sonnet-4-20250514}
        read -p "   Anthropic API key: " API_KEY
        ;;
    openai)
        read -p "   Model [gpt-4o]: " MODEL
        MODEL=${MODEL:-gpt-4o}
        read -p "   OpenAI API key: " API_KEY
        ;;
    gemini)
        read -p "   Model [gemini-2.0-flash]: " MODEL
        MODEL=${MODEL:-gemini-2.0-flash}
        read -p "   Google API key: " API_KEY
        ;;
    ollama)
        read -p "   Model [llama3.1:8b]: " MODEL
        MODEL=${MODEL:-llama3.1:8b}
        API_KEY=""
        echo "   Make sure Ollama is running: ollama serve"
        set_env "OLLAMA_BASE_URL" "http://host.docker.internal:11434"
        # Uncomment extra_hosts in docker-compose
        sedi 's|# extra_hosts:|extra_hosts:|' docker-compose.yml
        sedi 's|#   - "host.docker.internal:host-gateway"|  - "host.docker.internal:host-gateway"|' docker-compose.yml
        ;;
    openrouter)
        read -p "   Model [anthropic/claude-sonnet-4-20250514]: " MODEL
        MODEL=${MODEL:-anthropic/claude-sonnet-4-20250514}
        read -p "   OpenRouter API key: " API_KEY
        ;;
    *)
        echo "Unknown provider: $LLM_PROVIDER"
        exit 1
        ;;
esac

set_env "LLM_MODEL" "$MODEL"
if [ -n "$API_KEY" ]; then
    set_env "LLM_API_KEY" "$API_KEY"
fi

echo ""

# Allowed users
echo "3. Restrict access? (optional)"
echo "   Enter comma-separated Telegram user IDs, or leave empty for anyone."
echo "   (Send /start to @userinfobot on Telegram to find your ID)"
read -p "   Allowed users []: " ALLOWED
if [ -n "$ALLOWED" ]; then
    set_env "ALLOWED_USERS" "$ALLOWED"
fi

echo ""

# Bot name
read -p "4. Bot name [MyOldMachine]: " BOT_NAME
BOT_NAME=${BOT_NAME:-MyOldMachine}
set_env "BOT_NAME" "$BOT_NAME"

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Configuration saved to .env"
echo ""
echo "To start the bot:"
echo "  With Docker:    docker compose up -d"
echo "  Without Docker: pip install -r requirements.txt && python bot.py"
echo ""
echo "To view logs:     docker compose logs -f"
echo "To stop:          docker compose down"
