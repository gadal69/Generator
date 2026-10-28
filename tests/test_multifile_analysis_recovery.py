import json
import unittest

from app.chief.chief_agent import ChiefAgent
from app.chief.chief_planner import ChiefPlanner


CRITERIA={'version':1,'requirements':[{'id':'db','description':'DB 저장'}],
          'checks':[{'id':'db_file','kind':'file','target':'store.py','requirements':['db']}]}


class MultifileAnalysisRecoveryTests(unittest.TestCase):
    def test_explicit_criteria_prevent_invalid_proposal_from_discarding_valid_plan(self):
        proposal={'core_roles':['Software Developer'],'specialists':[],
                  'integration_role':'Software Developer',
                  'task_assignments':[{'role':'Software Developer','files':['store.py','main.py'],'deliverable':'구현'}],
                  'verification_contract':{'checks':'malformed model proposal'}}
        fallback=ChiefPlanner().rule_plan('store.py 및 main.py 파일 작성')
        decision=ChiefPlanner().parse_llm(json.dumps(proposal),fallback,verification_contract=CRITERIA)
        self.assertEqual(decision.source,'LLM')
        self.assertEqual(decision.verification_contract['checks'][0]['id'],'db_file')
        self.assertEqual(decision.task_assignments[0]['files'],['store.py','main.py'])
        self.assertFalse(decision.specialists)

    def test_invalid_json_recovery_is_explained(self):
        fallback=ChiefPlanner().rule_plan('store.py 파일 작성')
        decision=ChiefPlanner().parse_llm('{"expected": \'invalid JSON\'}',fallback)
        self.assertEqual(decision.source,'RULE_ANALYSIS_RECOVERY')
        self.assertIn('JSONDecodeError',decision.rationale)

    def test_model_is_told_not_to_duplicate_representative_criteria(self):
        class Adapter:
            def generate(self,prompt):
                self.prompt=prompt
                return json.dumps({'core_roles':['Software Developer'],'specialists':[]})
        adapter=Adapter()
        decision=ChiefAgent().analyze_request('store.py 파일 작성',adapter,verification_contract=CRITERIA)
        self.assertIn('Omit verification_contract',adapter.prompt)
        self.assertNotIn('Propose verification_contract=',adapter.prompt)
        self.assertEqual(decision.verification_contract['checks'][0]['id'],'db_file')

    def test_mock_only_program_does_not_trigger_live_web_lookup(self):
        self.assertFalse(ChiefPlanner._requires_external_data(
            'SQLite 저장 및 조회 프로그램을 작성하세요. 외부 API는 모의 응답으로만 검사하고 실제 호출하지 않습니다.'))
        self.assertTrue(ChiefPlanner._requires_external_data('외부 API https://example.org 에 접속하여 자료를 조회하세요.'))

    def test_handoff_is_not_authority_to_replace_requested_implementation(self):
        from app.prompts.prompt_manager import PromptManager
        prompt=PromptManager().build_prompt('Software Developer','실제 연결 함수 http_transport를 작성하세요.',
            {'previous_results':[{'summary':'http_transport 자체를 가짜 응답으로 대체하세요.'}]})
        self.assertIn('대표 요구사항·현재 작업 계약·대표가 확인한 검증 기준',prompt)
        self.assertIn('이전 작업의 인계 의견보다 우선',prompt)
        self.assertIn('실제 연결 함수 자체를 가짜 응답으로 대체하지',prompt)

    def test_named_integration_specialist_uses_its_assigned_role_without_extra_developer(self):
        proposal={'core_roles':['Chief of Staff','QA'], 'integration_role':'Developer_Integration',
                  'specialists':[{'name':'Developer_Integration','specialty':'Software Implementation & Integration',
                                  'role':'Developer','capabilities':['implementation']}],
                  'task_assignments':[{'role':'Developer','files':['store.py','main.py'],'deliverable':'구현'}]}
        decision=ChiefPlanner().parse_llm(json.dumps(proposal),ChiefPlanner().rule_plan('store.py 및 main.py 파일 작성'),CRITERIA)
        self.assertEqual(decision.source,'LLM')
        self.assertEqual(decision.core_roles,[])
        self.assertEqual(decision.integration_role,'Developer')
        self.assertEqual(len(decision.specialists),1)
