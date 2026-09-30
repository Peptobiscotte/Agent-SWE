# pyright: reportMissingTypeStubs = false
# pyright: reportUnknownMemberType = false
# pyright: reportUnknownVariableType = false
# pyright: reportUnknownArgumentType = false
import jedi
import sys


def jedi_script():
    """
    This function uses the jedi module to get all references to a specified
    variable, found by its name, file and line. I returns found references
    in a grep-like output
    """

    name = sys.argv[1]
    filepath = sys.argv[2]
    line = int(sys.argv[3])

    with open(filepath) as file:
        code = file.read()

    project = jedi.Project("./")

    script = jedi.Script(
        code=code,
        path=filepath,
        project=project
    )

    line_str = code.splitlines()[line - 1]
    column = line_str.find(name)

    # definitions = script.goto(
    #     line=line,
    #     column=column
    # )

    # print("DEF:\n")

    # for definition in definitions:
    #     print(
    #         "NAME: ", definition.name, "\n",
    #         "PATH: ", definition.module_path,  "\n",
    #         "LINE: ", definition.line,  "\n",
    #         "COL: ", definition.column,
    #     )

    references = script.get_references(
        line=line,
        column=column
    )

    ref_list: list[str] = []

    print("REFERENCES: \n")

    for ref in references:
        with open(ref.module_path) as file:
            whole_text = file.read().splitlines()

        line_content = whole_text[ref.line - 1]

        one_liner = f"{ref.module_path}:{ref.line} {line_content}"
        ref_list.append(one_liner)

    print("\n".join(ref_list))


if __name__ == "__main__":
    jedi_script()
