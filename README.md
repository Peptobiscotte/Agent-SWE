*This activity has been created as part of the 42 curriculum by mmonnier.*

# Agent Smith

## Description

Agent Smith is an autonomous coding-agent project built for the 42 curriculum. Its goal is to let a language model solve programming tasks through an iterative **Thought → Code → Observation** loop while keeping model-generated code inside a controlled execution environment.

The project targets two benchmarks:

- **MBPP (Mostly Basic Python Problems):** small Python programming tasks where the agent generates a complete function, runs the provided public tests, corrects the candidate when needed, and returns the final implementation.
- **SWE-bench:** real bug-fixing tasks in existing repositories. The agent explores a Dockerized repository, reads and searches code, edits files, runs the evaluation tests, and returns the final Git patch.

The project is not a simple "prompt in / answer out" wrapper. It combines:

- an LLM orchestration loop;
- a restricted Python sandbox;
- dynamic MCP tool discovery and execution;
- Docker-backed SWE-bench workspaces;
- structured execution feedback;
- token, time, iteration, and retry accounting;
- multi-provider/model benchmarking.

The final agent records each solve attempt as structured `StepMetrics` / `SolutionOutput` data so that the complete execution trace can be inspected and benchmarked.

---

## Main Features

- Autonomous MBPP and SWE-bench agents.
- Restricted subprocess-based sandbox.
- Configurable import, filesystem, timeout, and memory limits.
- MCP tool integration through both **stdio** and **streamable HTTP**.
- Dynamic tool discovery with `list_tools()`.
- MCP tools exposed inside generated Python code as normal function calls.
- Docker-backed SWE-bench repositories.
- Structured extraction of executable Python from model responses.
- Explicit feedback for extraction failures, execution failures, and timeouts.
- Persistent compact state for long SWE-bench runs.
- Public-test feedback and automatic finalization for MBPP.
- Rate-limit-aware API-key rotation.
- Per-step token, latency, retry, sandbox input/output, model, and provider logging.
- Model benchmarking across Groq and OpenRouter.

---

## System Architecture

The implementation is split into five main layers:

```text
                         +----------------------+
                         |       LLM API        |
                         | Groq / OpenRouter    |
                         +----------+-----------+
                                    |
                             model response
                                    |
                                    v
                         +----------------------+
                         |     Agent Loop       |
                         | MBPP / SWE-bench     |
                         +----------+-----------+
                                    |
                           extracted Python
                                    |
                                    v
                         +----------------------+
                         |       Sandbox        |
                         | restricted process   |
                         +----------+-----------+
                                    |
                            MCP proxy call
                                    |
                                    v
                         +----------------------+
                         |     MCP Client       |
                         | stdio / HTTP         |
                         +----------+-----------+
                                    |
                                    v
                +-------------------+-------------------+
                |                                       |
      +---------+----------+                  +---------+----------+
      |   MBPP MCP tools   |                  | SWE-bench MCP     |
      |     run_tests      |                  | tools + Docker    |
      +--------------------+                  +--------------------+
```

### Core components

`agent_mbpp/` contains the MBPP agent entry point and benchmark-specific orchestration.

`agent_swebench/` contains the SWE-bench agent. It starts the task Docker image, prepares the evaluation environment, connects the SWE-bench MCP tools to that container, and drives the iterative repair process.

`src/sandbox/` contains the restricted execution environment and the interactive sandbox CLI.

`src/mcp/` contains the generic MCP client. The client discovers tools dynamically with the MCP `list_tools()` operation and supports both stdio and streamable HTTP transports.

`src/utils/extract.py` converts model responses into executable sandbox actions. SWE-bench prefers executable code from the final model output and has a cautious reasoning fallback for a single unambiguous tool action. MBPP has benchmark-specific extraction/normalization because models may return a function directly instead of the expected `code = ...; run_tests(...)` wrapper.

`mcp_tools_mbpp.py` and `mcp_tools_swebench.py` provide the benchmark-specific tools exposed through MCP.

---

## Agent Loop

Both agents follow the same high-level loop:

```text
Task
  |
  v
Build prompt + available-tool documentation
  |
  v
LLM generation
  |
  v
Extract one executable Python action
  |
  +---- extraction failure ----> explicit observation ----+
  |                                                       |
  v                                                       |
Execute inside sandbox                                   |
  |                                                       |
  v                                                       |
Sandbox calls MCP proxy if a tool is needed              |
  |                                                       |
  v                                                       |
MCP server executes the real tool                        |
  |                                                       |
  v                                                       |
Observation returned to the model -----------------------+
  |
  v
Repeat until validated solution / iteration limit
```

Every model iteration is logged with:

- input token count;
- output token count;
- API request duration;
- provider URL;
- model name;
- raw LLM output;
- Python sent to the sandbox;
- sandbox/tool output;
- API retry count.

### MBPP loop

The MBPP agent receives:

- the natural-language task;
- the required function signature;
- the public tests.

The model writes a candidate implementation into a `code` variable and calls:

```python
run_tests(code=code)
```

The orchestrator supplies the official `test_imports` and `test_list` to the MCP tool, so the model cannot replace the test suite passed to the tool.

`run_tests` executes the candidate in a separate Python process. The current implementation returns structured information such as the first failing public assertion and, when possible, the actual and expected values.

When the official public tests pass, the agent automatically uses the tested candidate as the final MBPP solution instead of spending an additional model iteration only to submit it.

The MBPP context is intentionally compact because the benchmark has a very small cumulative input-token budget. Recent test feedback is kept concise and can include the previous failed candidate so the model can correct its last implementation without rebuilding the whole history.

### SWE-bench loop

For SWE-bench, the agent:

1. starts the task's Docker image;
2. identifies the container Python interpreter;
3. installs the helper scripts used by the tools;
4. installs `jedi` in the task environment;
5. starts the SWE-bench MCP server connected to the container;
6. dynamically discovers its tools;
7. asks the LLM to explore, edit, and test the repository;
8. accepts a final patch only after a successful test run.

The agent keeps the system prompt and task description, the six most recent conversation messages, and a compact persistent state containing useful facts from older iterations. Persistent facts include previously inspected file ranges, successful edits, located definitions, and the latest summarized test result.

This prevents the conversation from growing without bound while retaining the information needed for longer repository repairs.

A `final_answer(...)` call is rejected if the current repository state has not first been validated by a successful `run_tests()` call.

---

## Sandbox Design

The sandbox is the security boundary between LLM-generated Python and the host environment.

### Process isolation

Generated code is executed in a separate process created with the multiprocessing `spawn` context. The parent process remains responsible for orchestration and MCP communication.

The sandbox worker receives only:

- the generated Python code;
- the discovered MCP tool names;
- inter-process communication channels;
- the sandbox configuration.

### Restricted builtins

The executed namespace contains an explicit list of allowed builtins rather than the complete Python builtin namespace.

Sensitive operations are not exposed directly. In particular, `open` and `__import__` are replaced by controlled wrappers.

`final_answer` is injected directly by the sandbox. It raises a dedicated internal exception so final submission immediately stops the current sandbox execution and returns control to the orchestrator.

### Import allowlist

Imports are checked by the sandbox's custom `__import__` implementation. Only modules listed in `SandboxConfig.authorized_imports` are allowed.

The default configuration includes common algorithmic modules such as:

```text
math
collections
itertools
re
json
typing
functools
operator
heapq
bisect
copy
string
random
datetime
array
cmath
```

Modules used for process control, direct networking, or unrestricted operating-system access are not part of the default allowlist.

### Filesystem restrictions

The sandbox replaces `open()` with a path-checking wrapper. Paths are resolved before use and must remain under one of the configured `allowed_directories`.

The default writable/accessible roots are:

```text
/testbed
/tmp/agent
```

Path traversal outside these locations is rejected with `PermissionError`.

### Memory limits

The sandbox process applies an address-space limit with `resource.setrlimit(resource.RLIMIT_AS, ...)`.

The current default is:

```text
512 MB
```

### Execution timeout

The parent agent monitors the sandbox worker and terminates it if it runs longer than the configured execution time.

The default sandbox timeout is:

```text
30 seconds
```

The interactive CLI also preserves partial streamed output when an execution is interrupted.

### Sandbox configuration

A custom JSON configuration can override the defaults. Example:

```json
{
  "authorized_imports": [
    "math",
    "math.*",
    "collections",
    "collections.*",
    "itertools",
    "re",
    "json",
    "typing",
    "typing.*"
  ],
  "allowed_directories": [
    "/testbed",
    "/tmp/agent"
  ],
  "max_execution_time_seconds": 30,
  "max_memory_mb": 512
}
```

---

## MCP Integration

The project uses the Model Context Protocol as the boundary between sandboxed model code and privileged tools.

### Dynamic discovery

The MCP client does not hardcode the connected server's tool list. After connection it calls:

```python
tools = await session.list_tools()
```

and stores the returned tool definitions.

The sandbox receives these names and creates a proxy function for each one. A newly connected compliant MCP server can therefore expose tools without modifying the sandbox implementation.

For example, when connecting the provided `simple_mcp_server.py`, the sandbox dynamically discovers:

```text
add
multiply
echo
```

and exposes them as callable Python functions in the sandbox namespace.

### Tool-call flow

A sandbox-side proxy does not execute the privileged operation directly.

```text
sandbox function call
       |
       v
child process sends:
{tool, args}
       |
       v
parent MCP client
       |
       v
real MCP server
       |
       v
tool result
       |
       v
parent sends result back
       |
       v
sandbox function returns
```

This keeps MCP network/process access outside the generated-code process.

### Supported transports

The MCP client supports:

- **stdio**, using `stdio_client`;
- **streamable HTTP**, using `streamable_http_client`.

The interactive sandbox can therefore connect either to a local subprocess server or to an already-running HTTP MCP endpoint.

---

## Tool Implementation Details

### MBPP

The main MBPP tool is:

#### `run_tests(code=...)`

The model supplies only the candidate implementation. The orchestrator adds the official imports and public test list before calling the tool.

The candidate is run in a separate Python subprocess with a timeout. The structured result reports success or failure and gives diagnostic information for the first failing public test when available.

This tool is used both to give the model actionable feedback and to guard automatic final submission.

### SWE-bench filesystem tools

#### `read_file(filepath, start_line, end_line)`

Reads a selected line range and returns line-numbered output.

#### `edit_file(filepath, old_str, new_str)`

Performs an exact replacement. The old text must identify the intended location unambiguously. This makes edits deterministic and avoids unrestricted arbitrary shell editing.

#### `list_files(directory, pattern)`

Lists files matching a pattern inside the repository.

### SWE-bench code-search tools

#### `search_code(pattern, file_pattern)`

Performs grep-like code search and returns file/line matches.

#### `search_function_or_class_definition_in_code(name)`

Searches Python files for the requested function or class definition.

#### `find_references(name, filepath, line)`

Uses a Jedi helper script inside the repository environment to locate symbol references.

### SWE-bench execution tools

#### `run_tests()`

Runs the evaluation script supplied with the SWE-bench task and returns the relevant test output to the model.

#### `get_patch()`

Returns the repository changes as a unified Git diff using:

```bash
git -c core.fileMode=false diff
```

#### `run_command(command, workdir)`

Additional execution helper used for focused inspection or validation commands inside the task workspace.

---

## Instructions

### Requirements

- Python 3.10 or newer;
- `uv`;
- Docker with a running Docker daemon;
- provider API key(s) supplied through environment variables;
- network access for the host agent to contact the selected LLM provider.

The project uses OpenAI-compatible provider endpoints. Groq and OpenRouter were used during development and benchmarking.

API keys must be supplied through environment variables or a `.env` file. Do not hardcode keys in source code.

Typical variables include:

```bash
GROQ_API_KEY=...
OPENROUTER_API_KEY=...
```

### Installation

From the repository root:

```bash
uv sync
```

Make sure Docker is available:

```bash
docker info
```

---

## Running the Interactive Sandbox

### Default configuration

```bash
uv run sandbox
```

### Custom JSON configuration

```bash
uv run sandbox sandbox_template.json
```

### Connect MBPP tools through stdio

```bash
uv run sandbox \
  --mcp-stdio "python mcp_tools_mbpp.py" \
  sandbox_template.json
```

### Connect an MCP server through streamable HTTP

Start the server separately, then run:

```bash
uv run sandbox \
  --mcp-server "http://localhost:8000/mcp" \
  sandbox_template.json
```

### Connect SWE-bench tools

```bash
uv run sandbox \
  --mcp-stdio "python mcp_tools_swebench.py" \
  sandbox_template.json
```

### Dynamic-discovery test

With the review server:

```bash
uv run sandbox \
  --mcp-stdio "python simple_mcp_server.py --stdio"
```

Inside the sandbox:

```python
print(add(a=2, b=3))
print(multiply(a=4, b=5))
print(echo(message="hello"))
```

No `add`, `multiply`, or `echo` names are hardcoded in the sandbox; they are obtained from MCP discovery.

Type:

```text
exit
```

or press `Ctrl+D` to leave the interactive sandbox.

---

## Running the MBPP Agent

A task is provided as a JSON file and the result is written as a `SolutionOutput` JSON file.

Example with Groq:

```bash
uv run python -m agent_mbpp \
  --task-file path/to/mbpp_task.json \
  --output path/to/mbpp_solution.json \
  --model-name "openai/gpt-oss-120b" \
  --provider-url "https://api.groq.com/openai/v1"
```

The MBPP limits enforced by the evaluation are:

| Metric | Limit |
|---|---:|
| Maximum iterations | 10 |
| Cumulative input tokens | 6,000 |
| Cumulative output tokens | 1,500 |
| Total task timeout | 120 s |

Reasoning tokens count toward the output-token limit.

---

## Running the SWE-bench Agent

Example:

```bash
uv run python -m agent_swebench \
  --task-file path/to/swebench_task.json \
  --output path/to/swebench_solution.json \
  --model-name "openai/gpt-oss-120b" \
  --provider-url "https://api.groq.com/openai/v1"
```

The agent automatically creates and later removes the Docker container associated with the task.

The SWE-bench evaluation limits are:

| Metric | Limit |
|---|---:|
| Maximum iterations | 30 |
| Cumulative input tokens | 300,000 |
| Cumulative output tokens | 10,000 |
| Total task timeout | 900 s |

---

## Output and Logging

Both agents write the same general output structure.

A solution records:

- task identifier;
- benchmark name;
- success flag;
- final Python implementation or Git patch;
- system prompt;
- iterations;
- total API requests;
- total input/output tokens;
- total wall-clock time;
- per-step metrics.

A step records enough information to reproduce the agent trace:

```text
step
input_tokens
output_tokens
request_time_ms
api_url
model_name
llm_output
sandbox_input
sandbox_output
retries
```

For MBPP, `solution` contains the submitted Python function code.

For SWE-bench, `solution` contains the final unified Git patch.

---

## Benchmark Results and Analysis

The full benchmark is documented in [`BENCHMARK_REPORT.md`](./BENCHMARK_REPORT.md).

Five models from two providers were compared on the same three SWE-bench tasks:

- `sympy__sympy-14711`;
- `sympy__sympy-18189`;
- `django__django-11066`.

### Summary

| Model | Provider | Tasks solved | Solve rate |
|---|---|---:|---:|
| `gpt-oss-120b` | Groq | 3 / 3 | 100% |
| `qwen3.8-27b` | Groq | 2 / 3 | 66.7% |
| `nemotron-3-ultra-550b` | OpenRouter | 1 / 3 | 33.3% |
| `cohere/north-mini` | OpenRouter | 0 / 3 | 0% |
| `poolside/laguna` | OpenRouter | 0 / 3 | 0% |

### Main observations

`gpt-oss-120b` was the strongest model in the recorded SWE-bench comparison, solving all three benchmark tasks. It was therefore selected for the final SWE-bench pipeline.

`qwen3.8-27b` was more output-token efficient, but solved only two of the three tasks and was less reliable on the harder SymPy case.

The tested OpenRouter models produced substantially lower solve rates in this agent configuration. `nemotron-3-ultra-550b` solved one task, while `cohere/north-mini` and `poolside/laguna` solved none of the three.

The recorded benchmark also showed higher practical reliability from Groq for this workload. Free-tier rate limits remain important for both providers, so the LLM wrapper includes retry tracking and API-key rotation.

### Exploration efficiency

For successful GPT-OSS runs, the file that eventually appeared in the final patch was first identified very early:

| Task | First relevant read/edit step |
|---|---:|
| `sympy__sympy-14711` | 1 |
| `sympy__sympy-18189` | 4 |
| `django__django-11066` | 2 |

This suggests that the search/read tool set is usually sufficient to locate the relevant code without extensive repository exploration.

### Submission discipline

Successful benchmark runs reached final submission immediately after validation: there were no unnecessary reasoning iterations between the first confirmed passing test state and submission.

### Ablation

The benchmark report also compares GPT-OSS with a smaller per-request output budget. Reducing the maximum generation budget made structured output less reliable on some tasks and caused a failure on `sympy__sympy-18189`.

This is particularly important for reasoning models because reasoning tokens consume the same output budget as visible code. The final parameters therefore balance concise generations against leaving enough room for the model to produce a complete executable action.

---

## Project Structure

A simplified view of the repository:

```text
.
├── agent_mbpp/
│   └── __main__.py
├── agent_swebench/
│   └── __main__.py
├── src/
│   ├── mbpp/
│   ├── swebench/
│   ├── sandbox/
│   │   ├── sandbox_cli.py
│   │   ├── sandbox_config.py
│   │   └── sandbox_model.py
│   ├── mcp/
│   │   └── mcp_client.py
│   └── utils/
│       └── extract.py
├── mcp_tools_mbpp.py
├── mcp_tools_swebench.py
├── BENCHMARK_REPORT.md
└── README.md
```

---

## Technical Choices

### Why Python code as the model/action interface?

Instead of relying on provider-native tool calling, the model emits ordinary Python code. MCP tools are injected into the sandbox namespace as Python functions.

This keeps the agent loop provider-independent and makes the generated action visible in `sandbox_input` for evaluation and debugging.

### Why MCP?

MCP separates tool discovery/execution from the agent implementation. The sandbox can connect to an unknown compliant server, discover its tools, and expose them without benchmark-specific hardcoding.

### Why Docker for SWE-bench?

Each SWE-bench task targets a real repository with its own environment. The task-provided Docker image gives the agent an isolated, reproducible workspace while the host-side agent remains responsible for LLM calls and orchestration.

### Why short context plus persistent state?

Sending every previous step repeatedly increases token cost and can trigger provider rate limits. The SWE-bench agent therefore combines a short recent message window with compact persistent facts extracted from earlier tool calls.

For MBPP, the context is even smaller because the benchmark permits only 6,000 cumulative input tokens.

---

## Safety and Evaluation Constraints

The project is designed to solve the supplied task using only its task description, public tests, repository contents, and available tools.

It does not intentionally fetch public benchmark solutions, hidden tests, GitHub issues, pull requests, or gold patches.

Important evaluation thresholds:

- MBPP: at least **4 / 5** random tasks;
- SWE-bench: at least **2 / 3** random tasks;
- sandbox: all mandatory security/tool tests must pass.

API keys are loaded at runtime and must never be committed to the repository.

---

## Resources

### Technical references

- [Python documentation](https://docs.python.org/3/)
- [uv documentation](https://docs.astral.sh/uv/)
- [Docker documentation](https://docs.docker.com/)
- [Docker SDK for Python](https://docker-py.readthedocs.io/)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [SWE-bench](https://www.swebench.com/)
- [SWE-bench GitHub repository](https://github.com/SWE-bench/SWE-bench)
- Austin et al., *Program Synthesis with Large Language Models* (MBPP), [arXiv:2108.07732](https://arxiv.org/abs/2108.07732)
- [OpenAI Python SDK](https://github.com/openai/openai-python)
- [Groq API documentation](https://console.groq.com/docs)
- [OpenRouter documentation](https://openrouter.ai/docs)

### Use of AI during development

AI was used as a development assistant during this project for:

- interpreting and cross-checking project requirements;
- discussing sandbox and MCP architecture;
- debugging Python, multiprocessing, Docker, packaging, and MCP integration issues;
- analyzing agent execution logs;
- comparing model behavior and token usage;
- iterating on system prompts and context strategies;
- reviewing benchmark results;
- helping draft technical documentation, including this README.

The implementation was integrated, run, inspected, and benchmarked by the student. AI assistance was not used to fetch benchmark gold patches, hidden tests, or public task solutions for submission.

---

## Further Information

Detailed model/provider measurements, ablations, intermediary metrics, and conclusions are available in:

```text
BENCHMARK_REPORT.md
```

Per-task execution traces are stored in the generated `solution.json` files used to support the benchmark report.
