# pyright: reportMissingTypeArgument = false
# pyright: reportUnknownArgumentType = false
# pyright: reportUnknownMemberType = false
# pyright: reportUnknownParameterType = false
# pyright: reportUnknownVariableType = false
from mcp.server.fastmcp import FastMCP
import subprocess
import sys
import os
import json


mcp = FastMCP("MBPP", host="127.0.0.1", port=8000)
# get the transport type via global variable, if not set, stdio is default
# so we set it when starting our http connection
transport = os.getenv("MCP_TRANSPORT", "stdio")
if transport not in ("stdio", "streamable-http"):
    raise ValueError(f"Invalid transport value: {transport}")


@mcp.tool()
def run_tests(
    test_list: list[str],
    code: str,
    test_imports: list[str] | None = None,
) -> dict[str, object]:
    """
    Run an MBPP candidate against the public tests.

    Returns the first failing test with useful diagnostic information.
    """

    if test_imports is None:
        test_imports = []

    marker = "__MBPP_RESULT__"

    runner = f"""
import ast
import json

MARKER = {marker!r}

namespace = {{}}

imports_code = {chr(10).join(test_imports)!r}
candidate_code = {code!r}
tests = {test_list!r}


def emit(payload):
    print(
        MARKER
        + json.dumps(payload, ensure_ascii=False)
    )


try:
    if imports_code:
        exec(imports_code, namespace, namespace)

    exec(candidate_code, namespace, namespace)

except Exception as exc:
    emit({{
        "status": "error",
        "success": False,
        "phase": "candidate",
        "error_type": type(exc).__name__,
        "output": (
            f"Candidate execution failed: "
            f"{{type(exc).__name__}}: {{exc}}"
        )
    }})
    raise SystemExit(1)


for index, test in enumerate(tests, 1):
    try:
        tree = ast.parse(test)

        # Special handling for the very common MBPP shape:
        #
        # assert function(...) == expected
        #
        # This allows us to report actual / expected directly.
        if (
            len(tree.body) == 1
            and isinstance(tree.body[0], ast.Assert)
            and isinstance(tree.body[0].test, ast.Compare)
            and len(tree.body[0].test.ops) == 1
            and isinstance(tree.body[0].test.ops[0], ast.Eq)
            and len(tree.body[0].test.comparators) == 1
        ):
            comparison = tree.body[0].test

            actual = eval(
                compile(
                    ast.Expression(comparison.left),
                    "<mbpp-actual>",
                    "eval"
                ),
                namespace,
                namespace
            )

            expected = eval(
                compile(
                    ast.Expression(comparison.comparators[0]),
                    "<mbpp-expected>",
                    "eval"
                ),
                namespace,
                namespace
            )

            if actual != expected:
                emit({{
                    "status": "error",
                    "success": False,
                    "failed_test_index": index,
                    "failed_test": test,
                    "actual": repr(actual),
                    "expected": repr(expected),
                    "output": (
                        f"Public test {{index}}/{{len(tests)}} failed:\\n"
                        f"{{test}}\\n"
                        f"actual: {{actual!r}}\\n"
                        f"expected: {{expected!r}}"
                    )
                }})
                raise SystemExit(1)

            continue

        # Generic fallback for assertions that are not simple equality.
        exec(test, namespace, namespace)

    except SystemExit:
        raise

    except Exception as exc:
        emit({{
            "status": "error",
            "success": False,
            "failed_test_index": index,
            "failed_test": test,
            "error_type": type(exc).__name__,
            "output": (
                f"Public test {{index}}/{{len(tests)}} failed:\\n"
                f"{{test}}\\n"
                f"{{type(exc).__name__}}: {{exc}}"
            )
        }})
        raise SystemExit(1)


emit({{
    "status": "ok",
    "success": True,
    "output": f"All {{len(tests)}} public tests passed"
}})
"""

    try:
        res = subprocess.run(
            [sys.executable, "-c", runner],
            capture_output=True,
            text=True,
            timeout=5
        )

    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "success": False,
            "output": "Execution stopped after 5 seconds"
        }

    # Candidate stdout may contain arbitrary prints.
    # Search from the end for our structured result.
    for line in reversed(res.stdout.splitlines()):
        if not line.startswith(marker):
            continue

        try:
            result = json.loads(
                line[len(marker):]
            )
        except json.JSONDecodeError:
            break

        if isinstance(result, dict):
            return result

    error = (res.stderr or res.stdout).strip()

    if len(error) > 2000:
        error = error[-2000:]

    return {
        "status": "error",
        "success": False,
        "output": error or "Candidate execution failed"
    }


if __name__ == "__main__":
    mcp.run(transport=transport)
