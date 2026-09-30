# pyright: reportMissingTypeArgument = false
# pyright: reportAttributeAccessIssue = false
# pyright: reportUnknownArgumentType = false
# pyright: reportUnknownMemberType = false
# pyright: reportUnknownParameterType = false
# pyright: reportUnknownVariableType = false
from pathlib import Path
from docker.models.containers import Container


def export_jedi_script(container: Container) -> None:
    """
    Get the code from our jedi_script and writes it inside the docker
    container currently opened, this allows direct call to our script
    in our mcp_tools
    """
    script = Path("src/swebench/jedi_script.py").read_text()
    res = container.exec_run([
        "python",
        "-c",
        (
            "from pathlib import Path; "
            f"Path('/tmp/jedi_script.py').write_text({script!r})"
        ),
    ])

    if res.exit_code != 0:
        raise RuntimeError(res.output.decode())


def export_eval_script(container: Container, script: str) -> None:
    """
    Get the code from our evaluation_script and writes it inside the docker
    container currently opened, this allows direct call to our script
    in our mcp_tools with run_tests
    """
    res = container.exec_run([
        "python",
        "-c",
        (
            "from pathlib import Path; "
            f"Path('/tmp/eval_script.sh').write_text({script!r})"
        ),
    ])
    if res.exit_code != 0:
        raise RuntimeError(res.output.decode())
