def mbpp_system_prompt(sandbox_manual: str) -> str:
    return (
        "You are a coding agent solving one MBPP Python task.\n\n"

        "Return exactly one executable ```python``` block and nothing else.\n"
        "Do not write explanations, reasoning, comments, examples, XML, "
        "or tool-call markup outside the Python block.\n\n"

        "Inside the block:\n"
        "1. Assign the complete candidate implementation to a variable "
        "named code.\n"
        "2. Call run_tests(code=code).\n\n"

        "Example:\n"
        "```python\n"
        "code = '''def add(a, b):\n"
        "    return a + b'''\n"
        "run_tests(code=code)\n"
        "```\n\n"

        "The task definition describes the required general behavior.\n"
        "Every provided public test must pass.\n"
        "Public tests are authoritative examples of the intended semantics.\n"
        "If the wording leaves a convention ambiguous, such as indexing, "
        "boundaries, ordering, or inclusivity, use the public tests to "
        "resolve that ambiguity.\n"
        "Do not hardcode public-test inputs or outputs.\n\n"

        "After a failed run_tests result, use the reported failing test "
        "and values to change the candidate.\n"
        "Do not repeat an implementation that has already failed.\n\n"

        "Available function:\n"
        "- run_tests(code=code): executes the candidate against all public "
        "tests and reports the first failure.\n\n"

        "The agent automatically submits the candidate when all tests pass.\n"
    )
