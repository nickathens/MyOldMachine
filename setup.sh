#!/bin/bash
# MyOldMachine Setup Script
# Run this after cloning the repo to configure your bot.

set -e

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

echo "Let's configure your bot."
echo ""

# Telegram token
echo "1. Telegram Bot Token"
echo "   Get one from @BotFather on Telegram."
read -p "   Paste your token: " TELEGRAM_TOKEN
if [ -n "$TELEGRAM_TOKEN" ]; then
    sed -i "s|TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=$TELEGRAM_TOKEN|" .env
fi

echo ""

# LLM Provider
echo "2. LLM Provider"
echo "   Options: claude, openai, gemini, ollama, openrouter"
echo "   (ollama = free, runs locally)"
read -p "   Provider [claude]: " LLM_PROVIDER
LLM_PROVIDER=${LLM_PROVIDER:-claude}
sed -i "s|LLM_PROVIDER=.*|LLM_PROVIDER=$LLM_PROVIDER|" .env

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
        sed -i "s|# OLLAMA_BASE_URL=.*|OLLAMA_BASE_URL=http://host.docker.internal:11434|" .env
        # Uncomment extra_hosts in docker-compose
        sed -i 's|# extra_hosts:|extra_hosts:|' docker-compose.yml
        sed -i 's|#   - "host.docker.internal:host-gateway"|  - "host.docker.internal:host-gateway"|' docker-compose.yml
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

sed -i "s|LLM_MODEL=.*|LLM_MODEL=$MODEL|" .env
sed -i "s|LLM_API_KEY=.*|LLM_API_KEY=$API_KEY|" .env

echo ""

# Allowed users
echo "3. Restrict access? (optional)"
echo "   Enter comma-separated Telegram user IDs, or leave empty for anyone."
echo "   (Send /start to @userinfobot on Telegram to find your ID)"
read -p "   Allowed users []: " ALLOWED
if [ -n "$ALLOWED" ]; then
    sed -i "s|ALLOWED_USERS=.*|ALLOWED_USERS=$ALLOWED|" .env
fi

echo ""

# Bot name
read -p "4. Bot name [MyOldMachine]: " BOT_NAME
BOT_NAME=${BOT_NAME:-MyOldMachine}
sed -i "s|BOT_NAME=.*|BOT_NAME=$BOT_NAME|" .env

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
