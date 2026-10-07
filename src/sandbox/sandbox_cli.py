# pyright: reportUnknownMemberType = false
# pyright: reportMissingTypeStubs = false
# pyright: reportUnknownArgumentType = false
# pyright: reportUnknownVariableType = false
# pyright: reportAttributeAccessIssue = false
# pyright: reportUnnecessaryIsInstance = false
from src.swebench.export_script import export_jedi_script
from src.swebench.export_script import export_eval_script
from src.swebench.json_load_dump_swe import JsonHandler
from src.sandbox.sandbox_config import SandboxConfig
from src.sandbox.sandbox_model import Sandbox
from src.mcp.mcp_client import MCPClient
from multiprocessing import get_context
from time import monotonic
import json
import argparse
import asyncio
import docker
import sys
import os


def main_wrapper() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", nargs="?")
    parser.add_argument("--mcp-stdio")
    parser.add_argument("--mcp-server")
    args = parser.parse_args()
    asyncio.run(main(
        args.config,
        args.mcp_stdio,
        args.mcp_server
    ))


async def main(
        config_file: str | None,
        mcp_stdio: str | None,
        mcp_server: str | None
) -> None:
    # sandbox setup
    # if arg when run sandbox:
    if config_file is None:
        config = SandboxConfig()
    else:
        try:
            with open(config_file, "r") as file:
                config = SandboxConfig.model_validate_json(file.read())
        except Exception as e:
            print(e)
            return
    sandbox = Sandbox(config)
    ctx = get_context("spawn")
    container = None
    testbed_path = os.getenv("TESTBED_PATH")

    if (
        mcp_stdio
        and "swebench" in mcp_stdio
        and testbed_path
    ):
        client = MCPClient(mcp_stdio, None)

    # if stdio on swebench:
    elif mcp_stdio and "swebench" in mcp_stdio:
        task_file = "src/sandbox/swebench_task.json"
        handler = JsonHandler()
        task = handler.task_loader(task_file)
        docker_client = docker.from_env()
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

        client_params = (f"python mcp_tools_swebench.py --id {container.id}"
                         f" --python-path {python_path_docker}")
        client = MCPClient(client_params, None)
    # if stdio or http on mbpp serv:
    else:
        client = MCPClient(mcp_stdio, mcp_server)

    try:
        await client.connect()
        await client.load_tools()
        tool_names = [tool.name for tool in client.tools]
    except Exception as e:
        print(e)
        await client.close()
        return

    try:
        piped_input = not sys.stdin.isatty()
        while True:
            parent_conn, child_conn = ctx.Pipe()

            if piped_input:
                code = sys.stdin.read()

                if not code.strip():
                    break
            else:
                # Normal interactive REPL mode.
                try:
                    code = input("\033[33m>>>\033[0m")
                except EOFError:
                    break

                if code == "exit":
                    break

                if not code.strip():
                    continue
            partial_parent_conn, partial_child_conn = ctx.Pipe(duplex=False)
            queue = ctx.SimpleQueue()
            process = ctx.Process(
                target=sandbox.worker,
                args=(code, queue, tool_names, child_conn, partial_child_conn)
            )

            process.start()
            time_start = monotonic()
            timed_out = False

            partial_output = []

            while process.is_alive():
                while partial_parent_conn.poll():
                    partial_output.append(partial_parent_conn.recv())
                if parent_conn.poll(0.05):
                    # get the instructions from the pipe and call adequate tool
                    tool_call_instructions = parent_conn.recv()
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
                        structured = res_tool.structuredContent
                        if (
                            isinstance(structured, dict)
                            and set(structured.keys()) == {"result"}
                        ):
                            result = structured["result"]
                        else:
                            result = structured
                        if not isinstance(result, str):
                            result = json.dumps(result)
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
                if (monotonic() - time_start >=
                        config.max_execution_time_seconds):
                    process.terminate()
                    process.join()
                    timed_out = True
                    break

            if timed_out:
                if partial_output:
                    print("".join(partial_output), end="")

                print(
                    "Execution timed out, it may be because of an"
                    " infinite loop"
                )
            else:
                res = queue.get()
                print(res)
            if piped_input:
                break
    finally:
        await client.close()
        if mcp_stdio and "swebench" in mcp_stdio:
            if container is not None:
                print("Removing container...")
                container.remove(force=True)
