.DEFAULT_GOAL := help
SHELL := /usr/bin/env bash

SCRIPT := scripts/build-unknown-voice.sh
BATS   := bats

.PHONY: help lint test check fixtures-exec

help: ## show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  %-14s %s\n", $$1, $$2}'

lint: ## shellcheck the bash script + stubs
	@command -v shellcheck >/dev/null 2>&1 || { echo "shellcheck missing: sudo apt install shellcheck"; exit 1; }
	shellcheck $(SCRIPT) tests/fixtures/sox tests/fixtures/ffmpeg

fixtures-exec: ## ensure stubs + script are executable
	chmod +x $(SCRIPT) tests/fixtures/sox tests/fixtures/ffmpeg

test: fixtures-exec ## run bats tests (stubbed sox/ffmpeg, no real audio needed)
	@command -v $(BATS) >/dev/null 2>&1 || { echo "bats missing: sudo apt install bats  (or npm i -g bats)"; exit 1; }
	$(BATS) tests/build-unknown-voice.bats

check: lint test ## lint + test
