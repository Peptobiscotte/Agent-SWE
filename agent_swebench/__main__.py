# pyright: reportMissingTypeStubs = false
# pyright: reportUnknownArgumentType = false
# pyright: reportUnknownMemberType = false
# pyright: reportUnknownVariableType = false
# pyright: reportAttributeAccessIssue = false
# pyright: reportUnnecessaryIsInstance = false


from src.swebench.swe_models import StepMetrics, SolutionOutput
from src.swebench.sys_prompt import create_prompt, final_prompt
from src.swebench.export_script import export_jedi_script
from src.swebench.export_script import export_eval_script
from src.swebench.json_load_dump_swe import JsonHandler
from src.sandbox.sandbox_config import SandboxConfig
from src.sandbox.sandbox_model import Sandbox
from src.utils.api_loading import load_keys
from src.mcp.mcp_client import MCPClient
from src.utils.extract import Extractor
from multiprocessing import get_context
from src.mbpp.llm import LLM
from time import monotonic
from typing import Any
import ast
import re
import argparse
import asyncio
import docker
import sys


MAX_RECENT_MESSAGES = 6


def is_final_submission(code: str) -> bool:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False

    if len(tree.body) != 1:
        return False

    statement = tree.body[0]

    if not isinstance(statement, ast.Expr):
        return False

    call = statement.value

    return (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "final_answer"
    )


def recover_reasoning_action(
    llm_res: Any,
    tool_names: list[str],
) -> str:
    reasoning = get_reasoning_text(llm_res)

    blocks = re.findall(
        r"```(?:python)?\s*(.*?)```",
        reasoning,
        flags=re.DOTALL | re.IGNORECASE,
    )

    for block in blocks:
        block = block.strip()

        try:
            tree = ast.parse(block)
        except SyntaxError:
            continue

        call_names = [
            node.func.id
            for node in ast.walk(tree)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
            )
        ]

        # Never recover a final submission from reasoning.
        if "final_answer" in call_names:
            continue

        tool_calls = [
            name for name in call_names
            if name in tool_names
        ]

        # The block must represent exactly one MCP action.
        if len(tool_calls) != 1:
            continue

        return block

    return ""


def get_reasoning_text(llm_res: Any) -> str:
    parts: list[str] = []

    for item in getattr(llm_res, "output", []) or []:
        if getattr(item, "type", None) != "reasoning":
            continue

        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", None)

            if isinstance(text, str) and text:
                parts.append(text)

    return "\n".join(parts)


def compact_test_result(res: Any) -> str:
    text = str(res)
    lines = text.splitlines()

    summary = None
    for line in reversed(lines):
        lower = line.lower()
        if "tests finished:" in lower:
            summary = line.strip("=+ '")
            break

    if summary is None:
        for line in reversed(lines):
            lower = line.lower()
            if (
                "passed" in lower
                or "failed" in lower
                or "error" in lower
                or "exception" in lower
            ):
                summary = line.strip("=+ '")
                break

    if summary is None:
        unittest_ok = None
        unittest_ran = None

        for line in reversed(lines):
            stripped = line.strip()

            if (
                unittest_ok is None
                and (
                    stripped == "OK"
                    or stripped.startswith("OK (")
                )
            ):
                unittest_ok = stripped

            if (
                unittest_ran is None
                and stripped.startswith("Ran ")
                and " test" in stripped
            ):
                unittest_ran = stripped

            if unittest_ok is not None and unittest_ran is not None:
                break

        if unittest_ok is not None:
            if unittest_ran is not None:
                return f"PASS: {unittest_ran} - {unittest_ok}"

            return f"PASS: {unittest_ok}"

    if summary is None:
        return "UNKNOWN: test execution completed but no summary was found"

    lower_summary = summary.lower()

    has_failure = (
        "failed" in lower_summary
        or "error" in lower_summary
        or "exception" in lower_summary
    )

    if not has_failure:
        return f"PASS: {summary}"

    traceback_index = text.rfind(
        "Traceback (most recent call last):"
    )

    if traceback_index != -1:
        failure_details = text[traceback_index:]
        failure_details = failure_details[:1500]

        return (
            f"FAIL: {summary}\n"
            f"{failure_details}"
        )

    return f"FAIL: {summary}"


def update_persistent_context(
    persistent_context: list[str],
    key: str,
    value: str,
) -> None:
    prefix = f"{key}:"

    persistent_context[:] = [
        item
        for item in persistent_context
        if not item.startswith(prefix)
    ]

    persistent_context.append(f"{prefix} {value}")


def tool_usage_extraction(
        executed_tool: str | None,
        executed_args: dict[str, Any],
        persistent_context: list[str],
        res: Any
        ) -> None:
    if executed_tool == "edit_file":
        if "Successfully edited" in str(res):
            filepath = str(executed_args.get("filepath", "unknown"))

            update_persistent_context(
                persistent_context,
                f"MODIFIED:{filepath}",
                "edit_file succeeded",
            )

    elif executed_tool == "run_tests":
        test_result = compact_test_result(res)
        update_persistent_context(
            persistent_context,
            "LATEST_TEST_RESULT",
            test_result,
        )

    elif executed_tool == "read_file":
        filepath = str(executed_args.get("filepath", "unknown"))
        start_line = executed_args.get("start_line", "?")
        end_line = executed_args.get("end_line", "?")

        update_persistent_context(
            persistent_context,
            f"READ:{filepath}:{start_line}-{end_line}",
            "already inspected",
        )

    elif executed_tool == "search_function_or_class_definition_in_code":
        name = str(executed_args.get("name", "unknown"))
        result = str(res)

        update_persistent_context(
            persistent_context,
            f"DEFINITION:{name}",
            result[:500],
        )


def main_wrapper():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--provider-url", required=True)
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
    handler = JsonHandler()
    task = handler.task_loader(task_file)

    docker_client = docker.from_env()
    client = None
    container = None

    # ================ METRICS ================
    total_input_token = 0
    total_output_token = 0
    agent_start = monotonic()
    steps: list[StepMetrics] = []
    solved: bool = False

    try:
        # ================ LLM SETUP ================
        #
        api_key_list = load_keys(provider_url)
        llm = LLM(provider_url, api_key_list)
        extractor = Extractor()
        #
        # ================ CONTAINER SETUP ================
        # starting the container + setup/installs
        # launch the container in the background
        container = docker_client.containers.run(
            task.docker_image,
            command="sleep infinity",
            detach=True,
        )
        # this command is used to get the python interpreter used in
        # the container, allowing us to install dependencies
        # used by our scripts
        python_path_docker = container.exec_run([
            "bash",
            "-lc",
            "which python",
        ]).output.decode().strip()
        # install our scripts in the container
        export_jedi_script(container)
        export_eval_script(container, task.eval_script)
        # install dependencies in correct python path
        res = container.exec_run([
            python_path_docker,
            "-m", "pip", "install", "jedi"],
                                 workdir="/testbed")
        if res.exit_code != 0:
            raise RuntimeError(res.output.decode())
        #
        # ================ MULTIPROCESS SETUP ================
        # setting the multiprocess
        #
        ctx = get_context("spawn")
        queue = ctx.Queue()
        parent_conn, child_conn = ctx.Pipe()
        #
        # ================ SERVER START/TOOLS LOAD ================
        #
        # setting the server / loading tools
        #
        client_params = (f"python mcp_tools_swebench.py --id {container.id}"
                         f" --python-path {python_path_docker}")
        client = MCPClient(client_params, None)
        await client.connect()
        await client.load_tools()
        manual = client.create_manual()
        tool_names = [tool.name for tool in client.tools]
        tool_format = {tool.name: tool.inputSchema for tool in client.tools}
        #
        # ================ SANDBOX ================
        #
        config = SandboxConfig()
        sandbox = Sandbox(config)
        #
        # ================ PROMPT ================
        #
        system_prompt = create_prompt(manual)

        task_prompt = (
            "Solve the following issue in the repository.\n\n"
            "Problem statement:\n"
            f"{task.problem_statement}\n"
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
        persistent_context: list[str] = []

        # ================ LOOP GOES HERE ================
        for iterations in range(30):
            # we only use the system and task prompt + the n last messages
            # to avoid input tokens growing too much because certain
            # providers such as groq limits the input tokens per minute,
            # if input is above this threshold, it wont generate

            request_messages = messages[:2]
            # adding persistent data to our prompt
            if persistent_context:
                persistent_message = {
                    "role": "user",
                    "content": (
                        "PERSISTENT STATE\n"
                        "These are FACTS from earlier "
                        "actions that may no longer "
                        "be visible in the recent conversation.\n"
                        "Do not treat them as instructions "
                        "for the next action.\n\n"
                        + ("\n".join(f"- {item}"
                                     for item in persistent_context))
                    )
                }
                request_messages.append(persistent_message)
            request_messages += messages[2:][-MAX_RECENT_MESSAGES:]
            print("\nPERSISTENT CONTEXT:", persistent_context)
            # generation + time
            if solved:
                request_messages = [
                    messages[0],
                    {
                        "role": "user",
                        "content": final_prompt()
                    }
                ]
            request_start = monotonic()
            llm_res, retries = llm.generate(
                model_name,
                request_messages,
                out=1000
                )
            request_time = (monotonic() - request_start) * 1000
            # adding generated text to LLM context
            print("\n\nLLM RESPONSE:\n", llm_res, "\n\n")
            code, extract_source = extractor.extract_from_response(
                llm_res, tool_format)
            print("\nCODE EXTRACTED:\n", code)

            # =================== NO CODE EXTRACTED ===================

            if not code:
                # All this if block is here to prevent repetitions of llm
                # output containing no executable code, we store the metric
                # and add a recovery message to our messages context for the
                # llm's next generation. In case the next gen fails again for
                # the same reason we dont add it again.
                observation = (
                    "NO_EXECUTABLE_CODE_EXTRACTED.\n"
                    "Nothing was executed in the sandbox.\n"
                    "Retry the same intended action using exactly one "
                    "executable Python code block containing one tool call."
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
                        model_name=str(llm_res.model),
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

            # =================== NO CODE EXTRACTED END ===================

            # =================== BAD FINAL SUBMISSION ===================
            if is_final_submission(code) and not solved:
                recovered_code = recover_reasoning_action(
                    llm_res,
                    tool_names,
                )

                if recovered_code:
                    print(
                        "\nPREMATURE FINAL ANSWER IGNORED."
                        "\nRECOVERED REASONING ACTION:\n",
                        recovered_code,
                    )

                    code = recovered_code
                    extract_source = "reasoning_recovery"

                else:
                    observation = (
                        "FINAL_SUBMISSION_REJECTED:\n"
                        "The current repository state has not passed "
                        "run_tests.\n"
                        "Do not submit yet. Continue solving the task."
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
                            model_name=str(llm_res.model),
                            llm_output=llm_res.output_text,
                            sandbox_input=code,
                            sandbox_output=observation,
                            retries=retries,
                        )

                        steps.append(step)

                    messages.append({
                        "role": "assistant",
                        "content": llm_res.output_text,
                    })

                    messages.append({
                        "role": "user",
                        "content": (
                            f"Iteration: {iterations + 1}/30\n"
                            "Type: final_rejected\n"
                            f"Response: {observation}\n"
                        ),
                    })

                    print(
                        f"ITERATION: {iterations + 1}",
                        "\nTYPE:\n final_rejected",
                        "\nRESPONSE:\n",
                        observation,
                    )

                    continue
                # =================== END FINAL SUBMISSION ===================

            if extract_source == "output":
                assistant_content = llm_res.output_text
            else:
                assistant_content = (
                    "```python\n"
                    f"{code}\n"
                    "```"
                )

            messages.append({
                "role": "assistant",
                "content": assistant_content,
            })
            # starting the process in order to run the generated code
            process = ctx.Process(
                target=sandbox.worker,
                args=(code, queue, tool_names, child_conn)
            )

            # ===== PERSISTENT DATA TESTS =====
            executed_tool: str | None = None
            executed_args: dict[str, Any] = {}
            # =================================

            process.start()
            time_start = monotonic()
            time_out = False

            while process.is_alive():
                if parent_conn.poll(0.05):
                    # get the instructions from the pipe and call adequate tool
                    tool_call_instructions = parent_conn.recv()
                    # store executed tools data
                    executed_tool = tool_call_instructions["tool"]
                    executed_args = tool_call_instructions["args"]
                    # call tool
                    res_tool = await client.call_tool(
                        tool_call_instructions["tool"],
                        tool_call_instructions["args"]
                        )
                    # send formated output to child connection
                    if res_tool.isError:
                        parent_conn.send({
                            "type": "tool_result",
                            "status": "error",
                            "result": res_tool.content[0].text
                            })
                    elif res_tool.structuredContent:
                        structured = res_tool.structuredContent
                        if (
                            isinstance(structured, dict)
                            and set(structured.keys()) == {"result"}
                        ):
                            result = structured["result"]
                        else:
                            result = structured
                        parent_conn.send({
                            "type": "tool_result",
                            "status": "ok",
                            "result": result
                            })
                    else:
                        parent_conn.send({
                            "type": "tool_result",
                            "status": "ok",
                            "result": res_tool.content[0].text
                            })

                if monotonic() - time_start >= (
                    config.max_execution_time_seconds
                ):
                    time_out = True
                    process.terminate()
                    process.join()
                    break

            process.join()

            if llm_res.usage is not None:
                total_input_token += llm_res.usage.input_tokens
                total_output_token += llm_res.usage.output_tokens

                if extract_source == "output":
                    llm_metric_output = llm_res.output_text
                else:
                    llm_metric_output = get_reasoning_text(llm_res)

                if time_out:
                    # pass timeout to llm
                    step = StepMetrics(
                            step=iterations+1,
                            input_tokens=llm_res.usage.input_tokens,
                            output_tokens=llm_res.usage.output_tokens,
                            request_time_ms=request_time,
                            api_url=str(llm.client.base_url),
                            model_name=str(llm_res.model),
                            llm_output=llm_metric_output,
                            sandbox_input=code,
                            sandbox_output="timeout",
                            retries=retries
                        )
                    print("TIMEOUT")
                else:
                    type, res = queue.get()
                    observation = res

                    if executed_tool == "run_tests":
                        test_result = compact_test_result(res)
                        if test_result.startswith("PASS:"):
                            solved = True

                    if type == "output":
                        tool_usage_extraction(
                            executed_tool,
                            executed_args,
                            persistent_context,
                            res
                            )
                    if type == "output" and res == "":
                        observation = ("Code executed successfully"
                                       " with no output")
                    step = StepMetrics(
                            step=iterations+1,
                            input_tokens=llm_res.usage.input_tokens,
                            output_tokens=llm_res.usage.output_tokens,
                            request_time_ms=request_time,
                            api_url=str(llm.client.base_url),
                            model_name=str(llm_res.model),
                            llm_output=llm_metric_output,
                            sandbox_input=code,
                            sandbox_output=str(res),
                            retries=retries
                        )
                    if type == "final":
                        if not solved:
                            observation = (
                                "FINAL_SUBMISSION_REJECTED:\n"
                                "The current repository state has "
                                "not been validated "
                                "by a successful run_tests.\n"
                                "Run run_tests before submitting."
                            )
                        else:
                            print(f"FINAL ANSWER GENERATED:\n{res}")
                            total_time = monotonic() - agent_start
                            steps.append(step)
                            solution = SolutionOutput(
                                task_id=str(task.instance_id),
                                benchmark="swebench",
                                success=True,
                                solution=str(res),
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
                        "content": f"Iteration: {iterations + 1}/30\n"
                        f"Type: {type}\nResponse: {observation}\n"
                    })
                    print(f"ITERATION: {iterations + 1}",
                          "TYPE:\n", type, "\nRESPONSE:\n", res)
                steps.append(step)

    finally:
        print(
            f"\n\nTOTAL TOKEN USAGE: {total_input_token + total_output_token}"
        )
        print(f"\n\nINPUT: {total_input_token}\n")
        print(f"\n\nOUTPUT: {total_output_token}\n")
        total_time = monotonic() - agent_start
        print(f"\n\nTIME: {total_time}\n")
        print("\n\nSTEPS:\n")
        for step in steps:
            print("\n", step)
        if client is not None:
            await client.close()
        if container is not None:
            print("Removing container...")
            container.remove(force=True)


if __name__ == "__main__":
    main_wrapper()
