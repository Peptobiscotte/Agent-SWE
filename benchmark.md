# Model Benchmark Report

## 1. Setup

### 1.1 Benchmark objective

This benchmark compares multiple LLMs used with the same Agent Smith orchestration. The goal is to evaluate not only solve rate, but also exploration efficiency, token usage, provider reliability, latency, and iteration discipline.

### 1.2 Models and providers

At least **5 models** across at least **2 providers** must be compared.

| ID | Model | Provider |
|---|---|---|---|---|
| M1 | `gpt-oss-120b` | `groq` |
| M2 | `qwen3.8-27b` | `groq` |
| M3 | `nemotron-3-ultra-550b` | `openrouter` |
| M4 | `cohere/north-mini` | `openrouter` |
| M5 | `poolside/laguna` | `openrouter` |

### 1.3 SWE-bench tasks

The same set of at least **3 SWE-bench tasks** must be used for every model.

| ID | SWE-bench task | Why this task was selected |
|---|---|---|---|
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
| `nemotron-3-ultra-550b` | `openrouter` | `django-11066` | `PASS` | `13` | `32669` | `866` | `65.8` |
| `cohere/north-mini` | `openrouter` | `sympy-14711` | `FAIL` | `30` | `78945` | `6523` | `88.4` |
| `cohere/north-mini` | `openrouter` | `sympy-18189` | `FAIL` | `1` | `2887` | `0` | `35.2` |
| `cohere/north-mini` | `openrouter` | `django-11066` | `FAIL` | `30` | `86703` | `7048` | `97.6` |
| `poolside/laguna` | `openrouter` | `sympy-14711` | `FAIL` | `30` | `84250` | `7845` | `89.9` |
| `poolside/laguna` | `openrouter` | `sympy-18189` | `FAIL` | `1` | `2478` | `0` | `15.5` |
| `poolside/laguna` | `openrouter` | `django-11066` | `FAIL` | `1` | `2645` | `0` | `21.2` |

### 2.1 Aggregate results

| Model | Tasks solved | Solve rate | Avg. iterations | Avg. input tokens | Avg. output tokens | Avg. wall-clock time (s) |
|---|---:|---:|---:|---:|---:|---:|
| `gpt-oss-120b` | `<3/3>` | `<66%>` | `6.3` | `22946` | `833` | `38.6` |
| `qwen3.8-27b` | `<2/3>` | `<66%>` | `9` | `24775` | `430` | `37.6` |
| `nemotron-3-ultra-550b` | `<1/3>` | `<33%>` | `24.3` | `42646` | `1238` | `78.8` |
| `cohere/north-mini` | `<0/3>` | `<0%>` | `30` | `55360` | `4523` | `73.6` |
| `poolside/laguna` | `<0/3>` | `<0%>` | `30` | `29791` | `2615` | `41.6` |

### 2.2 Results analysis

`<Discuss the main differences between models. Do not rely only on raw solve rate. Compare iteration efficiency, token usage, wall-clock time, and consistency. Identify notable task-specific strengths or failures.>`

---

## 3. Provider Reliability

For each model/provider pair, report average response time per request, retries caused by rate limits/timeouts/errors, and overall availability during the benchmark.

| Model | Provider | Avg. response time / request (ms) | Retries | Overall availability |
|---|---|---:|---:|---:|---:|---:|---|
| `gpt-oss-120b` | `groq` | `8.5` | `0` | `HIGH` |
| `qwen3.8-27b` | `groq` | `5.9` | `0` | `HIGH` |
| `nemotron-3-ultra-550b` | `openrouter` | `4.2` | `0` | `MEDIUM` |
| `cohere/north-mini` | `openrouter` | `35.2` | `30` | `LOW` |
| `poolside/laguna` | `openrouter` | `15.5` | `30` | `LOW` |

### 3.1 Reliability analysis

`<Explain which providers were the most reliable in practice. Mention rate limits, quota exhaustion, 429/413/5xx errors, timeouts, long waits, or key rotation when relevant. Distinguish model quality from provider/API availability.>`

---

## 4. Intermediary Metrics

The subject requires **at least 2** intermediary metrics. This template includes all 3 suggested metrics.

### 4.1 Exploration efficiency

**Metric:** step at which the agent first reads or edits the file that appears in the final patch.

| Model | Task | First relevant file read/edit step |
|---|---|---:|---|---|
| `gpt-oss-120b` | `sympy-14711` | `1` |
| `gpt-oss-120b` | `sympy-18189` | `4` |
| `gpt-oss-120b` | `django-11066` | `2` |
| `qwen3.8-27b` | `sympy-14711` | `1` |
| `qwen3.8-27b` | `sympy-18189` | `4` |
| `qwen3.8-27b` | `django-11066` | `2` |


**Analysis:**  
`<Compare how directly models locate the relevant implementation. Mention unnecessary searches, repeated reads, premature edits, or strong use of issue evidence.>`

### 4.2 Partial progress

**Metric:** step at which test failures first decrease compared with the baseline.

Use `N/A` when no meaningful baseline test count is available or when the patch immediately makes all tests pass.

| Model | Task | Baseline failures | Step of first decrease | Failures after decrease | Notes |
|---|---|---:|---:|---:|---|
| `<MODEL_1>` | `sympy-14711` | `<N>` | `<N / N/A>` | `<N>` | `<NOTES>` |
| `<MODEL_1>` | `sympy-18189` | `<N>` | `<N / N/A>` | `<N>` | `<NOTES>` |
| `<MODEL_1>` | `django-11066` | `<N>` | `<N / N/A>` | `<N>` | `<NOTES>` |
| `<...>` | `<...>` | `<...>` | `<...>` | `<...>` | `<...>` |

**Analysis:**  
`<Discuss whether models make useful incremental progress or tend to alternate between hypotheses without improving tests.>`

### 4.3 Submission discipline

**Metric:** iterations between the first successful test run and `final_answer`.

The ideal value is **0**.

| Model | Task | First passing test step | `final_answer` step | Extra iterations after first pass |
|---|---|---:|---:|---:|
| `<MODEL_1>` | `sympy-14711` | `<N>` | `<N>` | `<N>` |
| `<MODEL_1>` | `sympy-18189` | `<N>` | `<N>` | `<N>` |
| `<MODEL_1>` | `django-11066` | `<N>` | `<N>` | `<N>` |
| `<...>` | `<...>` | `<...>` | `<...>` | `<...>` |

**Analysis:**  
`<Identify models that continue searching, reading, or testing after the task is already solved. Explain the effect on tokens, latency, and regression risk.>`

---

## 5. Ablation Study

At least one before/after comparison must use the **same model** on the **same tasks**, changing an agent element such as the prompt, tools, or parameters.

### 5.1 Change evaluated

- Model: `<MODEL>`
- Provider: `<PROVIDER>`
- Tasks: `sympy-14711, sympy-18189, django-11066`
- Baseline configuration: `<BASELINE_CONFIGURATION>`
- Modified configuration: `<MODIFIED_CONFIGURATION>`
- Single intended change: `<PROMPT / TOOL / PARAMETER CHANGE>`
- Hypothesis: `<EXPECTED_EFFECT>`

### 5.2 Before / after results

| Task | Configuration | Pass / Fail | Iterations | Input tokens | Output tokens | Wall-clock time (s) | Relevant intermediary metric |
|---|---|---:|---:|---:|---:|---:|---|
| `sympy-14711` | Before | `<PASS/FAIL>` | `<N>` | `<N>` | `<N>` | `<N.N>` | `<VALUE>` |
| `sympy-14711` | After | `<PASS/FAIL>` | `<N>` | `<N>` | `<N>` | `<N.N>` | `<VALUE>` |
| `sympy-18189` | Before | `<PASS/FAIL>` | `<N>` | `<N>` | `<N>` | `<N.N>` | `<VALUE>` |
| `sympy-18189` | After | `<PASS/FAIL>` | `<N>` | `<N>` | `<N>` | `<N.N>` | `<VALUE>` |
| `django-11066` | Before | `<PASS/FAIL>` | `<N>` | `<N>` | `<N>` | `<N.N>` | `<VALUE>` |
| `django-11066` | After | `<PASS/FAIL>` | `<N>` | `<N>` | `<N>` | `<N.N>` | `<VALUE>` |

### 5.3 Ablation analysis

`<Explain whether the change improved or degraded solve rate, efficiency, token usage, or reliability. Keep the conclusion tied to observed data and mention trade-offs.>`

---

## 6. Conclusions

### 6.1 Model(s) selected for the final pipeline

**Selected model(s):** `<MODEL(S)>`

`<Explain why these models were selected using the benchmark data: solve rate, iteration efficiency, token efficiency, latency, provider reliability, intermediary metrics, and trace behavior.>`

### 6.2 Models that can be disregarded

| Model | Decision | Data-based reason |
|---|---|---|
| `<MODEL>` | `<DISREGARD / BACKUP / KEEP>` | `<REASON SUPPORTED BY DATA>` |
| `<MODEL>` | `<DISREGARD / BACKUP / KEEP>` | `<REASON SUPPORTED BY DATA>` |
| `<MODEL>` | `<DISREGARD / BACKUP / KEEP>` | `<REASON SUPPORTED BY DATA>` |

### 6.3 Final observations

`<Summarize what mattered most in practice, based on the measurements above.>`

---

## 7. Backing Data

The backing `solution.json` files used for this report must be present in the repository.

Suggested organization:

```text
benchmark/
├── <MODEL_1>/
│   ├── <sympy-14711_solution.json
│   ├── sympy-18189_solution.json
│   └── django-11066_solution.json
├── <MODEL_2>/
│   └── ...
└── ablation/
    ├── before/
    └── after/
```

Actual location in this repository:

`<PATH_TO_BACKING_SOLUTION_JSON_FILES>`

### Completeness checklist

- [ ] At least 5 models were benchmarked.
- [ ] At least 2 providers were used.
- [ ] Every model was tested on the same set of at least 3 SWE-bench tasks.
- [ ] Every model × task row reports pass/fail, iterations, input tokens, output tokens, and wall-clock time.
- [ ] Provider reliability includes average response time, retries, and availability.
- [ ] At least 2 intermediary metrics are analyzed.
- [ ] At least 1 ablation compares before/after on the same tasks with the same model.
- [ ] Final model selection and discarded models are justified from the data.
- [ ] Backing `solution.json` files are included in the repository.
