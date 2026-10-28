from dataclasses import dataclass, asdict
from typing import List
import re
from app.organization.agent_registry import AgentRegistry
from app.agents.specialists.specialist_factory import SpecialistAgentFactory
from app.chief.allocation_policy import developer_key

@dataclass
class TeamMemberSpec:
    agent_id: str
    project_id: str
    name: str
    role: str
    model: str
    agent_type: str
    capabilities: List[str]
    created_by: str
    parent_agent_id: str|None=None
    specialty: str|None=None

class TeamComposer:
    """Chief의 결정을 실제 프로젝트 실행 Team으로 변환한다."""
    def __init__(self, project_id, registry=None, factory=None, model="gemma4:12b"):
        self.project_id=project_id; self.registry=registry or AgentRegistry(); self.factory=factory or SpecialistAgentFactory(); self.model=model

    @staticmethod
    def _role_key(value):
        developer=developer_key(value)
        if developer:return developer
        compact=re.sub(r"[^a-z0-9가-힣]", "", str(value or "").casefold())
        groups={
            "software_developer":("softwaredeveloper","developer","programmer","coder","개발자","소프트웨어개발"),
            "design":("design","디자이너","디자인"),
            "data_engineering":("dataengineer","dataengineering","데이터엔지니어","데이터공학"),
            "research":("researchspecialist","researcher","research","조사전문가","자료조사","조사"),
            "database":("databasespecialist","database","dbspecialist","db전문가","데이터베이스"),
            "security":("securityspecialist","보안전문가"),
        }
        for key,tokens in groups.items():
            if any(token in compact for token in tokens):
                return key
        return compact

    def compose(self, core_roles, specialists, chief_id="AGT-CHIEF-001"):
        members=[]; used=set(); role_keys=set()
        short=self.project_id.replace("PRJ-","")[:8]
        aliases={"PM":("PM","Project Manager",["planning","requirements"]),
                 "DESIGN":("DESIGN","System/UI Designer",["design","ux","ui"]),
                 "DEV":("DEV","Software Developer",["implementation","integration"]),
                 "QA":("QA","Quality Assurance",["verification","quality"]),
                 "RESEARCH":("Research","Research Specialist",["research","analysis"])}
        for idx,requested_role in enumerate(core_roles,1):
            key=str(requested_role).strip()
            canonical=aliases.get(key.upper())
            name,role,default_caps=canonical if canonical else (key,key,[key.casefold().replace(" ","_")])
            if not key:
                continue
            role_key=self._role_key(key)
            if role_key in role_keys:
                continue
            role_keys.add(role_key)
            agent_id=f"AGT-{short}-CORE{idx:02d}-{re.sub(r'[^A-Z0-9]+','-',name.upper()).strip('-')[:28]}"
            if agent_id in used:
                continue
            used.add(agent_id)
            members.append(TeamMemberSpec(agent_id,self.project_id,name,role,self.model,"DYNAMIC_CORE",default_caps,chief_id,chief_id))
        for idx,s in enumerate(specialists,1):
            specialty=s.get("specialty") or s.get("name") or "specialist"
            name=s.get("name") or specialty.title()+" Specialist"
            role=s.get("role") or self.factory.DEFAULT_ROLES.get(specialty,specialty)
            caps=s.get("capabilities") or [specialty]
            role_key=self._role_key(" ".join((name,role,specialty)))
            if role_key in role_keys:
                continue
            role_keys.add(role_key)
            agent_id=f"AGT-{short}-SP{idx:02d}-{re.sub(r'[^A-Z0-9]+','-',name.upper()).strip('-')[:28]}"
            members.append(TeamMemberSpec(agent_id,self.project_id,name,role,self.model,"DYNAMIC_SPECIALIST",caps,chief_id,chief_id,specialty))
        return members
    @staticmethod
    def as_dicts(members): return [asdict(m) for m in members]
