# pyright: reportMissingTypeStubs = false
# pyright: reportUnknownArgumentType = false
# pyright: reportUnknownMemberType = false
# pyright: reportUnknownVariableType = false
# pyright: reportAttributeAccessIssue = false
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
        model_name = "qwen/qwen3.8-27b"
    if not provider_url or provider_url == "":
        provider_url = "https://api.groq.com/openai/v1"

    # ================ METRICS ================

    agent_start = monotonic()
    total_input_token = 0
    total_output_token = 0
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
        "Output exactly one executable ```python``` block and nothing else.\n"
        "Use the exact function signature provided.\n"
        "Solve the task generally.\n\n"

        f"{manual}\n\n"

        "The functions run_tests(...) and final_answer(...) are already "
        "available in the sandbox.\n"
        "NEVER define, override, mock, or reimplement them.\n"
        "Do not use XML tags or native <tool_call> syntax.\n\n"

        "When calling run_tests, use EXACTLY the provided test_imports and "
        "test_list unchanged.\n"
        "Never add, remove, modify, or invent tests.\n"
        "The provided public tests are the only tests that run_tests should "
        "execute.\n"
        "Do not infer hidden tests or invent unsupported input types.\n\n"

        "Use this workflow:\n"
        "1. Put the candidate solution in a string variable named code.\n"
        "2. Call run_tests(...) using EXACTLY the provided public tests.\n"
        "3. Print the test result.\n"
        "4. If the tests pass, immediately call final_answer(code=code).\n"
        "5. If they fail, modify only the "
        "candidate solution, not the tests.\n\n"

        "Required response shape:\n"
        "```python\n"
        "code = 'def example(x):\\n    return x'\n"
        "result = run_tests(\n"
        f"    test_imports={task.test_imports!r},\n"
        f"    test_list={task.test_list!r},\n"
        "    code=code,\n"
        ")\n"
        "print(result)\n"
        "if result and result[0] == 'ok':\n"
        "    final_answer(code=code)\n"
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
        f"Function definition:\n{task.function_definition}"
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
            # timing the request
            request_start = monotonic()
            # start the generation
            llm_res, retries = llm.generate(model_name, messages, 500)
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
                    "Nothing was executed in the sandbox.\n"
                    "Retry using exactly one complete executable "
                    "Python code block.\n"
                    "Call run_tests once. If it returns 'ok', call "
                    "final_answer(code=code) in the same block."
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
                    f"Iteration: {iterations + 1}/30\n"
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
                # check every 0.05s if theres an objet in the pipe
                if parent_conn.poll(0.05):
                    # get the instructions from the pipe and call adequate tool
                    tool_call_instructions = parent_conn.recv()
                    if tool_call_instructions["tool"] == "run_tests":
                        tool_call_instructions[
                            "args"]["test_imports"] = task.test_imports
                        tool_call_instructions[
                            "args"]["test_list"] = task.test_list
                    res_tool = await client.call_tool(
                        tool_call_instructions["tool"],
                        tool_call_instructions["args"]
                        )
                    print(f"STRUCTURED CONTENT:\n{res_tool}\n")
                    # send formated output to child connection
                    if res_tool.isError:
                        parent_conn.send({
                            "type": "tool_result",
                            "status": "error",
                            "result": res_tool.content[0].text
                            })
                    elif res_tool.structuredContent:
                        parent_conn.send({
                            "type": "tool_result",
                            "status": "ok",
                            "result": res_tool.structuredContent["result"]
                            })
                # kill the process if timeout
                if monotonic() - time_start >= (
                    config.max_execution_time_seconds
                ):
                    time_out = True
                    process.terminate()
                    process.join()
                    break
            # ================ METRICS ================
            # Extraction of the result with queue.get then send it back
            # to the LLM for next iteration

            if llm_res.usage is not None:
                total_input_token += llm_res.usage.input_tokens
                total_output_token += llm_res.usage.output_tokens

                print("STATUS:", llm_res.status)
                print("INCOMPLETE:", llm_res.incomplete_details)

                if llm_res.usage:
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
                    print("TIMEOUT")
                else:
                    type, res = queue.get()
                    observation = res
                    if type == "output" and res == "":
                        observation = "Code executed successfully"
                        " with no output"
                    step = StepMetrics(
                            step=iterations+1,
                            input_tokens=llm_res.usage.input_tokens,
                            output_tokens=llm_res.usage.output_tokens,
                            request_time_ms=request_time,
                            api_url=str(llm.client.base_url),
                            model_name=llm_res.model,
                            llm_output=llm_res.output_text,
                            sandbox_input=code,
                            sandbox_output=res,
                            retries=retries
                        )
                    if type == "final":
                        print(f"FINAL ANSWER GENERATED:\n{res}")
                        total_time = monotonic() - agent_start
                        steps.append(step)
                        solution = SolutionOutput(
                            task_id=str(task.task_id),
                            benchmark="mbpp",
                            success=True,
                            solution=res,
                            system_prompt=system_prompt,
                            iterations=iterations+1,
                            total_requests=iterations+1,
                            total_input_tokens=total_input_token,
                            total_output_tokens=total_output_token,
                            total_time_seconds=total_time,
                            steps=steps
                        )
                        handler.solution_dump(solution, output_file)
                        break
                    messages.append({
                        "role": "user",
                        "content": f"Iteration: {iterations}\n"
                        f"Type: {type}\nResponse: {observation}\n"
                        })
                    # print("RESPONSE:\n", res, "TYPE:\n", type)
                    # print("EXIT CODE:", process.exitcode)
                steps.append(step)
            # ⚠️⚠️REMOVE AFTER TESTING⚠️⚠️
            # break

    # ================ CLEANING ================
    finally:
        print("TOTAL INPUT: ", total_input_token)
        print("TOTAL OUTPUT: ", total_output_token)
        await client.close()


if __name__ == "__main__":
    main_wrapper()
