FROM python:3.12-slim

# System dependencies for common skills
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    sox \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY . .

# Create data directory
RUN mkdir -p /app/data

# Run
CMD ["python", "bot.py"]
