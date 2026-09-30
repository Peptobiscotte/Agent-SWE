# pyright: reportMissingTypeArgument = false
# pyright: reportAttributeAccessIssue = false
# pyright: reportUnknownArgumentType = false
# pyright: reportUnknownMemberType = false
# pyright: reportUnknownParameterType = false
# pyright: reportUnknownVariableType = false
from mcp.server.fastmcp import FastMCP
from typing import Any
import docker
import argparse
import os


MAX_RESULTS = 30
MAX_OUTPUT_CHARS = 12_000
MAX_READ_LINE = 100

mcp = FastMCP("MBPP", host="127.0.0.1", port=8000)
# get the transport type via global variable, if not set, stdio is default
# so we set it when starting our http connection
transport = os.getenv("MCP_TRANSPORT", "stdio")
if transport not in ("stdio", "streamable-http"):
    raise ValueError(f"Invalid transport value: {transport}")

# we use --id as param when launching th server so it can get the id of the
# already launched docker
parser = argparse.ArgumentParser()
parser.add_argument("--id", required=True)
parser.add_argument("--python-path", required=True)
args = parser.parse_args()
# don't recreate the container as it is already running,
# use get to make the container object available in the serv
docker_client = docker.from_env()
container = docker_client.containers.get(args.id)
python_path = args.python_path


@mcp.tool()
def read_file(
    filepath: str,
    start_line: int,
    end_line: int
) -> str:
    trunc_msg = ""
    # executes a UNIX command
    res = container.exec_run(["cat", filepath], workdir="/testbed")
    # decode because exec_run can return bytes
    text = res.output.decode()
    # manage potential errors
    if res.exit_code != 0:
        raise RuntimeError(text)
    lines = text.splitlines()
    if start_line < 1:
        raise ValueError("start_line must be >= 1")
    if end_line < start_line:
        raise ValueError("end_line must be >= start_line")
    if start_line > len(lines):
        raise ValueError("start_line out of range")
    # slice the targetted lines
    if end_line - start_line + 1 > MAX_READ_LINE:
        end_line = start_line + MAX_READ_LINE - 1
        trunc_msg = f"[TRUNCATED], maximum {MAX_READ_LINE} per call"
    selected = lines[start_line - 1:end_line]
    # add line number to response for more readability
    num_line = start_line
    for i, line in enumerate(selected):
        selected[i] = str(num_line) + ". " + line
        num_line += 1
    final = "\n".join(selected)
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
    res = container.exec_run(["find", ".", "-name", pattern],
                             workdir=directory)
    if res.exit_code != 0:
        raise RuntimeError(res.output.decode())
    lines = res.output.decode().splitlines()
    for i, line in enumerate(lines):
        lines[i] = directory + line[1:]
    if len(lines) > MAX_RESULTS:
        lines = lines[:MAX_RESULTS]
        lines.append(f"TRUNCATED: showing first {MAX_RESULTS} results")
    return "\n".join(lines)


@mcp.tool()
def search_code(pattern: str, file_pattern: str) -> str:
    """
    Search recursively for text in repository files.

    directory specifies where to search and must be inside /testbed.
    file_pattern filters filenames, for example "*.py".
    """
    res = container.exec_run(["grep", "-R", "-n", pattern, "/testbed",
                              f"--include={file_pattern}"], workdir="/testbed")
    if res.exit_code == 1:
        return "No matches found"
    if res.exit_code != 0:
        raise RuntimeError(res.output.decode())
    out = res.output.decode()
    lines = out.splitlines()
    if len(lines) > MAX_RESULTS:
        lines = lines[:MAX_RESULTS]
        lines.append(f"TRUNCATED: showing first {MAX_RESULTS} lines")
    out = "\n".join(lines)
    if len(out) > MAX_OUTPUT_CHARS:
        out = out[:MAX_OUTPUT_CHARS]
        out += "\n[OUTPUT TRUNCATED]"
    return out


@mcp.tool()
def search_function_or_class_definition_in_code(name: str) -> str:
    pattern = rf"^[[:space:]]*(def|class)[[:space:]]+{name}\b"
    res = container.exec_run([
            "grep", "-R", "-n", "-E", pattern, "/testbed",
            "--include", "*.py"], workdir="/testbed")
    if res.exit_code and res.exit_code > 1:
        raise RuntimeError(res.output.decode())
    elif res.exit_code == 1:
        return "No function or class definition found"
    return res.output.decode()


@mcp.tool()
def find_references(name: str, filepath: str, line: int) -> str:
    res = container.exec_run([
        python_path,
        "/tmp/jedi_script.py",
        name,
        filepath,
        str(line)
    ])
    if res.exit_code != 0:
        raise RuntimeError(res.output.decode())

    out = res.output.decode()
    lines = out.splitlines()
    if len(lines) > MAX_RESULTS:
        lines = lines[:MAX_RESULTS]
        lines.append(f"TRUNCATED: showing first {MAX_RESULTS} lines")
    out = "\n".join(lines)
    if len(out) > MAX_OUTPUT_CHARS:
        out = out[:MAX_OUTPUT_CHARS]
        out += "\n[OUTPUT TRUNCATED]"
    return out


@mcp.tool()
def edit_file(filepath: str, old_str: str, new_str: str) -> str:
    """
    Replace exactly one occurrence of old_str with new_str in a file.

    old_str must match the existing file content exactly and uniquely.

    For multiline edits, use Python strings with explicit newline escapes.
    Example:
        old = 'def foo():\\n    return 1'
        new = 'def foo():\\n    return 2'
        print(edit_file(
            filepath='/testbed/example.py',
            old_str=old,
            new_str=new,
        ))

    Always print the returned value so the edit result is visible.
    """
    res = container.exec_run([
        "cat", filepath
    ])
    if res.exit_code != 0:
        raise RuntimeError(res.output.decode())
    # get the content of the file
    file_content = res.output.decode()
    # check if old str in file
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
    # create new file with replacement
    new_content = file_content.replace(old_str, new_str)
    # re-write the file
    res = container.exec_run([
            "python",
            "-c",
            (
                "from pathlib import Path; "
                f"Path({filepath!r}).write_text({new_content!r})"
            ),
        ])
    if res.exit_code != 0:
        raise RuntimeError(res.output.decode())
    return f"Successfully edited {filepath}"


@mcp.tool()
def get_patch() -> str:
    res = container.exec_run([
        "git", "-c", "core.fileMode=false", "diff"
    ])
    if res.exit_code != 0:
        raise RuntimeError(res.output.decode())
    return res.output.decode()


@mcp.tool()
def run_command(command: str, workdir: str) -> dict[str, Any]:
    # adding demux = True splits the res between stdout and stderr
    # default is mixing them in output
    res = container.exec_run(["bash", "-lc", command],
                             workdir=workdir, demux=True)
    # extract the tuple
    out, err = res.output

    stdout = out.decode() if out else ""
    stderr = err.decode() if err else ""

    exitcode = res.exit_code

    return {"stdout": stdout, "stderr": stderr, "exitcode": exitcode}


@mcp.tool()
def run_tests() -> str:
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
