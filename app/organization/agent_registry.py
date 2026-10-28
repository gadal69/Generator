from dataclasses import dataclass, asdict
from typing import Dict, List

@dataclass(frozen=True)
class AgentDefinition:
    agent_id: str
    name: str
    role: str
    capabilities: List[str]
    description: str
    model_policy: str = "local"
    permanent: bool = True

class AgentRegistry:
    def __init__(self):
        # Agent instances are created per project from the Chief's decision.
        self._definitions: Dict[str, AgentDefinition] = {}

    def all(self): return list(self._definitions.values())
    def get(self, agent_id): return self._definitions.get(agent_id)
    def find_by_role(self, role):
        role=role.lower()
        return next((a for a in self._definitions.values() if a.role.lower()==role or a.name.lower()==role), None)
    def find_by_capability(self, capability):
        return [a for a in self._definitions.values() if capability in a.capabilities]
    def as_dicts(self): return [asdict(a) for a in self.all()]

class CapabilityRegistry:
    def __init__(self, agent_registry=None):
        self.agent_registry=agent_registry or AgentRegistry()
    def capabilities(self):
        return {a.agent_id:list(a.capabilities) for a in self.agent_registry.all()}
    def match(self, required):
        required=set(required or [])
        scored=[]
        for a in self.agent_registry.all():
            score=len(required.intersection(a.capabilities))
            if score: scored.append((score,a))
        return [a for _,a in sorted(scored,key=lambda x:(-x[0],x[1].name))]
