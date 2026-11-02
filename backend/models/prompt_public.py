"""Developer prompt catalogue and edit contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictStr


class PromptCatalogueEntry(BaseModel):
    id: str
    feature: str
    placeholders: list[str]
    required_literals: list[str]
    default_text: str
    override_text: str | None
    status: Literal["default", "overridden", "default changed", "invalid"]


class PromptOverrideUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: StrictStr
