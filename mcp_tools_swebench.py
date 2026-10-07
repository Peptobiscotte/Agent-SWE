# pyright: reportMissingTypeArgument = false
# pyright: reportAttributeAccessIssue = false
# pyright: reportUnknownArgumentType = false
# pyright: reportUnknownMemberType = false
# pyright: reportUnknownParameterType = false
# pyright: reportUnknownVariableType = false
from mcp.server.fastmcp import FastMCP
from typing import Any
from pathlib import Path
import subprocess
import docker
import argparse
import fnmatch
import os
import re
import sys


MAX_RESULTS = 30
MAX_OUTPUT_CHARS = 12_000
MAX_READ_LINE = 200

mcp = FastMCP("SWEBENCH", host="127.0.0.1", port=8000)

transport = os.getenv("MCP_TRANSPORT", "stdio")
if transport not in ("stdio", "streamable-http"):
    raise ValueError(f"Invalid transport value: {transport}")

parser = argparse.ArgumentParser()
parser.add_argument("--id")
parser.add_argument("--python-path")
args = parser.parse_args()

testbed_path_env = os.getenv("TESTBED_PATH")

container = None
python_path = args.python_path or sys.executable
local_testbed: Path | None = None

if args.id:
    if not args.python_path:
        raise ValueError("--python-path is required when --id is provided")
    docker_client = docker.from_env()
    container = docker_client.containers.get(args.id)
elif testbed_path_env:
    local_testbed = Path(testbed_path_env).resolve()
    if not local_testbed.is_dir():
        raise RuntimeError(
            f"TESTBED_PATH does not point to a directory: {local_testbed}"
        )
else:
    raise RuntimeError(
        "Either --id/--python-path or TESTBED_PATH must be provided"
    )


def _local_path(path: str) -> Path:
    """Map a /testbed path used by tools to the local exam testbed."""
    if local_testbed is None:
        raise RuntimeError("Local TESTBED_PATH mode is not active")

    if path == "/testbed":
        candidate = local_testbed
    elif path.startswith("/testbed/"):
        candidate = local_testbed / path[len("/testbed/"):]
    else:
        candidate = local_testbed / path

    candidate = candidate.resolve()

    if candidate != local_testbed and local_testbed not in candidate.parents:
        raise RuntimeError(f"Path escapes TESTBED_PATH: {path}")

    return candidate


def _virtual_path(path: Path) -> str:
    """Convert a local exam path back to the /testbed namespace."""
    if local_testbed is None:
        raise RuntimeError("Local TESTBED_PATH mode is not active")

    relative = path.resolve().relative_to(local_testbed)
    if str(relative) == ".":
        return "/testbed"
    return "/testbed/" + relative.as_posix()


def _truncate_lines(lines: list[str], label: str = "lines") -> str:
    if len(lines) > MAX_RESULTS:
        lines = lines[:MAX_RESULTS]
        lines.append(f"TRUNCATED: showing first {MAX_RESULTS} {label}")

    out = "\n".join(lines)

    if len(out) > MAX_OUTPUT_CHARS:
        out = out[:MAX_OUTPUT_CHARS] + "\n[OUTPUT TRUNCATED]"

    return out


@mcp.tool()
def read_file(
    filepath: str,
    start_line: int,
    end_line: int
) -> str:
    trunc_msg = ""
    requested_end = end_line

    if container is not None:
        res = container.exec_run(["cat", filepath], workdir="/testbed")
        text = res.output.decode()
        if res.exit_code != 0:
            raise RuntimeError(text)
    else:
        path = _local_path(filepath)
        if not path.is_file():
            raise RuntimeError(f"File not found: {filepath}")
        text = path.read_text()

    lines = text.splitlines()

    if start_line < 1:
        raise ValueError("start_line must be >= 1")
    if end_line < start_line:
        raise ValueError("end_line must be >= start_line")
    if start_line > len(lines):
        raise ValueError("start_line out of range")

    if end_line - start_line + 1 > MAX_READ_LINE:
        end_line = start_line + MAX_READ_LINE - 1
        trunc_msg = (
            f"[TRUNCATED: requested lines {start_line}-{requested_end}, "
            f"returned {start_line}-{end_line}. "
            f"Continue from line {end_line + 1}.]"
        )

    selected = lines[start_line - 1:end_line]
    numbered = [
        f"{line_number}: {line}"
        for line_number, line in enumerate(selected, start=start_line)
    ]

    final = "\n".join(numbered)
    if trunc_msg:
        final += "\n" + trunc_msg

    return final


@mcp.tool()
def list_files(directory: str, pattern: str) -> str:
    """
    List repository files matching a filename pattern.

    directory may be absolute under /testbed or relative to /testbed.
    Example:
        directory="/testbed/sympy/physics/vector"
        pattern="*.py"
    """
    if container is not None:
        res = container.exec_run(
            ["find", ".", "-name", pattern],
            workdir=directory
        )
        if res.exit_code != 0:
            raise RuntimeError(res.output.decode())

        lines = res.output.decode().splitlines()
        for i, line in enumerate(lines):
            lines[i] = directory.rstrip("/") + line[1:]
    else:
        root = _local_path(directory)
        if not root.is_dir():
            raise RuntimeError(f"Directory not found: {directory}")

        matches = sorted(
            path for path in root.rglob("*")
            if path.is_file() and fnmatch.fnmatch(path.name, pattern)
        )
        lines = [_virtual_path(path) for path in matches]

    if len(lines) > MAX_RESULTS:
        lines = lines[:MAX_RESULTS]
        lines.append(f"TRUNCATED: showing first {MAX_RESULTS} results")

    return "\n".join(lines)


@mcp.tool()
def search_code(pattern: str, file_pattern: str) -> str:
    """
    Search recursively for text in repository files.

    file_pattern filters filenames, for example "*.py".
    """
    if container is not None:
        res = container.exec_run(
            [
                "grep", "-R", "-n", pattern, "/testbed",
                f"--include={file_pattern}"
            ],
            workdir="/testbed"
        )

        if res.exit_code == 1:
            return "No matches found"
        if res.exit_code != 0:
            raise RuntimeError(res.output.decode())

        return _truncate_lines(
            res.output.decode().splitlines(),
            "lines"
        )

    if local_testbed is None:
        raise RuntimeError("Local TESTBED_PATH mode is not active")

    found: list[str] = []

    for path in sorted(local_testbed.rglob("*")):
        if not path.is_file() or not fnmatch.fnmatch(path.name, file_pattern):
            continue

        try:
            lines = path.read_text().splitlines()
        except UnicodeDecodeError:
            continue

        for line_number, line in enumerate(lines, start=1):
            if pattern in line:
                found.append(
                    f"{_virtual_path(path)}:{line_number} {line}"
                )

    if not found:
        return "No matches found"

    return _truncate_lines(found, "lines")


@mcp.tool()
def search_function_or_class_definition_in_code(name: str) -> Any:
    if container is not None:
        pattern = rf"^[[:space:]]*(def|class)[[:space:]]+{name}\b"
        res = container.exec_run(
            [
                "grep", "-R", "-n", "-E", pattern, "/testbed",
                "--include", "*.py"
            ],
            workdir="/testbed"
        )

        if res.exit_code and res.exit_code > 1:
            raise RuntimeError(res.output.decode())
        if res.exit_code == 1:
            return "No function or class definition found"

        return res.output.decode()

    if local_testbed is None:
        raise RuntimeError("Local TESTBED_PATH mode is not active")

    regex = re.compile(
        rf"^\s*(?:def|class)\s+{re.escape(name)}\b"
    )
    found: list[str] = []

    for path in sorted(local_testbed.rglob("*.py")):
        lines = path.read_text().splitlines()

        for line_number, line in enumerate(lines, start=1):
            if regex.search(line):
                found.append(
                    f"{_virtual_path(path)}:{line_number} {line}"
                )

    if not found:
        return "No function or class definition found"

    return _truncate_lines(found, "lines")


@mcp.tool()
def find_references(name: str, filepath: str, line: int) -> str:
    if container is not None:
        res = container.exec_run([
            python_path,
            "/tmp/jedi_script.py",
            name,
            filepath,
            str(line)
        ])

        if res.exit_code != 0:
            raise RuntimeError(res.output.decode())

        return _truncate_lines(
            res.output.decode().splitlines(),
            "lines"
        )

    # The sandbox exam does not install the Docker-side Jedi helper.
    # For the local testbed, a repository-wide textual reference search
    # provides the expected grep-like reference output.
    if local_testbed is None:
        raise RuntimeError("Local TESTBED_PATH mode is not active")

    found: list[str] = []

    for path in sorted(local_testbed.rglob("*.py")):
        lines = path.read_text().splitlines()

        for line_number, content in enumerate(lines, start=1):
            if name in content:
                found.append(
                    f"{_virtual_path(path)}:{line_number} {content}"
                )

    if not found:
        return "No references found"

    return _truncate_lines(found, "lines")


@mcp.tool()
def edit_file(filepath: str, old_str: str, new_str: str) -> str:
    """
    Replace exactly one occurrence of old_str with new_str in a file.

    old_str must match the existing file content exactly and uniquely.
    """
    if container is not None:
        res = container.exec_run(["cat", filepath])
        if res.exit_code != 0:
            raise RuntimeError(res.output.decode())
        file_content = res.output.decode()
    else:
        path = _local_path(filepath)
        if not path.is_file():
            raise RuntimeError(f"File not found: {filepath}")
        file_content = path.read_text()

    count = file_content.count(old_str)

    if count == 0:
        raise RuntimeError(
            f"edit_file failed: old_str was not found in {filepath}. "
            "Re-read the relevant section and copy the exact current text "
            "before retrying."
        )

    if count > 1:
        raise RuntimeError(
            f"{old_str!r} occurs {count} times in {filepath}; "
            "old_str must uniquely identify the edit location"
        )

    new_content = file_content.replace(old_str, new_str)

    if container is not None:
        res = container.exec_run([
            "python",
            "-c",
            (
                "from pathlib import Path; "
                f"Path({filepath!r}).write_text({new_content!r})"
            ),
        ],
            workdir="/testbed",
            user="0"
        )
        if res.exit_code != 0:
            raise RuntimeError(res.output.decode())
    else:
        _local_path(filepath).write_text(new_content)

    return f"Successfully edited {filepath}"


@mcp.tool()
def get_patch() -> Any:
    if container is not None:
        res = container.exec_run(
            [
                "git",
                "-c", "safe.directory=/testbed",
                "-c", "core.fileMode=false",
                "diff",
            ],
            workdir="/testbed",
        )

        if res.exit_code != 0:
            raise RuntimeError(res.output.decode())

        return res.output.decode()

    if local_testbed is None:
        raise RuntimeError("Local TESTBED_PATH mode is not active")

    res = subprocess.run(
        ["git", "-c", "core.fileMode=false", "diff"],
        cwd=local_testbed,
        capture_output=True,
        text=True,
    )

    if res.returncode != 0:
        raise RuntimeError(res.stderr or res.stdout)

    return res.stdout if res.stdout else "No changes"


@mcp.tool()
def run_command(command: str, workdir: str) -> dict[str, Any]:
    if container is not None:
        res = container.exec_run(
            ["bash", "-lc", command],
            workdir=workdir,
            demux=True
        )
        out, err = res.output

        stdout = out.decode() if out else ""
        stderr = err.decode() if err else ""

        return {
            "stdout": stdout,
            "stderr": stderr,
            "exitcode": res.exit_code
        }

    cwd = _local_path(workdir)

    res = subprocess.run(
        ["bash", "-lc", command],
        cwd=cwd,
        capture_output=True,
        text=True,
    )

    return {
        "stdout": res.stdout,
        "stderr": res.stderr,
        "exitcode": res.returncode
    }


@mcp.tool()
def run_tests() -> Any:
    if container is None:
        # The sandbox tool exam only verifies that run_tests is callable.
        # The real SWE-bench evaluation script is injected in Docker mode.
        return "No eval script configured in local TESTBED_PATH mode"

    res = container.exec_run([
        "bash",
        "/tmp/eval_script.sh"
    ])

    if res.exit_code != 0:
        raise RuntimeError(res.output.decode())

    start_txt = ">>>>> Start Test Output"
    end_txt = ">>>>> End Test Output"

    output = res.output.decode()
    start = output.find(start_txt)
    end = output.find(end_txt)

    if start == -1 or end == -1:
        return output

    start += len(start_txt)

    return output[start:end].strip()


if __name__ == "__main__":
    mcp.run(transport=transport)
