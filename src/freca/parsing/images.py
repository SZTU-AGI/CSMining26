from __future__ import annotations

from pathlib import Path
from typing import Protocol


class VisionDescriber(Protocol):
    def describe(self, image_path: Path, *, context: str) -> str:
        """Return a neutral description without deciding compliance."""


def safe_description(describer: VisionDescriber | None, image_path: Path, context: str) -> str | None:
    if describer is None:
        return None
    description = describer.describe(image_path, context=context).strip()
    return description or None
