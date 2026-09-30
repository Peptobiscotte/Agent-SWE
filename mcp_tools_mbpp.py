# pyright: reportMissingTypeArgument = false
# pyright: reportUnknownArgumentType = false
# pyright: reportUnknownMemberType = false
# pyright: reportUnknownParameterType = false
# pyright: reportUnknownVariableType = false
from mcp.server.fastmcp import FastMCP
import subprocess
import sys
import os


mcp = FastMCP("MBPP", host="127.0.0.1", port=8000)
# get the transport type via global variable, if not set, stdio is default
# so we set it when starting our http connection
transport = os.getenv("MCP_TRANSPORT", "stdio")
if transport not in ("stdio", "streamable-http"):
    raise ValueError(f"Invalid transport value: {transport}")


@mcp.tool()
def run_tests(
    test_imports: list[str],
    test_list: list[str],
    code: str
) -> tuple[str, str]:
    """
    Run an MBPP candidate solution against the provided public tests.

    This tool is already implemented by the environment.
    Call it directly; do not redefine or emulate it.

    Args:
        test_imports: Python import statements required by the tests.
        test_list: Assertion strings used to test the candidate solution.
        code: Complete Python code containing the required function.

    Returns:
        ("ok", "All tests passed") when all tests pass,
        ("error", stderr) when execution or an assertion fails,
        or ("timeout", message) if execution exceeds 5 seconds.

    Example:
        run_tests(
            test_imports=[],
            test_list=["assert add(2, 3) == 5"],
            code="def add(a, b):\\n    return a + b",
        )
    """
    code_whole = "\n".join(test_imports)
    code_whole += "\n" + code + "\n"
    code_whole += "\n".join(test_list)
    try:
        res = subprocess.run(
            [sys.executable, "-c", code_whole],
            capture_output=True,
            text=True,
            timeout=5
        )
    except subprocess.TimeoutExpired:
        return ("timeout", "Execution stopped after 5 seconds")
    if res.returncode == 0:
        return ("ok", "All tests passed")
    return ("error", res.stderr)


if __name__ == "__main__":
    mcp.run(transport=transport)
