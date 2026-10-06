# pyright: reportMissingTypeStubs = false
# pyright: reportUnknownArgumentType = false
# pyright: reportUnknownMemberType = false
# pyright: reportUnknownVariableType = false
# pyright: reportAttributeAccessIssue = false
# pyright: reportUnnecessaryIsInstance = false
from src.mbpp.mbpp_models import StepMetrics, SolutionOutput
from src.sandbox.sandbox_config import SandboxConfig
from src.mbpp.json_load_dump import JsonHandler
from src.sandbox.sandbox_model import Sandbox
from src.utils.api_loading import load_keys
from src.mcp.mcp_client import MCPClient
from multiprocessing import get_context
from src.utils.extract import Extractor
from src.mbpp.llm import LLM
from time import monotonic
import argparse
import asyncio
import sys


def main_wrapper():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-name", default=None)
    parser.add_argument("--provider-url", default=None)
    args = parser.parse_args()
    try:
        asyncio.run(main(
            args.task_file,
            args.output,
            args.model_name,
            args.provider_url
        ))
    except Exception as e:
        print(e)
        sys.exit(1)


async def main(
        task_file: str,
        output_file: str,
        model_name: str,
        provider_url: str
        ) -> None:

    # ================ DEFAULTS ================
    if not model_name or model_name == "":
        # model_name = "qwen/qwen3.8-27b"
        model_name = "openai/gpt-oss-120b"
    if not provider_url or provider_url == "":
        provider_url = "https://api.groq.com/openai/v1"

    # ================ METRICS ================

    agent_start = monotonic()
    total_input_token = 0
    total_output_token = 0
    total_requests = 0
    max_output_tokens = 1500
    res = ""
    steps: list[StepMetrics] = []
    # ================ TASK LOADING ================
    src_path = task_file
    handler = JsonHandler()
    task = handler.task_loader(src_path)
    # ================ MULTIPROCESS SETUP ================
    ctx = get_context("spawn")
    # ====== IPC
    #
    # the queue is used to send data from child process to parent
    # it will store the result of the exec function, be it a print,
    # an error or the finalAnswer signal.
    #
    queue = ctx.Queue()
    #
    # the pipe is here to assure bidirectionnal connections between the
    # child and the parent, here the child will send instructions for
    # tool calling to the parent, the parent receives it and sends it
    # to the MCP session which returns the result to the parent that
    # can then send it back to the child.
    #
    parent_conn, child_conn = ctx.Pipe()
    #
    # ================ MCP SERVER ================
    #
    client = MCPClient(None, None)
    await client.connect()
    await client.load_tools()
    manual = client.create_manual()
    tool_names = [tool.name for tool in client.tools]
    tool_format = {tool.name: tool.inputSchema for tool in client.tools}
    #
    # ================ LLM SETUP ================
    #
    api_key_list = load_keys(provider_url)
    llm = LLM(provider_url, api_key_list)
    #
    # ================ PROMPT ================
    #
    system_prompt = (
        "You are a coding agent solving an MBPP Python task.\n"
        "Output exactly one complete executable ```python``` "
        "block and nothing else.\n"
        "Use the exact function signature provided.\n\n"

        "The natural-language task definition is authoritative.\n"
        "Public tests are incomplete examples and may not cover all "
        "required behavior.\n"
        "Before writing the solution, account for every semantic "
        "requirement in the task definition, including qualifiers, "
        "ordering rules, equivalence rules, and edge cases implied "
        "by the wording.\n"
        "Solve the task generally, not only for the public tests.\n"
        "Be concise: no explanations, docstrings, comments, "
        "examples, or extra tests.\n\n"

        f"{manual}\n\n"

        "run_tests(...) and final_answer(...) already exist in the sandbox.\n"
        "Never define, override, mock, or reimplement them.\n"
        "Do not use XML or native API tool calling.\n"
        "Write calls to them as ordinary Python code.\n\n"

        "The agent automatically supplies the official "
        "test_imports and test_list when run_tests is executed.\n"
        "Do not pass, copy, modify, or invent tests yourself.\n"
        "Call run_tests only as run_tests(code=code).\n\n"

        "Workflow:\n"
        "1. Read the task definition carefully and identify all "
        "required behavior.\n"
        "2. Store a general candidate solution in a variable named code.\n"
        "3. Call result = run_tests(code=code).\n"
        "4. If tests fail, fix the candidate on the next turn.\n"
        "5. Passing tests only confirms the public cases; the "
        "candidate must still implement the full task definition.\n"
        "6. You may call final_answer(code=code) when tests pass, "
        "but the agent will also automatically submit a candidate "
        "that passes run_tests.\n\n"

        "Required response shape:\n"
        "```python\n"
        "code = 'def example(x):\\n    return x'\n"
        "run_tests(code=code)\n"
        "```\n"
    )

    if "gpt-oss-120b" in model_name:
        system_prompt += (
            "IMPORTANT: Do not use the model/API native "
            "tool-calling mechanism.\n"
            "run_tests and final_answer are Python "
            "function names that you must WRITE "
            "inside the output Python code; "
            "they are executed later by the sandbox.\n"
            "Your response itself must be plain text "
            "containing the Python code block.\n\n"
        )

    task_prompt = (
        f"Task definition:\n{task.task_definition}\n\n"
        f"Function definition:\n{task.function_definition}\n\n"
        # f"Official test imports:\n{task.test_imports!r}\n\n"
        # f"Official public tests:\n{task.test_list!r}\n"
    )
    messages = [
        {
            "role": "system",
            "content": system_prompt
        },
        {
            "role": "user",
            "content": task_prompt
        },
    ]
    #
    # ================ SANDBOX ================
    #
    config = SandboxConfig()
    sandbox = Sandbox(config)
    #
    #  ================ MAIN LOOP ================
    #
    try:
        for iterations in range(10):
            # ================ GENERATION ================
            remaining_output = max_output_tokens - total_output_token
            if remaining_output <= 0:
                print("MBPP output-token budget exhausted")
                break

            output_limit = min(500, remaining_output)

            # timing the request
            request_start = monotonic()
            # start the generation
            llm_res, retries = llm.generate(
                model_name,
                messages,
                output_limit
            )
            total_requests += 1 + retries
            # add the duration for metrics
            request_time = (monotonic() - request_start) * 1000
            # adding output to future message prompt
            # ================ EXTRACTION ================
            print("\n\nLLM RESPONSE:\n", llm_res.output_text, "\n\n")
            extractor = Extractor()
            code = extractor.extract_from_md(llm_res.output_text, tool_format)
            print("\nCODE EXTRACTED:\n", code)

            if not code:
                # All this if block is here to prevent repetitions of llm
                # output containing no executable code, we store the metric
                # and add a recovery message to our messages context for the
                # llm's next generation. In case the next gen fails again for
                # the same reason we dont add it again.
                observation = (
                    "NO_EXECUTABLE_CODE_EXTRACTED.\n"
                    "Nothing was executed.\n"
                    "Retry with exactly one SHORT "
                    "complete ```python``` block.\n"
                    "No explanations, docstrings, "
                    "comments, examples, or tests.\n"
                    "Use only a candidate code "
                    "variable and run_tests(code=code)."
                )

                if llm_res.usage is not None:
                    total_input_token += llm_res.usage.input_tokens
                    total_output_token += llm_res.usage.output_tokens

                    step = StepMetrics(
                        step=iterations + 1,
                        input_tokens=llm_res.usage.input_tokens,
                        output_tokens=llm_res.usage.output_tokens,
                        request_time_ms=request_time,
                        api_url=str(llm.client.base_url),
                        model_name=llm_res.model,
                        llm_output=llm_res.output_text,
                        sandbox_input="",
                        sandbox_output=observation,
                        retries=retries
                    )
                    steps.append(step)

                recovery_message = (
                    f"Iteration: {iterations + 1}/10\n"
                    "Type: extract_error\n"
                    f"Response: {observation}\n"
                )

                if not (
                    messages
                    and messages[-1]["role"] == "user"
                    and "Type: extract_error" in messages[-1]["content"]
                ):
                    messages.append({
                        "role": "user",
                        "content": recovery_message
                    })

                print(
                    f"ITERATION: {iterations + 1}",
                    "\nTYPE:\n extract_error",
                    "\nRESPONSE:\n",
                    observation
                )
                continue

            messages.append({
                "role": "assistant",
                "content": llm_res.output_text
            })

            # ================ SANDBOX PROCESS ================
            #
            # a child process is created here and aims at sandbox worker which
            # is the core method in the sandbox, it will try to exec
            # the code and return th result at the end

            passed_code: str | None = None
            passed_test_result = None
            run_tests_called = False
            last_run_tests_result = None

            process = ctx.Process(
                target=sandbox.worker,
                args=(code, queue, tool_names, child_conn))

            # start will start the execution of the target function, before
            # that, the process was ready but waiting.
            # we then start a timer

            process.start()
            time_start = monotonic()
            time_out = False

            # while the process is working, so while the code is running inside
            # our sandbox, we watch for data sent from the child to the parent.
            # Because we know the kind of data we wait for, we can call our
            # tools to be executed in our server if data is received.
            # Result of the tool call is then sent back to the child
            # (the sandbox). We also watch for execution timeout according
            # to our configuration

            while process.is_alive():
                # check every 0.05s if theres an object in the pipe
                if parent_conn.poll(0.05):
                    # get the instructions from the pipe and call adequate tool
                    tool_call_instructions = parent_conn.recv()
                    tool_name = tool_call_instructions["tool"]
                    tool_args = dict(tool_call_instructions["args"])

                    candidate_code = None

                    if tool_name == "run_tests":
                        run_tests_called = True
                        candidate_code = tool_args.get("code")
                        tool_args["test_imports"] = task.test_imports
                        tool_args["test_list"] = task.test_list

                    res_tool = await client.call_tool(
                        tool_name,
                        tool_args
                    )
                    print(f"STRUCTURED CONTENT:\n{res_tool}\n")

                    if res_tool.isError:
                        tool_result = res_tool.content[0].text
                        parent_conn.send({
                            "type": "tool_result",
                            "status": "error",
                            "result": tool_result
                        })
                    elif res_tool.structuredContent:
                        structured = res_tool.structuredContent
                        if (
                            isinstance(structured, dict)
                            and set(structured.keys()) == {"result"}
                        ):
                            tool_result = structured["result"]
                        else:
                            tool_result = structured

                        parent_conn.send({
                            "type": "tool_result",
                            "status": "ok",
                            "result": tool_result
                        })
                    else:
                        tool_result = res_tool.content[0].text
                        parent_conn.send({
                            "type": "tool_result",
                            "status": "ok",
                            "result": tool_result
                        })

                    if tool_name == "run_tests":
                        last_run_tests_result = tool_result

                        if (
                            candidate_code is not None
                            and isinstance(tool_result, dict)
                            and tool_result.get("status") == "ok"
                        ):
                            passed_code = candidate_code
                            passed_test_result = tool_result

                # kill the process if timeout
                if monotonic() - time_start >= (
                    config.max_execution_time_seconds
                ):
                    time_out = True
                    process.terminate()
                    process.join()
                    break

            process.join()
            # ================ METRICS ================
            # Extraction of the result with queue.get then send it back
            # to the LLM for next iteration

            if llm_res.usage is not None:
                total_input_token += llm_res.usage.input_tokens
                total_output_token += llm_res.usage.output_tokens

                print("STATUS:", llm_res.status)
                print("INCOMPLETE:", llm_res.incomplete_details)
                print("OUTPUT TOKENS:", llm_res.usage.output_tokens)
                print(
                    "REASONING TOKENS:",
                    llm_res.usage.output_tokens_details.reasoning_tokens
                )

                if time_out:
                    # pass timeout to llm
                    step = StepMetrics(
                            step=iterations+1,
                            input_tokens=llm_res.usage.input_tokens,
                            output_tokens=llm_res.usage.output_tokens,
                            request_time_ms=request_time,
                            api_url=str(llm.client.base_url),
                            model_name=llm_res.model,
                            llm_output=llm_res.output_text,
                            sandbox_input=code,
                            sandbox_output="timeout",
                            retries=retries
                        )
                    steps.append(step)
                    print("TIMEOUT")
                    messages.append({
                        "role": "user",
                        "content": (
                            f"Iteration: {iterations + 1}/10\n"
                            "Type: timeout\n"
                            "Response: Sandbox execution timed out.\n"
                        )
                    })
                    continue

                type, res, out = queue.get()

                final_code: str | None = None
                auto_finalized = False

                if type == "final":
                    final_code = str(res)
                    observation = str(res)
                elif passed_code is not None:
                    final_code = passed_code
                    auto_finalized = True
                    type = "final"
                    observation = (
                        "AUTO_FINALIZED_AFTER_OFFICIAL_TESTS_PASS.\n"
                        f"run_tests result: {passed_test_result}"
                    )
                    print(
                        "AUTO FINAL ANSWER: "
                        "candidate passed official MBPP tests"
                    )
                else:
                    if run_tests_called:
                        observation = (
                            "OFFICIAL TESTS EXECUTED:\n"
                            f"{task.test_list!r}\n\n"
                            "RESULT:\n"
                            f"{last_run_tests_result}"
                        )
                    else:
                        observation = str(res)
                        if type == "output" and res == "":
                            observation = (
                                "Code executed successfully with no output. "
                                "Call run_tests(code=code)."
                            )

                step = StepMetrics(
                        step=iterations+1,
                        input_tokens=llm_res.usage.input_tokens,
                        output_tokens=llm_res.usage.output_tokens,
                        request_time_ms=request_time,
                        api_url=str(llm.client.base_url),
                        model_name=llm_res.model,
                        llm_output=llm_res.output_text,
                        sandbox_input=code,
                        sandbox_output=str(observation + out),
                        retries=retries
                    )

                if final_code is not None:
                    if auto_finalized:
                        print(f"FINAL ANSWER GENERATED (AUTO):\n{final_code}")
                    else:
                        print(f"FINAL ANSWER GENERATED:\n{final_code}")

                    total_time = monotonic() - agent_start
                    steps.append(step)
                    solution = SolutionOutput(
                        task_id=str(task.task_id),
                        benchmark="mbpp",
                        success=True,
                        solution=final_code,
                        system_prompt=system_prompt,
                        iterations=iterations+1,
                        total_requests=total_requests,
                        total_input_tokens=total_input_token,
                        total_output_tokens=total_output_token,
                        total_time_seconds=total_time,
                        steps=steps
                    )
                    handler.solution_dump(solution, output_file)
                    break

                steps.append(step)
                messages.append({
                    "role": "user",
                    "content": (
                        f"Iteration: {iterations + 1}/10\n"
                        f"Type: {type}\n"
                        f"Response:\n{observation}\n"
                    )
                })
                # print("RESPONSE:\n", res, "TYPE:\n", type)
                # print("EXIT CODE:", process.exitcode)
            # ⚠️⚠️REMOVE AFTER TESTING⚠️⚠️
            # break

    # ================ CLEANING ================
    finally:
        print("TOTAL INPUT: ", total_input_token)
        print("TOTAL OUTPUT: ", total_output_token)
        await client.close()


if __name__ == "__main__":
    main_wrapper()
