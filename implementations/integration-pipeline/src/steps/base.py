from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from ..pipeline.pipeline import Pipeline, TargetContext


class Step(ABC):
    step_names: Tuple[str, ...] = ()

    def __init__(self, pipeline: Pipeline) -> None:
        self.pipeline = pipeline

    def should_run(self) -> bool:
        return self.pipeline.args.step in self.step_names or self.pipeline.args.step == "all"

    @abstractmethod
    def run(self, ctx: TargetContext) -> bool:
        return True
