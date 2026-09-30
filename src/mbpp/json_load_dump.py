# pyright: reportMissingTypeStubs = false
from pathlib import Path
from src.mbpp.mbpp_models import MBPPTaskInput, SolutionOutput
import json


class JsonHandler():
    def task_loader(self, path_file: str) -> MBPPTaskInput:
        """
        Loads a task from a json file and returns it as a
        MBPPTaskInput object
        """
        path = Path(path_file)
        with open(path, "r") as file:
            data = json.load(file)

        task_input = MBPPTaskInput.model_validate(data)

        return task_input

    def solution_dump(self, solution: SolutionOutput, output:  str) -> None:

        output_path = Path(output)

        with output_path.open("w", encoding="utf-8") as file:
            file.write(solution.model_dump_json(indent=4))
