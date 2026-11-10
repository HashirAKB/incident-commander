"""Structured outputs requested from the model (triage and synthesis).

Action arguments travel as a JSON string (`args_json`) rather than a free-form
object: strict structured-output schemas need every object to declare its
properties, and each WRITE tool has different ones. The synthesizer node
parses and validates the string against the tool's real schema.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from incident_commander.state import Category, InvestigatorName, RootCause, Severity


class TriageOutput(BaseModel):
    category: Category
    severity: Severity
    investigators: list[InvestigatorName] = Field(description="Which investigators to run; metrics always runs")
    rationale: str


class ActionProposal(BaseModel):
    tool: str = Field(description="Name of a WRITE tool: create_incident or restart_device")
    args_json: str = Field(description="The tool arguments as a JSON object string")
    rationale: str
    risk: str


class SynthesisOutput(BaseModel):
    root_cause: RootCause
    confidence: float = Field(ge=0, le=1)
    summary: str
    evidence: list[str] = Field(description="Finding ids such as F-metrics that support the diagnosis")
    actions: list[ActionProposal] = Field(description="Proposed write actions; each needs human approval")
