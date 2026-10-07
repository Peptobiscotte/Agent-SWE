# pyright: reportMissingTypeStubs = false
# pyright: reportUnknownArgumentType = false
# pyright: reportUnknownMemberType = false
# pyright: reportUnknownVariableType = false
# pyright: reportAttributeAccessIssue = false
# pyright: reportUnnecessaryIsInstance = false
from src.mbpp.mbpp_models import StepMetrics, SolutionOutput
from src.sandbox.sandbox_config import SandboxConfig
from src.mbpp.json_load_dump import JsonHandler
from src.mbpp.sys_prompt import mbpp_system_prompt
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


def main_wrapper() -> None:
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

    system_prompt = mbpp_system_prompt(manual)
    public_tests = "\n".join(
        task.test_imports + task.test_list
    )
    task_prompt = (
        f"Task definition:\n"
        f"{task.task_definition}\n\n"
        f"Required function signature:\n"
        f"{task.function_definition}\n\n"
        f"Public tests:\n"
        f"{public_tests}\n"
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

            # limiting the output and the context window
            output_limit = min(500, remaining_output)
            request_messages = messages[:2]
            if len(messages) > 2:
                request_messages.append(messages[-1])

            # timing the request
            request_start = monotonic()
            # start the generation
            llm_res, retries = llm.generate(
                model_name,
                request_messages,
                output_limit
            )
            total_requests += 1 + retries
            # add the duration for metrics
            request_time = (monotonic() - request_start) * 1000
            # adding output to future message prompt
            # ================ EXTRACTION ================
            print("\n\nLLM RESPONSE:\n", llm_res.output_text, "\n\n")
            extractor = Extractor()
            code = extractor.extract_mbpp_from_md(
                llm_res.output_text, tool_format)
            print("\nCODE EXTRACTED:\n", code)
            # ================ NO CODE MGMT ================
            if not code:
                # All this if block is here to prevent repetitions of llm
                # output containing no executable code, we store the metric
                # and add a recovery message to our messages context for the
                # llm's next generation. In case the next gen fails again for
                # the same reason we dont add it again.
                observation = (
                    "EXTRACTION_ERROR: No executable candidate was recovered. "
                    "Return one ```python``` block containing "
                    "code = <full function implementation> and "
                    "run_tests(code=code)."
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
            # ================ END NO CODE ================
            # append only after no code verification
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
            candidate_code: str | None = None

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
                        if (
                            isinstance(last_run_tests_result, dict)
                            and last_run_tests_result.get("status") == "error"
                        ):
                            observation = (
                                "PREVIOUS_CANDIDATE:\n"
                                f"{candidate_code}\n\n"
                                "TEST_FAILURE:\n"
                                f"test: {last_run_tests_result.get(
                                    'failed_test')}\n"
                                f"actual: {last_run_tests_result.get(
                                    'actual')}\n"
                                f"expected: {last_run_tests_result.get(
                                    'expected')}\n\n"
                                "The previous candidate failed. "
                                "Change the implementation or "
                                "semantic interpretation. "
                                "Do not repeat the same logic."
                            )
                        else:
                            observation = (
                                "RUN_TESTS_RESULT:\n"
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
