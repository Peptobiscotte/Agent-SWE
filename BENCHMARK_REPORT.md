# Model Benchmark Report

## 1. Setup

### 1.1 Benchmark objective

This benchmark compares multiple LLMs used with the same Agent Smith orchestration. The goal is to evaluate not only solve rate, but also exploration efficiency, token usage, provider reliability, latency, and iteration discipline.

### 1.2 Models and providers

At least **5 models** across at least **2 providers** must be compared.

| ID | Model | Provider |
|---|---|---|
| M1 | `gpt-oss-120b` | `groq` |
| M2 | `qwen3.8-27b` | `groq` |
| M3 | `nemotron-3-ultra-550b` | `openrouter` |
| M4 | `cohere/north-mini` | `openrouter` |
| M5 | `poolside/laguna` | `openrouter` |

### 1.3 SWE-bench tasks

The same set of at least **3 SWE-bench tasks** must be used for every model.

| ID | SWE-bench task | Why this task was selected |
|---|---|---|
| T1 | `sympy__sympy-14711` | `Easy sympy problem with short fix` |
| T2 | `sympy__sympy-18189` | `Harder sympy problem to compare with previous task` |
| T3 | `django__django-11066` | `Hardest problem in a different repository` |

### 1.4 Benchmark conditions

Keep the conditions below constant unless a variation is explicitly part of the ablation study.

- Agent/orchestrator version: `AgentSmithv1`
- Tool set: `SWE_BENCH_TOOLS`
- Maximum iterations: `30`
- Maximum output tokens per request: `1000`
- Context/history policy: `6 recent messages (3 iterations)`

---

## 2. Results

For every **model × task** combination, record pass/fail, iterations, total input tokens, total output tokens, and wall-clock time.

| Model | Provider | Task | Pass / Fail | Iterations | Input tokens | Output tokens | Wall-clock time (s) |
|---|---|---|---:|---:|---:|---:|---:|
| `gpt-oss-120b` | `groq` | `sympy-14711` | `PASS` | `5` | `15673` | `937` | `10.0` |
| `gpt-oss-120b` | `groq` | `sympy-18189` | `PASS` | `9` | `38383` | `1238` | `77.1` |
| `gpt-oss-120b` | `groq` | `django-11066` | `PASS` | `5` | `14784` | `326` | `29.9` |
| `qwen3.8-27b` | `groq` | `sympy-14711` | `PASS` | `13` | `36758` | `821` | `48.8` |
| `qwen3.8-27b` | `groq` | `sympy-18189` | `FAIL` | `6` | `22785` | `134` | `35.6` |
| `qwen3.8-27b` | `groq` | `django-11066` | `PASS` | `8` | `26414` | `337` | `18.6` |
| `nemotron-3-ultra-550b` | `openrouter` | `sympy-14711` | `FAIL` | `30` | `30452` | `548` | `43.4` |
| `nemotron-3-ultra-550b` | `openrouter` | `sympy-18189` | `FAIL` | `30` | `64819` | `2302` | `128.4` |
| `nemotron-3-ultra-550b` | `openrouter` | `django-11066` | `PASS` | `10` | `32669` | `866` | `65.8` |
| `cohere/north-mini` | `openrouter` | `sympy-14711` | `FAIL` | `30` | `78945` | `6523` | `88.4` |
| `cohere/north-mini` | `openrouter` | `sympy-18189` | `FAIL` | `1` | `2887` | `0` | `35.2` |
| `cohere/north-mini` | `openrouter` | `django-11066` | `FAIL` | `30` | `86703` | `7048` | `97.6` |
| `poolside/laguna` | `openrouter` | `sympy-14711` | `FAIL` | `30` | `84250` | `7845` | `89.9` |
| `poolside/laguna` | `openrouter` | `sympy-18189` | `FAIL` | `1` | `2478` | `0` | `15.5` |
| `poolside/laguna` | `openrouter` | `django-11066` | `FAIL` | `1` | `2645` | `0` | `21.2` |

### 2.1 Aggregate results

| Model | Tasks solved | Solve rate | Avg. iterations | Avg. input tokens | Avg. output tokens | Avg. wall-clock time (s) |
|---|---:|---:|---:|---:|---:|---:|
| `gpt-oss-120b` | `3/3` | `66%` | `6.3` | `22946` | `833` | `38.6` |
| `qwen3.8-27b` | `2/3` | `66%` | `9` | `24775` | `430` | `37.6` |
| `nemotron-3-ultra-550b` | `1/3` | `33%` | `24.3` | `42646` | `1238` | `78.8` |
| `cohere/north-mini` | `0/3` | `0%` | `30` | `55360` | `4523` | `73.6` |
| `poolside/laguna` | `0/3` | `0%` | `30` | `29791` | `2615` | `41.6` |

### 2.2 Results analysis

Basically groq provided models pass the tests better and in less time

---

## 3. Provider Reliability

For each model/provider pair, report average response time per request, retries caused by rate limits/timeouts/errors, and overall availability during the benchmark.

| Model | Provider | Avg. response time / request (ms) | Retries | Overall availability |
|---|---|---:|---:|---|
| `gpt-oss-120b` | `groq` | `8.5` | `0` | `HIGH` |
| `qwen3.8-27b` | `groq` | `5.9` | `0` | `HIGH` |
| `nemotron-3-ultra-550b` | `openrouter` | `4.2` | `0` | `MEDIUM` |
| `cohere/north-mini` | `openrouter` | `35.2` | `30` | `LOW` |
| `poolside/laguna` | `openrouter` | `15.5` | `30` | `LOW` |

### 3.1 Reliability analysis

Groq is more reliable overall, with higher limits on API calls per day even though lower limits on token per day, still with free plan it is very easy to reach limits

---

## 4. Intermediary Metrics

The subject requires **at least 2** intermediary metrics. This template includes all 3 suggested metrics.

### 4.1 Exploration efficiency

**Metric:** step at which the agent first reads or edits the file that appears in the final patch.

| Model | Task | First relevant file read/edit step |
|---|---|---:|
| `gpt-oss-120b` | `sympy-14711` | `1` |
| `gpt-oss-120b` | `sympy-18189` | `4` |
| `gpt-oss-120b` | `django-11066` | `2` |
| `qwen3.8-27b` | `sympy-14711` | `1` |
| `qwen3.8-27b` | `sympy-18189` | `4` |
| `qwen3.8-27b` | `django-11066` | `2` |
| `nemotron-3-ultra-550b` | `sympy-14711` | `N/A` |
| `nemotron-3-ultra-550b` | `sympy-18189` | `2` |
| `nemotron-3-ultra-550b` | `django-11066` | `N/A` |
| `cohere/north-mini` | `sympy-14711` | `N/A` |
| `cohere/north-mini` | `sympy-18189` | `N/A` |
| `cohere/north-mini` | `django-11066` | `N/A` |
| `poolside/laguna` | `sympy-14711` | `N/A` |
| `poolside/laguna` | `sympy-18189` | `N/A` |
| `poolside/laguna` | `django-11066` | `N/A` |


**Analysis:**  
Model locate relevant files pretty fast and accurately, some times delayed by running tests before searching the error


### 4.3 Submission discipline

**Metric:** iterations between the first successful test run and `final_answer`.

The ideal value is **0**.

| Model | Task | First passing test step | `final_answer` step | Extra iterations after first pass |
|---|---|---:|---:|---:|
| `gpt-oss-120b` | `sympy-14711` | `4` | `5` | `0` |
| `gpt-oss-120b` | `sympy-18189` | `8` | `9` | `0` |
| `gpt-oss-120b` | `django-11066` | `4` | `5` | `0` |
| `qwen3.8-27b` | `sympy-14711` | `12` | `13` | `0` |
| `qwen3.8-27b` | `sympy-18189` | `N/A` | `N/A` | `N/A` |
| `qwen3.8-27b` | `django-11066` | `7` | `8` | `0` |
| `nemotron-3-ultra-550b` | `sympy-14711` | `N/A` | `N/A` | `N/A` |
| `nemotron-3-ultra-550b` | `sympy-18189` | `N/A` | `N/A` | `N/A` |
| `nemotron-3-ultra-550b` | `django-11066` | `9` | `10` | `0` |
| `cohere/north-mini` | `sympy-14711` | `N/A` | `N/A` | `N/A` |
| `cohere/north-mini` | `sympy-18189` | `N/A` | `N/A` | `N/A` |
| `cohere/north-mini` | `django-11066` | `N/A` | `N/A` | `N/A` |
| `poolside/laguna` | `sympy-14711` | `N/A` | `N/A` | `N/A` |
| `poolside/laguna` | `sympy-18189` | `N/A` | `N/A` | `N/A` |
| `poolside/laguna` | `django-11066` | `N/A` | `N/A` | `N/A` |

**Analysis:**  
Current implementation guides the model to final_answer when tests results are all green

---

## 5. Ablation Study

At least one before/after comparison must use the **same model** on the **same tasks**, changing an agent element such as the prompt, tools, or parameters.

### 5.1 Change evaluated

- Model: `gpt-oss-120b`
- Provider: `groq`
- Tasks: `sympy-14711, sympy-18189, django-11066`
- Baseline configuration: `max_output_token=1000`
- Modified configuration: `max_output_token=500`
- Single intended change: `llm generation parameter`
- Hypothesis: `reduction of output tokens on a thinking model should decrease total output but can stop the model from generating complete structured code, expected more failure on extract and more steps`

### 5.2 Before / after results

| Task | Configuration | Pass / Fail | Iterations | Input tokens | Output tokens | Wall-clock time (s) | Relevant intermediary metric |
|---|---|---:|---:|---:|---:|---:|---|
| `sympy-14711` | Before | `PASS` | `5` | `15673` | `937` | `10.0` |
| `sympy-14711` | After | `PASS` | `8` | `24056` | `2535` | `16.8` |
| `sympy-18189` | Before | `PASS` | `9` | `38383` | `1238` | `77.1` |
| `sympy-18189` | After | `FAIL` | `6` | `27309` | `1677` | `26.9` |
| `django-11066` | Before | `PASS` | `5` | `14784` | `326` | `29.9` |
| `django-11066` | After | `PASS` | `5` | `14784` | `325` | `8.8` |

### 5.3 Ablation analysis

The change is dramatic on 18189, the program stops after an error due to gigantic context, on the other the token usage is higher but not significant

---

## 6. Conclusions

### 6.1 Model(s) selected for the final pipeline

**Selected model(s):** `gpt-oss-120b`

gpt-oss-120b is the only model passing all the tests, even though it cannot run on thinking off so it produces more output than a qwen for example

### 6.2 Models that can be disregarded

| Model | Decision | Data-based reason |
|---|---|---|
| `nemotron-3-ultra-550b` | `DISREGARD` | `Can't pass tests, free provider too limited` |
| `cohere/north-mini` | `DISREGARD` | `Can't pass tests, free provider too limited` |
| `poolside/laguna` | `DISREGARD` | `Can't pass tests, free provider too limited` |

### 6.3 Final observations

On practice because the subjects limits us to free providers, models using groq rank higher because token limits are harder to reach

---
