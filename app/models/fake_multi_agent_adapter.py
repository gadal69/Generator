class FakeMultiAgentAdapter:
    """Offline deterministic adapter used by the GUI self-test and tests."""

    proposal_calls = 0

    def __init__(self, model):
        self.model = model

    def generate(self, prompt, images=None):
        upper = str(prompt).upper()
        if "VERDICT:" in upper and ("QA AGENT" in upper or "요구사항과 결과물을 검증" in upper):
            if "[QA:FAIL]" in upper:
                return "VERDICT: FAIL\nQA found a deliberate test defect."
            return "VERDICT: PASS\nQA passed."
        if "PROPOSAL SPECIALIST" in upper:
            type(self).proposal_calls += 1
            if type(self).proposal_calls == 1:
                return "[Summary]\nProposal draft\n[QA:FAIL] deliberate first-pass defect"
            return "[Summary]\nFinal proposal output after rework"
        if "SOFTWARE DEVELOPER" in upper:
            return "[Summary]\nImplementation result"
        if "RESEARCH SPECIALIST" in upper:
            return "[Summary]\nRequirements and constraints analyzed"
        if "PROJECT MANAGER" in upper:
            return "[Summary]\nExecution plan created"
        return "[Summary]\nGeneric agent result"
