"""The shape of generated content.

Kept in its own module so the grounding gate and the generation pipeline can
both depend on it without depending on each other.

`source` records how a station's content came to exist. It is written into the
tour, because "a model wrote this and it passed the gate" and "the gate
rejected the model twice and this is structural fallback" are different claims
and the reader is entitled to know which one they are reading.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Provenance(StrEnum):
    MODEL = "model"
    MODEL_RETRY = "model_retry"
    STRUCTURAL = "structural"


class AnswerLocation(BaseModel):
    """Where the answer to a question lives. Line numbers are 1-based."""

    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class Question(BaseModel):
    """A verification question and the location of its answer."""

    prompt: str = Field(min_length=1)
    answer_location: AnswerLocation


class StationDraft(BaseModel):
    """Exactly what the model is asked to return — and nothing more.

    The station's own path and its provenance are facts the pipeline already
    knows. Asking the model to repeat them would invite it to get them wrong,
    so the draft omits them and the pipeline fills them in.
    """

    summary: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    questions: list[Question]


class StationExplanation(BaseModel):
    """Generated content for one station."""

    path: str
    summary: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    questions: list[Question]
    provenance: Provenance = Provenance.MODEL

    @classmethod
    def from_draft(
        cls, draft: StationDraft, path: str, provenance: Provenance
    ) -> StationExplanation:
        return cls(
            path=path,
            summary=draft.summary,
            why_it_matters=draft.why_it_matters,
            questions=draft.questions,
            provenance=provenance,
        )
