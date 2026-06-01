# Docker, Make & AWS EC2 Deployment Requirements

This document details the requirements for containerizing the AI Knowledge and Agent Framework (FastAPI) with Docker, automating setup with Make, and deploying to AWS EC2.

---

## 1. Overview

| Component | Purpose |
|-----------|---------|
| **Docker** | Isolate dependencies, build, and run the FastAPI app in production |
| **Make** | Automate installation and execution with a single command |
| **EC2** | Host the containerized application on AWS |

---

## 2. Docker Requirements

### 2.1 Build Process

1. **Base image**: Use `python:3.12-slim` for a minimal, production-safe footprint.

2. **Dependency installation**:
   - Command: `pip install --no-cache-dir -r requirements.txt`
   - Run as a non-root user (`appuser`) for security.

3. **Run step**:
   - Command: `uvicorn src.main:app --host 0.0.0.0 --port 8000`
   - Expose port: `8000`.

### 2.2 Dockerfile Structure

```dockerfile
# Stage 1: builder — install deps into a clean layer
FROM python:3.12-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# Stage 2: production — copy only what's needed
FROM python:3.12-slim AS production

WORKDIR /app

# Non-root user
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY src/ ./src/

USER appuser

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

### 2.3 `.dockerignore`

```
.git
.venv
__pycache__
*.pyc
*.pyo
.pytest_cache
.env
.env.*
chroma_db/
*.egg-info
dist/
build/
```

### 2.4 Environment Variables

The app expects these at runtime (via `.env` or container env):

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key (`sk-ant-...`) |
| `PORT` | No | HTTP port (default: 8000) |
| `CLIENT_URL` | No | CORS origin for frontend (e.g. `https://myapp.com`) |
| `REDIS_URL` | No | Redis connection string for production memory store (e.g. `redis://localhost:6379`) |
| `VOYAGE_API_KEY` | No | Voyage AI key if using Voyage AI embeddings instead of sentence-transformers |

---

## 3. Make Requirements

### 3.1 Targets

| Target | Description |
|--------|-------------|
| `make install` | Install Docker (if missing) and build the image |
| `make build` | Build the Docker image |
| `make run` | Run the container in production mode |
| `make up` | Build + run (default convenience target) |
| `make stop` | Stop and remove the running container |
| `make logs` | Tail container logs |
| `make shell` | Open a shell in the running container |

### 3.2 Makefile Example

```makefile
IMAGE_NAME    := ai-knowledge-agent-framework
IMAGE_TAG     := latest
CONTAINER_NAME := ai-knowledge-agent-framework
PORT          := 8000

.PHONY: build run up stop logs shell install

build:
	docker build -t $(IMAGE_NAME):$(IMAGE_TAG) .

run:
	docker run -d \
		--name $(CONTAINER_NAME) \
		-p $(PORT):8000 \
		--env-file .env \
		--restart unless-stopped \
		$(IMAGE_NAME):$(IMAGE_TAG)

up: build run

stop:
	docker stop $(CONTAINER_NAME) || true
	docker rm $(CONTAINER_NAME) || true

logs:
	docker logs -f $(CONTAINER_NAME)

shell:
	docker exec -it $(CONTAINER_NAME) /bin/sh

install:
	@which docker > /dev/null 2>&1 || (echo "Docker not found. Install from https://docs.docker.com/get-docker/" && exit 1)
	$(MAKE) up
```

### 3.3 Makefile Variables

- `IMAGE_NAME`: e.g. `ai-knowledge-agent-framework`
- `IMAGE_TAG`: e.g. `latest` or `$(shell git rev-parse --short HEAD)` for versioned tags
- `CONTAINER_NAME`: matches `IMAGE_NAME` for easy management
- `PORT`: `8000` (host port mapped to container)

---

## 4. AWS EC2 Requirements

### 4.1 Instance

- **AMI**: Amazon Linux 2023 or Ubuntu 22.04 LTS
- **Instance type**: Minimum `t3.micro`; recommended `t3.small` for sentence-transformers (model loading needs ~400 MB RAM)
- **Storage**: 10 GB minimum; 20 GB recommended (sentence-transformers model cache)

### 4.2 Pre-requisites on EC2

- Docker installed and running
- Make installed
- SSH access configured
- Security group allowing inbound traffic on port `8000` (or your chosen port)

**Install Docker on Amazon Linux 2023:**
```bash
sudo yum update -y
sudo yum install -y docker make git
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker ec2-user
# Log out and back in for group change to take effect
```

**Install Docker on Ubuntu 22.04:**
```bash
sudo apt update && sudo apt install -y docker.io make git
sudo systemctl start docker && sudo systemctl enable docker
sudo usermod -aG docker ubuntu
```

### 4.3 Network & Security

- **Inbound rules**:
  - Port 22 (SSH) for management — restrict to your IP
  - Port 8000 (or app port) for HTTP traffic
  - Port 443 (HTTPS) if terminating TLS on the instance
- **Outbound**: Default allow all (for `pip`, Anthropic API, ChromaDB updates, etc.)

### 4.4 Environment on EC2

```bash
# Create .env on the instance
cat > .env << 'EOF'
ANTHROPIC_API_KEY=sk-ant-...
CLIENT_URL=https://yourfrontend.com
REDIS_URL=redis://your-redis-host:6379
EOF
chmod 600 .env
```

For production, use **AWS Secrets Manager** or **Parameter Store** instead of a plain `.env` file:

```bash
# Retrieve secret at container startup (add to Makefile run target)
docker run -e ANTHROPIC_API_KEY=$(aws secretsmanager get-secret-value \
  --secret-id anthropic-api-key --query SecretString --output text) ...
```

### 4.5 Deployment Flow on EC2

```bash
# 1. Clone repository
git clone https://github.com/your-org/ai-knowledge-agent-framework.git
cd ai-knowledge-agent-framework

# 2. Create .env
cp .env.example .env
nano .env  # fill in ANTHROPIC_API_KEY and other vars

# 3. Build and run
make up

# 4. Check it's running
make logs
curl http://localhost:8000/health
```

### 4.6 Auto-start on boot (optional)

Create a systemd service so Docker auto-starts the container after a reboot:

```ini
# /etc/systemd/system/ai-agent.service
[Unit]
Description=AI Knowledge Agent Framework
After=docker.service
Requires=docker.service

[Service]
WorkingDirectory=/home/ec2-user/ai-knowledge-agent-framework
ExecStart=/usr/bin/make run
ExecStop=/usr/bin/make stop
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable ai-agent
sudo systemctl start ai-agent
```

---

## 5. Python-specific considerations

### sentence-transformers model cache

The `all-MiniLM-L6-v2` model (~80 MB) is downloaded on first run. To avoid re-downloading on every container start, mount a volume or pre-download during the Docker build:

```dockerfile
# Pre-download the model during image build
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
```

Or mount a cache volume:
```bash
docker run -v sentence_transformers_cache:/root/.cache/huggingface ...
```

### ChromaDB persistence

If using `chromadb.PersistentClient`, mount the data directory so the index survives container restarts:

```bash
docker run -v $(pwd)/chroma_db:/app/chroma_db ...
```

Update the Makefile `run` target to include the volume flag.

### Workers

`uvicorn` is started with `--workers 1` in the Dockerfile. For CPU-bound workloads (e.g. many concurrent sentence-transformer encode calls), consider:

- `--workers 2` on `t3.small` (2 vCPUs)
- Or run `gunicorn -k uvicorn.workers.UvicornWorker` for process-level concurrency

For IO-bound workloads (calling the Anthropic API), a single async worker handles high concurrency well.

---

## 6. Complete Command Reference

### Build & Run (Local / EC2)

```bash
# Build image and run container
make up

# Manual Docker commands (if Make is not used)
docker build -t ai-knowledge-agent-framework .
docker run -p 8000:8000 --env-file .env ai-knowledge-agent-framework

# Dev (without Docker)
uvicorn src.main:app --reload --port 8000
```

### Python Scripts (inside container)

```bash
# Install deps
pip install -r requirements.txt

# Run dev server
uvicorn src.main:app --reload

# Run tests
pytest

# Trigger RAG index (if exposed as a CLI or endpoint)
curl -X POST http://localhost:8000/knowledge/index -d '{"url": "https://cheesecakelabs.com"}'
```

---

## 7. File Checklist

| File | Purpose |
|------|---------|
| `Dockerfile` | Multi-stage build: install deps, copy src, run uvicorn |
| `Makefile` | Targets: build, run, up, stop, logs, shell, install |
| `.dockerignore` | Exclude `.venv`, `.git`, `__pycache__`, `.env*`, `chroma_db/` |
| `requirements.txt` | Pinned Python dependencies |
| `.env.example` | Template with all required variable names (no values) |

---

## 8. Summary

1. **Docker**: Multi-stage Dockerfile (`python:3.12-slim`) → `pip install -r requirements.txt` → `uvicorn src.main:app`
2. **Make**: `make up` automates Docker build and run with env injection
3. **EC2**: Install Docker + Make, clone repo, create `.env`, run `make up`
4. **Sentence-transformers**: Pre-download the model in the image or mount a cache volume
5. **ChromaDB persistence**: Mount `./chroma_db` as a volume if using `PersistentClient`
