# pyright: reportMissingTypeStubs = false
# pyright: reportUnknownArgumentType = false
# pyright: reportUnknownMemberType = false
# pyright: reportUnknownVariableType = false
# pyright: reportAttributeAccessIssue = false
# pyright: reportUnnecessaryIsInstance = false


from src.swebench.swe_models import StepMetrics, SolutionOutput
from src.swebench.export_script import export_jedi_script
from src.swebench.export_script import export_eval_script
from src.swebench.json_load_dump_swe import JsonHandler
from src.sandbox.sandbox_config import SandboxConfig
from src.swebench.sys_prompt import create_prompt
from src.swebench.llm_memory import update_memory
from src.sandbox.sandbox_model import Sandbox
from src.utils.api_loading import load_keys
from src.mcp.mcp_client import MCPClient
from src.utils.extract import Extractor
from multiprocessing import get_context
from src.mbpp.llm import LLM
from time import monotonic
import argparse
import asyncio
import docker
import sys


MAX_RECENT_MESSAGES = 6
MEMORY_UPDATE_INTERVAL = 8


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
        # code = "print(read_file(
        # filepath='AUTHORS', start_line=19, end_line=2))"
        # code = "print(list_files(
        # directory='/testbed/sympy', pattern='*.py'))"
        # code = "print(search_code(pattern='gradient', file_pattern='*.py'))"
        # code = "print(search_function_or_class_definition_in_code(
        # name='_check_vector'))"
        # code = "print(find_references(
        # name='Vector', filepath='/testbed/sympy/physics/vector/vector.py',
        # line=10))"
        # code = "print(edit_file(filepath='/testbed/AUTHORS', old_str=
        # 'There are a total of 704 authors', new_str='SUCCE$$'))"
        # code = "print(get_patch())"
        # code = "print(run_command(command='exit 42', workdir='/testbed'))"
        # code = "print(run_tests())"
        #
        # create / start / listen process
        #
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
        work_memory: str = ""
        # ================ LOOP GOES HERE ================
        for iterations in range(30):
            # we only use the system and task prompt + the n last messages
            # to avoid input tokens growing too much because certain
            # providers such as groq limits the input tokens per minute,
            # if input is above this threshold, it wont generate

            if iterations > 0 and iterations % MEMORY_UPDATE_INTERVAL == 0:
                work_memory = update_memory(
                    messages,
                    task.problem_statement,
                    work_memory,
                    llm,
                    model_name
                )

            memory_message = {
                "role": "user",
                "content": (
                    "PERSISTENT WORKING MEMORY\n"
                    "This summarizes earlier iterations that may no "
                    "longer be present in the conversation\n\n"
                    f"{work_memory}"
                )
            }

            request_messages = messages[:2]

            if work_memory:
                request_messages.append(memory_message)

            request_messages += messages[2:][-MAX_RECENT_MESSAGES:]

            # generation + time
            request_start = monotonic()
            llm_res, retries = llm.generate(
                model_name,
                request_messages,
                out=500
                )
            request_time = (monotonic() - request_start) * 1000
            # adding generated text to LLM context
            print("\n\nLLM RESPONSE:\n", llm_res, "\n\n")
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
            # starting the process in order to run the generated code
            process = ctx.Process(
                target=sandbox.worker,
                args=(code, queue, tool_names, child_conn)
            )

            process.start()
            time_start = monotonic()
            time_out = False

            while process.is_alive():
                if parent_conn.poll(0.05):
                    # get the instructions from the pipe and call adequate tool
                    tool_call_instructions = parent_conn.recv()
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
                        observation = ("Code executed successfully"
                                       " with no output")
                    step = StepMetrics(
                            step=iterations+1,
                            input_tokens=llm_res.usage.input_tokens,
                            output_tokens=llm_res.usage.output_tokens,
                            request_time_ms=request_time,
                            api_url=str(llm.client.base_url),
                            model_name=llm_res.model,
                            llm_output=llm_res.output_text,
                            sandbox_input=code,
                            sandbox_output=str(res),
                            retries=retries
                        )
                    if type == "final":
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
