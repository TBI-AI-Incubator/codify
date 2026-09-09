"""EU acquis framework, loads `data/frameworks/eu-acquis.yaml`."""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field

from codify.data_paths import data_dir

_STRICT = ConfigDict(extra="forbid", populate_by_name=True)

FRAMEWORKS_DIR = data_dir(__file__, "frameworks")


class AcquisCluster(BaseModel):
    model_config = _STRICT
    id: int | None
    name: str


class AcquisChapter(BaseModel):
    model_config = _STRICT
    number: Annotated[int, Field(ge=1, le=35)]
    title: str
    description: str
    cluster: int | None
    threshold_confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.70
    classifier_labels: list[str] = Field(default_factory=list)


class AcquisFramework(BaseModel):
    model_config = _STRICT
    clusters: list[AcquisCluster]
    classifier_priority: list[int]
    chapters: list[AcquisChapter]


@lru_cache(maxsize=1)
def load_acquis_framework() -> AcquisFramework:
    path = FRAMEWORKS_DIR / "eu-acquis.yaml"
    if not path.exists():
        raise FileNotFoundError(f"EU acquis YAML not found at {path}")
    framework = AcquisFramework.model_validate(yaml.safe_load(path.read_text()))
    chapter_numbers = {c.number for c in framework.chapters}
    priority_set = set(framework.classifier_priority)
    if priority_set != chapter_numbers or len(framework.classifier_priority) != len(priority_set):
        raise ValueError("classifier_priority out of sync with chapters")
    return framework


@lru_cache(maxsize=1)
def _priority_ordered_chapters() -> tuple[AcquisChapter, ...]:
    framework = load_acquis_framework()
    by_number = {c.number: c for c in framework.chapters}
    return tuple(by_number[n] for n in framework.classifier_priority)


def classify_directive(directory_labels: Iterable[str]) -> int | None:
    """EUR-Lex directory labels → acquis chapter (1-35); first priority hit wins."""
    lowered = [str(label).lower() for label in directory_labels if label]
    if not lowered:
        return None
    for chapter in _priority_ordered_chapters():
        for label in lowered:
            for needle in chapter.classifier_labels:
                if needle in label:
                    return chapter.number
    return None


def threshold_for_chapter(chapter_no: int | None) -> float:
    """Per-chapter `needs_review` gate; 0.70 fallback when unknown."""
    if chapter_no is None:
        return 0.70
    for chapter in load_acquis_framework().chapters:
        if chapter.number == chapter_no:
            return chapter.threshold_confidence
    return 0.70
