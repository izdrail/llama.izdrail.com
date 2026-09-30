IMAGE_NAME := izdrail/llama.izdrail.com
LLAMA_CPP_CONTAINER := llama-cpp
LLAMA_CPP_PORT ?= 8080

.PHONY: build run stop restart logs status test test-health test-models validate clean

build:
	docker compose build llama-cpp

run:
	docker compose up -d llama-cpp

stop:
	docker compose down

restart: stop run

logs:
	docker compose logs -f

status:
	docker compose ps

validate:
	docker compose config --quiet

test: validate test-health

test-health:
	curl -fsS "http://localhost:$(LLAMA_CPP_PORT)/health"

test-models:
	curl -fsS "http://localhost:$(LLAMA_CPP_PORT)/v1/models"

clean: stop
	docker rmi $(IMAGE_NAME) || true
