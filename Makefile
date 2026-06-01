IMAGE_NAME    := ai-knowledge-agent-framework
IMAGE_TAG     := latest
CONTAINER_NAME := ai-knowledge-agent-framework
PORT          := 8000

.PHONY: build run up stop logs shell install dev

dev:
	uvicorn src.main:app --reload --port $(PORT)

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
	@which docker > /dev/null 2>&1 || (echo "Docker not found. See https://docs.docker.com/get-docker/" && exit 1)
	$(MAKE) up
