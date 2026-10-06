VENV := /goinfre/$(USER)/agent-env/.venv

UV_ENV := UV_PROJECT_ENVIRONMENT=$(VENV)
# sandbox script
export PYTHONPATH="$(pwd)"

DOCKER_HOST := unix://$(XDG_RUNTIME_DIR)/podman/podman.sock
export DOCKER_HOST

PODMAN_USERNS := keep-id
export PODMAN_USERNS

api_test:
	$(UV_ENV) uv run python api_req.py

podman:
	@mkdir -p "$(XDG_RUNTIME_DIR)/podman"
	@if [ ! -S "$(XDG_RUNTIME_DIR)/podman/podman.sock" ]; then \
		podman system service --time=0 \
			unix://$(XDG_RUNTIME_DIR)/podman/podman.sock \
			>/tmp/agent-smith-podman.log 2>&1 & \
		sleep 1; \
	fi
	@echo "Podman API ready"

docker-image: podman
	@podman image exists python:3.11-slim || podman pull python:3.11-slim
	@echo "python:3.11-slim ready"

setup-containers: docker-image
	@echo "Container environment ready"

start_server_mbpp:
	$(UV_ENV) MCP_TRANSPORT=streamable-http uv run python mcp_tools_mbpp.py

start_server_swebench:
	$(UV_ENV) MCP_TRANSPORT=streamable-http uv run python mcp_tools_swebench.py

sandbox_serv:
	$(UV_ENV) uv run sandbox --mcp-server "http://localhost:8000/mcp"

sandbox_stdio_mbpp:
	$(UV_ENV) uv run sandbox --mcp-stdio "python mcp_tools_mbpp.py"

sandbox_stdio_swe:
	$(UV_ENV) uv run sandbox --mcp-stdio "python mcp_tools_swebench.py"

export_UV:
	export UV_PROJECT_ENVIRONMENT=/goinfre/$(USER)/agent-env/.venv

add-helper:
	$(UV_ENV) uv add

install: export_UV
	@mkdir -p /goinfre/$(USER)/agent-env
	$(UV_ENV) UV_LINK_MODE=copy uv sync --python 3.12

ext:
	$(UV_ENV) uv run python3 src/utils/extract.py

jedi:
	$(UV_ENV) uv run python3 jedi_script.py

version:
	$(UV_ENV) uv run which pydantic

run-mbpp:
	$(UV_ENV) uv run python -m agent_mbpp --task-file ../cache/mbpp_task.json --output ../cache/mbpp_solution.json --model-name "openai/gpt-oss-120b" --provider-url "https://api.groq.com/openai/v1"

run-swe-groq:
	$(UV_ENV) uv run python -m agent_swebench --task-file ../cache/sympy__sympy-14711.json --output ../cache/sympy__sympy-14711_solution.json --model-name "openai/gpt-oss-20b" --provider-url "https://api.groq.com/openai/v1"

run-swe-open:
	$(UV_ENV) uv run python -m agent_swebench --task-file ../cache/sympy__sympy-14711.json --output ../cache/sympy__sympy-14711-pool.json --model-name "poolside/laguna-s-2.1:free" --provider-url "https://openrouter.ai/api/v1"

task_mbpp:
	$(UV_ENV) uv run moulinette_eval dump mbpp --output ../cache/mbpp_task.json

validate_mbpp:
	$(UV_ENV) uv run moulinette_eval validate mbpp \
    ../cache/mbpp_task.json \
    ../cache/mbpp_solution.json

validate_swe:
	cd ../moulinette && \
	$(UV_ENV) uv run moulinette_eval validate swebench \
		../cache/django__django-11066.json \
		../cache/django__django-11066_solution_qwen.json

debug:
	$(UV_ENV) uv run python -m pdb -m src

clean:
	@find . -type d -name "__pycache__" -exec rm -rf {} +
	@rm -rf .mypy_cache
	@rm -rf .pytest_cache
	@rm -rf .ruff_cache
	@echo "Temporary files and caches removed."


lint:
	$(UV_ENV) uv run flake8 src
	$(UV_ENV) uv run mypy src \
		--warn-return-any \
		--warn-unused-ignores \
		--ignore-missing-imports \
		--disallow-untyped-defs \
		--check-untyped-defs


lint-strict:
	$(UV_ENV) uv run flake8 src
	$(UV_ENV) uv run mypy --strict src --ignore-missing-imports


activate:
	@echo "Run:"
	@echo "source $(VENV)/bin/activate"


deactivate:
	@echo "Run:"
	@echo "deactivate"


sync: install


.PHONY: install run debug clean lint lint-strict activate deactivate sync