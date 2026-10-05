# pyright: reportUnknownMemberType = false
import ast
from typing import Any


class Extractor:
    def extract_from_md(self, llm_out: str, tools_dict: dict[str, Any]) -> str:
        """
        Removes everything but the code block in a .md style string
        """

        start = "```python"
        end = "```"
        code = ""
        # first try .md -> python extraction
        blocks: list[str] = []
        search_from = 0

        while True:
            start_index = llm_out.find(start, search_from)
            if start_index == -1:
                break

            start_index += len(start)
            end_index = llm_out.find(end, start_index)
            if end_index == -1:
                break

            blocks.append(llm_out[start_index:end_index].strip())
            search_from = end_index + len(end)

        # keep the block containing an actual tool call
        for block in blocks:
            if any(f"{tool_name}(" in block for tool_name in tools_dict):
                code = block
                break
        # second try tool call extraction
        if code == "":
            if "<tool_call>" in llm_out:
                func_name = self.extract_func_name(llm_out)
                if func_name not in tools_dict:
                    # print("\n", repr(func_name))
                    return ""
                params = self.extract_params_name(
                    llm_out, tools_dict.get(func_name))
                code = f"print({func_name}("
                p_len = len(params)
                for i in range(len(params)):
                    code += params[i]
                    if i <= p_len - 2:
                        code += ", "
                code += "))"

                return code

        return code

    def extract_func_name(self, llm_out: str) -> str:
        txt = llm_out
        # first encountered tool calling format
        marker = "<function="
        start = txt.find(marker)

        if start != -1:
            start += len(marker)
            end = txt.find(">", start)

            if end != -1:
                return txt[start:end].strip()

        # second encountered tool calling format
        marker = "<tool_call>"
        start = txt.find(marker)

        if start != -1:
            start += len(marker)
            end = txt.find("<parameter=", start)

            if end != -1:
                return txt[start:end].strip()

        return ""

    def extract_params_name(self, llm_out: str, prop: Any) -> list[str]:
        """
        extract params name and values from XML tool call
        """
        txt = llm_out
        params_number = txt.count("<parameter=")
        if params_number < 1:
            return []
        params_list: list[str] = []

        for _ in range(params_number):
            param_name = ""
            param_index = txt.find("<parameter=")
            param_term = txt.find("</parameter>")
            if param_term != -1 and param_index != -1:
                param_cell = txt[param_index + len("<parameter="):param_term]
                name_end = param_cell.find(">")
                if name_end != -1:
                    param_name = param_cell[:name_end]
                    param_type = prop.get("properties")[param_name]["type"]
                    param_val = param_cell[
                        len(param_name + ">"):].strip()
                    f_val = ""

                    if param_type == "string":
                        f_val = repr(param_val)
                    elif param_type == "boolean":
                        f_val = ("True" if param_val.lower() == "true"
                                 else "False")
                    elif param_type == "integer":
                        f_val = str(int(param_val))
                    elif param_type == "number":
                        f_val = str(float(param_val))

                    params_list.append(f"{param_name}={f_val}")
                    txt = txt[
                        param_term + len("</parameter>"):].strip()
        return params_list

    def extract_from_response(
            self,
            llm_res: Any,
            tools_dict: dict[str, Any]
            ) -> tuple[str, str]:
        """
        Prefer the model final output.

        If there is no executable action in output_text, cautiously fall back
        to reasoning, but only when it contains exactly one unambiguous MCP
        action.
        """

        output_text = getattr(llm_res, "output_text", "") or ""

        code = self.extract_from_md(
            output_text,
            tools_dict
        ).strip()

        if code:
            return code, "output"

        reasoning = self._extract_reasoning_text(llm_res)

        if not reasoning:
            return "", "error"

        blocks = self._extract_python_blocks(reasoning)

        # Several code blocks means the model was considering several actions.
        # Do not guess which one should actually be executed.
        if len(blocks) != 1:
            return "", "error"

        candidate = blocks[0].strip()

        if not self._is_single_tool_action(candidate, tools_dict):
            return "", "error"

        return candidate, "reasoning"

    def _extract_reasoning_text(self, llm_res: Any) -> str:
        parts: list[str] = []

        for item in getattr(llm_res, "output", []) or []:
            if getattr(item, "type", None) != "reasoning":
                continue

            for content in getattr(item, "content", []) or []:
                text = getattr(content, "text", None)

                if isinstance(text, str) and text:
                    parts.append(text)

        return "\n".join(parts)

    def _extract_python_blocks(self, text: str) -> list[str]:
        start = "```python"
        end = "```"

        blocks: list[str] = []
        search_from = 0

        while True:
            start_index = text.find(start, search_from)

            if start_index == -1:
                break

            start_index += len(start)

            end_index = text.find(end, start_index)

            if end_index == -1:
                break

            blocks.append(
                text[start_index:end_index].strip()
            )

            search_from = end_index + len(end)

        return blocks

    def _is_single_tool_action(
            self,
            code: str,
            tools_dict: dict[str, Any]
            ) -> bool:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return False

        # The fallback must contain exactly one Python statement.
        if len(tree.body) != 1:
            return False

        statement = tree.body[0]

        if not isinstance(statement, ast.Expr):
            return False

        call = statement.value

        if not isinstance(call, ast.Call):
            return False

        # Accept:
        # print(read_file(...))
        if (
            isinstance(call.func, ast.Name)
            and call.func.id == "print"
        ):
            if len(call.args) != 1 or call.keywords:
                return False

            call = call.args[0]

            if not isinstance(call, ast.Call):
                return False

        # Accept:
        # read_file(...)
        # run_tests()
        # edit_file(...)
        if not isinstance(call.func, ast.Name):
            return False

        tool_name = call.func.id

        if tool_name not in tools_dict:
            return False

        # Reject nested MCP calls.
        nested_tools = []

        for node in ast.walk(call):
            if node is call:
                continue

            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in tools_dict
            ):
                nested_tools.append(node.func.id)

        if nested_tools:
            return False

        return True

# if __name__ == "__main__":
#     tools = {
#         'read_file':
#         {'properties':
#          {'filepath': {'title': 'Filepath', 'type': 'string'},
#           'start_line': {'title': 'Start', 'type': 'integer'},
#           'end_line': {'title': 'End', 'type': 'integer'}},
#          'required': ['directory', 'pattern'],
#          'title': 'list_filesArguments',
#          'type': 'object'}}

#     llm_out = ('''<tool_call>
#                 read_file
#                 <parameter=filepath>
#                 /home/user/project/sympy/physics/vector/vector.py
#                 </parameter>
#                 <parameter=start_line>
#                 50
#                 </parameter>
#                 <parameter=end_line>
#                 80
#                 </parameter>
#                 </function>
#                 </tool_call>''')

#     ext = Extractor()
#     print(ext.extract_from_md(llm_out, tools))
#     # print(ext.extract_from_md("```python\nprint('hello')```", tools))
