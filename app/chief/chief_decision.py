from dataclasses import dataclass, asdict, field
from typing import List, Dict

@dataclass
class ChiefDecision:
    core_roles: List[str] = field(default_factory=list)
    specialists: List[Dict] = field(default_factory=list)
    required_capabilities: List[str] = field(default_factory=list)
    rationale: str = ""
    priority: str = "NORMAL"
    cloud_ai_required: bool = False
    cloud_purpose: str = ""
    cloud_scope: str = ""
    cloud_data_scope: str = ""
    estimated_cost: str = "0"
    source: str = "RULE"
    execution_mode: str = "local"
    external_data_access: bool = False
    external_data_plan: Dict = field(default_factory=dict)
    runtime_requirements: List[Dict] = field(default_factory=list)
    library_requirements: List[Dict] = field(default_factory=list)
    workflow_type: str = "standard"
    automation: Dict = field(default_factory=dict)
    file_creation_required: bool = False
    direct_execution: bool = False
    work_summary: str = ""
    work_steps: List[str] = field(default_factory=list)
    model_reviews: Dict = field(default_factory=dict)
    comparison_summary: str = ""
    task_assignments: List[Dict] = field(default_factory=list)
    acceptance_criteria: List[str] = field(default_factory=list)
    verification_contract: Dict = field(default_factory=dict)
    verification_plan_required: bool = False
    verification_plan_attempts: List[Dict] = field(default_factory=list)
    analysis_issues: List[Dict] = field(default_factory=list)
    requirements_ready: bool = True
    work_domains: List[str] = field(default_factory=list)
    tool_requests: List[Dict] = field(default_factory=list)
    clarification_questions: List[str] = field(default_factory=list)
    integration_role: str = ""
    def to_dict(self): return asdict(self)
