from app.agents.base_agent import BaseAgent

class SpecialistAgentFactory:
    DEFAULT_ROLES={
        "pm":"Project Manager","design":"UI UX Designer","developer":"Software Developer","qa":"Quality Assurance",
        "researcher":"Research Specialist","proposal":"Proposal Specialist","ocr":"AI OCR Specialist","vlm":"VLM Specialist",
        "gis":"GIS Specialist","db":"DB Specialist","security":"Security Specialist"
    }
    DEFAULT_CAPABILITIES={
        "proposal":["proposal_writing","rfp_analysis","proposal_structure"],
        "ocr":["ocr","vlm","document_analysis","metadata_extraction"],
        "gis":["gis","spatial_data","coordinate_analysis"],
        "db":["database","sql","data_modeling","data_quality"],
        "security":["security","privacy","risk_assessment"]
    }
    def create(self,agent_id,name,specialty,model="gemma4:12b",role=None):
        return BaseAgent(agent_id=agent_id,name=name,role=role or self.DEFAULT_ROLES.get(specialty,specialty),model=model)
    def create_from_spec(self,spec):
        for k in ("agent_id","name","specialty"):
            if k not in spec: raise ValueError(f"Agent spec 누락: {k}")
        return self.create(**spec)
    def normalize_spec(self,spec):
        s=dict(spec); s.setdefault("capabilities",self.DEFAULT_CAPABILITIES.get(s.get("specialty"),[s.get("specialty","specialist")]))
        return s
