# pyright: reportMissingTypeStubs = false
# pyright: reportUnknownArgumentType = false
# pyright: reportUnknownMemberType = false
# pyright: reportUnknownVariableType = false
# pyright: reportAttributeAccessIssue = false
from src.mbpp.json_load_dump import JsonHandler
from src.mbpp.llm import LLM
from src.utils.extract import Extractor
from src.mbpp.mbpp_models import StepMetrics, SolutionOutput
from src.sandbox.sandbox_model import Sandbox
from src.sandbox.sandbox_config import SandboxConfig
from multiprocessing import get_context
from src.mcp.mcp_client import MCPClient
from time import monotonic
import asyncio


async def main() -> None:
    # ================ METRICS ================
    agent_start = monotonic()
    total_input_token = 0
    total_output_token = 0
    res = ""
    steps: list[StepMetrics] = []
    # ================ TASK LOADING ================
    src_path = "../cache/mbpp_task.json"
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
    client = MCPClient(None, "http://localhost:8000/mcp")
    await client.connect()
    await client.load_tools()
    manual = client.create_manual()
    tool_names = [tool.name for tool in client.tools]
    #
    # ================ PROMPT ================
    #
    llm = LLM()
    system_prompt = (
        "You are a coding agent.\n"
        "Return only one block of executable Python code.\n"
        "Use the exact function definition provided.\n"
        "No prose added.\n"
        f"{manual}\n"
        f"Test imports: {task.test_imports}.\n"
        f"Test list: {task.test_list}"
        "MCP tools are exposed inside the sandbox as ordinary "
        "Python functions.\n"
        "You MUST call them using valid Python syntax inside the single Python"
        " code block.\n"
        "For example:\n"
        "result = run_tests(\n"
        "    test_imports=[...],\n"
        "    test_list=[...],\n"
        '    code="..."\n'
        ")\n"
        "print(result)\n"
        "Do NOT use XML tags such as <run_tests>...</run_tests>.\n"
        "Do NOT emit tool calls outside the Python code block.\n"
        "final_answer is also a Python function available in the sandbox.\n"
        "To submit the solution, execute:\n"
        "final_answer(code='your code solution')\n"
        "For example:\n"
        'final_answer(code="""def add(a, b):\n    return a + b""")'
        "Do NOT use <final_answer> tags.\n"
        "The final_answer(...) call MUST be inside the Python code block."
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
    # ================ SANDBOX ================
    #
    config = SandboxConfig()
    sandbox = Sandbox(config)
    # llm-like code for testing purposes
    # if iterations < 2:
    #     code = """print(
    #     run_tests(
    #     test_imports=[],
    #     test_list=["assert check_monthnumb_number(2)==False",
    #     "assert check_monthnumb_number(6)==False"],
    #     code="while True:\\n    pass"
    #     ))"""
    # else:
    #     code = """final_answer("stop")"""
    #
    #  ================ MAIN LOOP ================
    #
    try:
        for iterations in range(10):
            # ================ GENERATION ================
            print(f"\n\nPROMPT {iterations}", messages, "\n\n")
            # timing the request
            request_start = monotonic()
            llm_res = llm.generate("qwen/qwen3.8-27b", messages)
            request_time = (monotonic() - request_start) * 1000
            # adding output to future message prompt
            messages.append({
                "role": "assistant",
                "content": llm_res.output_text
            })
            # ================ EXTRACTION ================
            print("\n\nLLM RESPONSE:\n", llm_res.output_text, "\n\n")
            extractor = Extractor(llm_res.output_text)
            code = extractor.extract_from_md()
            print("\nCODE EXTRACTED:\n", code)
            # for params in res:
            #     print(params)
            #     print()
            #
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
                            retries=0
                        )
                    print("TIMEOUT")
                else:
                    type, res = queue.get()
                    observation = res
                    if type == "output" and res == "":
                        observation = "Code executed successfully"
                        " with no output"
                    total_input_token += llm_res.usage.input_tokens
                    total_output_token += llm_res.usage.output_tokens
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
                            retries=0
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
                        handler.solution_dump(solution)
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
    asyncio.run(main())
