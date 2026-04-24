#!/usr/bin/env python3
"""
Docker Services - Spin up common services quickly.

Usage:
    python docker_service.py list                    # List running containers
    python docker_service.py start postgres          # Start PostgreSQL
    python docker_service.py start redis             # Start Redis
    python docker_service.py start mongo             # Start MongoDB
    python docker_service.py stop <container_name>   # Stop a container
    python docker_service.py logs <container_name>   # View logs
"""

import argparse
import subprocess
import sys

# Pre-configured services
SERVICES = {
    "postgres": {
        "image": "postgres:15-alpine",
        "name": "claude-postgres",
        "ports": {"5432": "5432"},
        "env": {
            "POSTGRES_USER": "claude",
            "POSTGRES_PASSWORD": "claude123",
            "POSTGRES_DB": "claude_db"
        },
        "volumes": {"claude_postgres_data": "/var/lib/postgresql/data"},
        "connection": "postgresql://claude:claude123@localhost:5432/claude_db"
    },
    "redis": {
        "image": "redis:7-alpine",
        "name": "claude-redis",
        "ports": {"6379": "6379"},
        "env": {},
        "volumes": {"claude_redis_data": "/data"},
        "connection": "redis://localhost:6379"
    },
    "mongo": {
        "image": "mongo:7",
        "name": "claude-mongo",
        "ports": {"27017": "27017"},
        "env": {
            "MONGO_INITDB_ROOT_USERNAME": "claude",
            "MONGO_INITDB_ROOT_PASSWORD": "claude123"
        },
        "volumes": {"claude_mongo_data": "/data/db"},
        "connection": "mongodb://claude:claude123@localhost:27017"
    },
    "mysql": {
        "image": "mysql:8",
        "name": "claude-mysql",
        "ports": {"3306": "3306"},
        "env": {
            "MYSQL_ROOT_PASSWORD": "claude123",
            "MYSQL_DATABASE": "claude_db",
            "MYSQL_USER": "claude",
            "MYSQL_PASSWORD": "claude123"
        },
        "volumes": {"claude_mysql_data": "/var/lib/mysql"},
        "connection": "mysql://claude:claude123@localhost:3306/claude_db"
    },
    "adminer": {
        "image": "adminer:4",
        "name": "claude-adminer",
        "ports": {"8080": "8080"},
        "env": {},
        "volumes": {},
        "connection": "http://localhost:8080"
    },
    "minio": {
        "image": "minio/minio",
        "name": "claude-minio",
        "ports": {"9000": "9000", "9001": "9001"},
        "env": {
            "MINIO_ROOT_USER": "claude",
            "MINIO_ROOT_PASSWORD": "claude123"
        },
        "volumes": {"claude_minio_data": "/data"},
        "command": "server /data --console-address ':9001'",
        "connection": "http://localhost:9000 (console: http://localhost:9001)"
    }
}


def run_docker(args: list, capture: bool = True) -> tuple:
    """Run docker command."""
    cmd = ["docker"] + args
    result = subprocess.run(cmd, capture_output=capture, text=True)
    return result.returncode, result.stdout, result.stderr


def list_containers() -> None:
    """List running containers."""
    code, stdout, stderr = run_docker(["ps", "--format", "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"])
    if code == 0:
        print(stdout)
    else:
        print(f"Error: {stderr}")


def start_service(service_name: str) -> dict:
    """Start a pre-configured service."""
    if service_name not in SERVICES:
        return {"error": f"Unknown service: {service_name}. Available: {', '.join(SERVICES.keys())}"}

    config = SERVICES[service_name]

    # Check if already running
    code, stdout, _ = run_docker(["ps", "-q", "-f", f"name={config['name']}"])
    if stdout.strip():
        return {"status": "already_running", "name": config['name'], "connection": config.get('connection')}

    # Build docker run command
    cmd = ["run", "-d", "--name", config["name"]]

    # Add ports
    for host_port, container_port in config.get("ports", {}).items():
        cmd.extend(["-p", f"{host_port}:{container_port}"])

    # Add environment variables
    for key, value in config.get("env", {}).items():
        cmd.extend(["-e", f"{key}={value}"])

    # Add volumes
    for volume, mount in config.get("volumes", {}).items():
        cmd.extend(["-v", f"{volume}:{mount}"])

    # Add image
    cmd.append(config["image"])

    # Add command if specified
    if "command" in config:
        cmd.extend(config["command"].split())

    code, stdout, stderr = run_docker(cmd)

    if code == 0:
        return {
            "status": "started",
            "name": config["name"],
            "container_id": stdout.strip()[:12],
            "connection": config.get("connection")
        }
    else:
        # Try to remove existing stopped container and retry
        run_docker(["rm", config["name"]])
        code, stdout, stderr = run_docker(cmd)
        if code == 0:
            return {
                "status": "started",
                "name": config["name"],
                "container_id": stdout.strip()[:12],
                "connection": config.get("connection")
            }
        return {"error": stderr}


def stop_service(container_name: str) -> dict:
    """Stop a container."""
    code, stdout, stderr = run_docker(["stop", container_name])
    if code == 0:
        return {"status": "stopped", "name": container_name}
    else:
        return {"error": stderr}


def get_logs(container_name: str, lines: int = 50) -> None:
    """Get container logs."""
    code, stdout, stderr = run_docker(["logs", "--tail", str(lines), container_name])
    if code == 0:
        print(stdout)
        if stderr:
            print(stderr)
    else:
        print(f"Error: {stderr}")


def main():
    parser = argparse.ArgumentParser(description="Manage Docker services")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # List command
    subparsers.add_parser("list", help="List running containers")

    # Start command
    start_parser = subparsers.add_parser("start", help="Start a service")
    start_parser.add_argument("service", choices=list(SERVICES.keys()), help="Service to start")

    # Stop command
    stop_parser = subparsers.add_parser("stop", help="Stop a container")
    stop_parser.add_argument("container", help="Container name to stop")

    # Logs command
    logs_parser = subparsers.add_parser("logs", help="View container logs")
    logs_parser.add_argument("container", help="Container name")
    logs_parser.add_argument("--lines", "-n", type=int, default=50, help="Number of lines")

    # Available command
    subparsers.add_parser("available", help="List available services")

    args = parser.parse_args()

    if args.command == "list":
        list_containers()

    elif args.command == "start":
        result = start_service(args.service)
        if "error" in result:
            print(f"Error: {result['error']}")
            return 1
        print(f"Service: {result['name']}")
        print(f"Status: {result['status']}")
        if result.get('connection'):
            print(f"Connection: {result['connection']}")

    elif args.command == "stop":
        result = stop_service(args.container)
        if "error" in result:
            print(f"Error: {result['error']}")
            return 1
        print(f"Stopped: {result['name']}")

    elif args.command == "logs":
        get_logs(args.container, args.lines)

    elif args.command == "available":
        print("Available services:")
        for name, config in SERVICES.items():
            print(f"  {name:12} - {config['image']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
