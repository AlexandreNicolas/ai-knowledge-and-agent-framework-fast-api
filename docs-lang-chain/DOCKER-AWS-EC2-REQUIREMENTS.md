# Docker, Make & AWS EC2 Deployment Requirements

This document details the requirements for containerizing the Ai Knowledge and Agent Framework backend with Docker, automating setup with Make, and deploying to AWS EC2.

---

## 1. Overview

| Component | Purpose |
|-----------|---------|
| **Docker** | Isolate dependencies, build, and run the NestJS app in production |
| **Make** | Automate installation and execution with a single command |
| **EC2** | Host the containerized application on AWS |

---

## 2. Docker Requirements

### 2.1 Build Process

1. **Base image**: Use `node:20-alpine` (or `node:20-slim`) for minimal footprint.

2. **Dependency installation**:
   - Command: `npm i --legacy-peer-deps`
   - Run as non-root user where possible.

3. **Build step**:
   - Command: `npm run build` (which runs `nest build`).
   - Output: `dist/` directory with compiled JavaScript.

4. **Production run**:
   - Command: `node dist/main`
   - Expose port: `8000` (default from `main.ts`).

### 2.2 Dockerfile Structure

```
Stage 1 (builder):
  - Copy package.json, package-lock.json
  - npm i --legacy-peer-deps
  - Copy source code
  - npm run build

Stage 2 (production):
  - Minimal base
  - Copy only dist/ and node_modules/production deps
  - CMD: node dist/main
```

### 2.3 Environment Variables

The app expects these at runtime (via `.env` or container env):

| Variable | Required | Description |
|----------|----------|-------------|
| `PORT` | No | HTTP port (default: 8000) |
| `CLIENT_URL` | Yes | CORS origin for frontend |
| `OPENAI_API_KEY` | Yes | OpenAI API key for LangChain / RAG |

---

## 3. Make Requirements

### 3.1 Targets

| Target | Description |
|--------|-------------|
| `make install` | Install Docker (if missing) and build the image |
| `make build` | Build the Docker image |
| `make run` | Run the container in production mode |
| `make up` | Build + run (default or convenience target) |
| `make stop` | Stop the running container |
| `make logs` | Show container logs |

### 3.2 Installation Logic

- **Docker**: If Docker is not installed, the Makefile should provide clear instructions or a script to install it (e.g. for Amazon Linux 2 or Ubuntu).
- **Automatic flow**: `make install` (or `make up`) should:
  1. Ensure Docker is available
  2. Run `docker build` with the correct build args
  3. Run `docker run` with appropriate env and port mapping

### 3.3 Makefile Variables

- `IMAGE_NAME`: e.g. `ai-knowledge-and-agent-framework`
- `IMAGE_TAG`: e.g. `latest` or `$(shell git rev-parse --short HEAD)`
- `CONTAINER_NAME`: e.g. `ai-knowledge-and-agent-framework`
- `PORT`: `8000` (host port mapped to container)

---

## 4. AWS EC2 Requirements

### 4.1 Instance

- **AMI**: Amazon Linux 2 or Ubuntu 22.04 LTS
- **Instance type**: Minimum `t3.micro`; recommended `t3.small` for production
- **Storage**: 8 GB minimum; 20 GB recommended

### 4.2 Pre-requisites on EC2

- Docker installed and running
- Make installed
- SSH access configured
- Security group allowing inbound traffic on port `8000` (or chosen port)

### 4.3 Network & Security

- **Inbound rules**:
  - Port 22 (SSH) for management
  - Port 8000 (or app port) for HTTP traffic
- **Outbound**: Default allow all (for npm, OpenAI API, etc.)

### 4.4 Environment on EC2

- Create `.env` file or use `docker run --env-file .env`
- Store secrets securely (e.g. AWS Secrets Manager, Parameter Store) for production

### 4.5 Deployment Flow on EC2

1. Clone repository (or copy build artifacts)
2. Copy `.env` (or inject env vars)
3. Run `make install` or `make up`
4. Optional: systemd service for auto-start on boot

---

## 5. Complete Command Reference

### Build & Run (Local / EC2)

```bash
# Install deps, build image, run container
make install   # or make up

# Manual Docker commands (if Make is not used)
docker build -t ai-knowledge-and-agent-framework .
docker run -p 8000:8000 --env-file .env ai-knowledge-and-agent-framework
```

### Production npm Scripts (inside container)

- Install: `npm i --legacy-peer-deps`
- Build: `npm run build` → `nest build`
- Start: `npm run start:prod` → `node dist/main`

---

## 6. File Checklist

| File | Purpose |
|------|---------|
| `Dockerfile` | Multi-stage build: install, build, run |
| `Makefile` | Targets: install, build, run, up, stop, logs |
| `.dockerignore` | Exclude `node_modules`, `.git`, `dist`, `.env*` |

---

## 7. Summary

1. **Docker**: Multi-stage Dockerfile → `npm i --legacy-peer-deps` → `npm run build` → `node dist/main`
2. **Make**: `make install` / `make up` automates Docker build and run
3. **EC2**: Install Docker + Make, clone repo, run `make up` with proper `.env`
