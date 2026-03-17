# Docker Services

Quickly spin up databases and services using Docker.

## Usage

```bash
# List running containers
python skills/docker-services/scripts/docker_service.py list

# List available services
python skills/docker-services/scripts/docker_service.py available

# Start a service
python skills/docker-services/scripts/docker_service.py start postgres
python skills/docker-services/scripts/docker_service.py start redis
python skills/docker-services/scripts/docker_service.py start mongo

# Stop a service
python skills/docker-services/scripts/docker_service.py stop claude-postgres

# View logs
python skills/docker-services/scripts/docker_service.py logs claude-postgres
```

## Available Services

| Service | Image | Default Port | Connection String |
|---------|-------|--------------|-------------------|
| postgres | postgres:15-alpine | 5432 | postgresql://claude:claude123@localhost:5432/claude_db |
| redis | redis:7-alpine | 6379 | redis://localhost:6379 |
| mongo | mongo:7 | 27017 | mongodb://claude:claude123@localhost:27017 |
| mysql | mysql:8 | 3306 | mysql://claude:claude123@localhost:3306/claude_db |
| adminer | adminer:4 | 8080 | http://localhost:8080 |
| minio | minio/minio | 9000, 9001 | http://localhost:9000 |

## Notes

- Requires Docker installed and running
- Data persists in Docker volumes (survives container restarts)
- Default credentials are for development only
- All services use `claude-` prefix for container names
