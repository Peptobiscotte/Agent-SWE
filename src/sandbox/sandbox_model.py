# pyright: reportUnknownParameterType = false
# pyright: reportMissingTypeArgument = false
# pyright: reportUnknownMemberType = false
# pyright: reportUnknownVariableType = false
# pyright: reportMissingTypeStubs = false
from io import StringIO
from contextlib import redirect_stdout, redirect_stderr
from typing import Any, Mapping, Sequence, Callable, IO
from types import ModuleType
from src.sandbox.sandbox_config import SandboxConfig, FinalAnswerException
from multiprocessing import Queue
from multiprocessing.connection import Connection
from pathlib import Path
import sys
import resource
import traceback


class StreamingBuffer(StringIO):
    def __init__(self, partial_conn: Connection) -> None:
        super().__init__()
        self.partial_conn = partial_conn

    def write(self, text: str) -> int:
        written = super().write(text)

        if text:
            self.partial_conn.send(text)

        return written


class Sandbox():
    def __init__(self, config: SandboxConfig) -> None:
        self.config = config
        # here self.module_import is the function to make an import not
        # the imported modules.
        #
        # When exec() is called, if it runs into an import
        # instruction, the interpreter looks for the builtin __import__
        # and runs self.module_import() with the import name value.
        # it then adds the imported module to our 'global' namespace.
        #
        # The default builtin configuration for imports is:
        # "__import__": __import__ so the native function for imports,
        # here we only specify to this function what we filter.
        # the same behavior applies to open
        #
        # final_answer is a tool exposed by the sandbox not the MCP serv
        # we define it here so it is always available
        self.last_tool_result: Any = None
        self.last_sandbox_output = ""
        self.namespace: dict[str, Any] = {
            "final_answer": self.final_answer,
            "__builtins__": {
                "open": self.open_authorized,
                "__import__": self.module_import,
                "final_answer": self.final_answer,
                "bool": bool,
                "int": int,
                "float": float,
                "complex": complex,
                "str": str,
                "bytes": bytes,
                "bytearray": bytearray,
                "list": list,
                "tuple": tuple,
                "dict": dict,
                "set": set,
                "frozenset": frozenset,
                "range": range,
                "enumerate": enumerate,
                "zip": zip,
                "map": map,
                "filter": filter,
                "print": print,
                "iter": iter,
                "next": next,
                "reversed": reversed,
                "sorted": sorted,
                "len": len,
                "sum": sum,
                "min": min,
                "max": max,
                "abs": abs,
                "round": round,
                "pow": pow,
                "MemoryError": MemoryError,
                "divmod": divmod,
                "chr": chr,
                "ord": ord,
                "bin": bin,
                "oct": oct,
                "hex": hex,
                "isinstance": isinstance,
                "issubclass": issubclass,
                "callable": callable,
                "slice": slice,
                "repr": repr,
                "format": format,
                "dir": dir,
                "Exception": Exception,
                "ZeroDivisionError": ZeroDivisionError,
                "ImportError": ImportError,
                "PermissionError": PermissionError,
                "NameError": NameError,
                "AttributeError": AttributeError,
                "AssertionError": AssertionError,
                "ValueError": ValueError,
                "TypeError": TypeError,
                "KeyError": KeyError,
                "IndexError": IndexError,
                "RuntimeError": RuntimeError,
                "StopIteration": StopIteration,
                "all": all,
                "any": any,
            }
        }

    def authorized_imports(self, name: str) -> bool:
        # Simply returns True if the name of the module
        # the interpreter is trying to import is in our list
        # of authorized modules else False
        if any(
            name == import_name for
            import_name in self.config.authorized_imports
        ):
            return True
        if "." in name:
            cut = name[:name.find(".") + 1]
            for import_name in self.config.authorized_imports:
                if import_name.endswith(".*"):
                    cut_import = import_name[:import_name.find(".") + 1]
                    if cut == cut_import:
                        return True
        return False

    def module_import(
            self,
            name: str,
            globals: Mapping[str, object] | None = None,
            locals: Mapping[str, object] | None = None,
            fromlist: Sequence[str] | None = (),
            level: int = 0
    ) -> ModuleType:
        """
        Function to specify which type of import is available
        Args:
            name,
            globals,
        Return:
            etc...
        """
        # returns the import function with the authorized name
        # of the module to import while the interpreter is running
        if self.authorized_imports(name):
            return __import__(
                name,
                globals,
                locals,
                fromlist,
                level
            )
        else:
            raise ImportError(f"Module {name} not authorized")

    def authorized_path(self, path: str) -> bool:
        real_path = Path(path).resolve()
        for dir in self.config.allowed_directories:
            dir_allowed = Path(dir)
            if real_path == dir_allowed:
                return True
            elif dir_allowed in real_path.parents:
                return True
        return False

    def open_authorized(
            self,
            file: str,
            mode: str = 'r',
            buffering: int = -1,
            encoding: str | None = None,
            errors: str | None = None,
            newline: str | None = None,
            closefd: bool = True,
            opener: Callable[[str, int], int] | None = None
    ) -> IO | None:
        if opener is not None:
            raise PermissionError("Opener is not allowed")
        if self.authorized_path(file):
            return open(
                file,
                mode,
                buffering,
                encoding,
                errors,
                newline,
                closefd,
                opener
            )
        raise PermissionError(f"Forbidden path {file}")

    def make_proxy(self, name: str, child_conn: Connection):
        def proxy(**kwargs: Any):
            instructions = {
                "type": "tool_call",
                "tool": name,
                "args": kwargs
            }
            child_conn.send(instructions)
            response = child_conn.recv()
            if response["status"] == "error":
                raise RuntimeError(response["result"])
            self.last_tool_result = response["result"]
            return response["result"]
        return proxy

    def worker(
            self,
            code: str,
            queue: Queue,
            tools: list[str],
            child_conn: Connection,
            partial_conn: Any = None
    ) -> None:
        # creates the wrapper for the MCP tools
        # we pass the proxy for each tool to our sandbox namespace
        # so the llm can call the function even though they won't run
        # in the sandbox
        for name in tools:
            self.namespace[name] = self.make_proxy(name, child_conn)
        # utils function to add the result of exec to our Queue
        # Queue is a process queue allowing communications between
        # multiple running processes (here the parent and the child)
        #
        # simply set ram limits using builtin resource module,
        # * 1024 * 1024 because setrlimit limits argument wants
        # bytes and our limit is expossed in mb so bytes * kb * mb
        resource.setrlimit(
            resource.RLIMIT_AS,
            (self.config.max_memory_mb * 1024 * 1024,
             self.config.max_memory_mb * 1024 * 1024)
        )
        try:
            res = self.execute(code, partial_conn)
            if ('File "<sandbox>"' in res or
                    "['timeout'" in res or "['error" in res):
                queue.put(("error", res, self.last_sandbox_output))
                return
            queue.put(("output", res, self.last_sandbox_output))
        except FinalAnswerException as e:
            queue.put(("final", e.code, self.last_sandbox_output))

    def execute(self, code: str, partial_conn: Any = None) -> str:
        # exec the code and redirects all outputs (out and err)
        # to buffer *file*, returning its value
        # doing so assures no print on terminal during execution
        # and allows persistance of printed datas
        if partial_conn is not None:
            buffer = StreamingBuffer(partial_conn)
        else:
            buffer = StringIO()
        buffer_err = StringIO()
        try:
            self.last_tool_result = None
            self.last_sandbox_output = ""
            with redirect_stdout(buffer):
                # we use exec to run code as str
                # we use compile to add our sandbox name for traceback
                exec(compile(code, "<sandbox>", "exec"), self.namespace)

            output = buffer.getvalue()
            self.last_sandbox_output = output
            if self.last_tool_result is not None:
                return str(self.last_tool_result)
            if output:
                return output
            return ""
        except FinalAnswerException:
            self.last_sandbox_output = buffer.getvalue()
            raise
        except Exception as e:
            with redirect_stderr(buffer_err):
                # we only want to extract the error line from the trace
                extract = traceback.format_exc()
                for lines in extract.splitlines():
                    if 'File "<sandbox>"' in lines:
                        extract = lines
                print(
                    f"{type(e).__name__}: {e}\n{extract}",
                    file=sys.stderr
                    )
            self.last_sandbox_output = (
                buffer.getvalue()
                + buffer_err.getvalue()
            )
            return self.last_sandbox_output

    def final_answer(self, code: str) -> None:
        # we make final_answer an 'error' so that it instantly stops
        # the execution in case it is not the end of the code block
        # sent by the llm, we can then catch the Exception.
        raise FinalAnswerException(code)
