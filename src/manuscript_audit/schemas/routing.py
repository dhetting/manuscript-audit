from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ManuscriptClassification(BaseModel):
    pathway: Literal["math_stats_theory", "applied_stats", "data_science", "unknown"]
    paper_type: str
    evidence_types: list[str] = Field(default_factory=list)
    claim_types: list[str] = Field(default_factory=list)
    high_risk_features: list[str] = Field(default_factory=list)
    recommended_stack: Literal["minimal", "standard", "maximal"]


class ApplicabilityDecision(BaseModel):
    name: str
    applicable: bool
    rationale: str


class ModuleRoutingTable(BaseModel):
    route_version: str
    pathway: str
    paper_type: str
    recommended_stack: str
    modules: list[ApplicabilityDecision]


class DomainRoutingTable(BaseModel):
    route_version: str
    domains: list[ApplicabilityDecision]
