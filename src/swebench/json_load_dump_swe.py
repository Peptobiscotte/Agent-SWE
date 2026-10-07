# pyright: reportMissingTypeStubs = false
from pathlib import Path
from src.swebench.swe_models import SWEBenchTaskInput, SolutionOutput
import json
from typing import Any


class JsonHandler():
    def task_loader(self, path_file: str) -> Any:
        """
        Loads a task from a json file and returns it as a
        SWEBenchTaskInput object
        """
        path = Path(path_file)
        with open(path, "r") as file:
            data = json.load(file)

        task_input = SWEBenchTaskInput.model_validate(data)

        return task_input

    def solution_dump(self, solution: SolutionOutput, output:  str) -> None:

        output_path = Path(output)

        with output_path.open("w", encoding="utf-8") as file:
            file.write(solution.model_dump_json(indent=4))
