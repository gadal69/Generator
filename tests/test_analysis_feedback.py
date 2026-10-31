import copy
import unittest
from app.chief.analysis_feedback import verification_feedback


class AnalysisFeedbackTests(unittest.TestCase):
    def test_copyable_draft_preserves_original_and_marks_proposals(self):
        from app.chief.analysis_feedback import reanalysis_draft
        decision = {'verification_contract': {}, 'acceptance_criteria': []}
        original = copy.deepcopy(decision)
        text = reanalysis_draft(decision, '웹 계산기 만들어 주세요. 소수점은 제외합니다.')
        self.assertIn('웹 계산기 만들어 주세요. 소수점은 제외합니다.', text)
        self.assertIn('대표 확인이 필요한 제안', text)
        self.assertIn('2+3=5', text)
        self.assertIn('원문과 충돌하는 내용은 제외', text)
        self.assertIn('다시 분석', text)
        self.assertEqual(decision, original)

    def test_missing_plan_explains_all_sections_without_mutating(self):
        decision = {'work_summary': '사칙연산 계산기', 'verification_contract': {}}
        original = copy.deepcopy(decision)
        text = '\n'.join(verification_feedback(decision))
        for word in ('요구사항 목록', '합격 기준', '검증 항목', '재분석 요청', '대표 확인 전 초안'):
            self.assertIn(word, text)
        self.assertEqual(decision, original)

    def test_incomplete_links_and_categories(self):
        decision = {'acceptance_criteria': ['결과 일치'], 'verification_contract': {
            'requirements': [{'id': 'R1', 'description': '입력 처리'}],
            'checks': [{'id': 'C1', 'requirements': ['R1'], 'category': 'normal'}]}}
        self.assertIn('입력 유형별 검사 부족', '\n'.join(verification_feedback(decision)))
        decision['verification_contract']['requirements'][0]['required_categories'] = ['normal']
        self.assertEqual(verification_feedback(decision), [])
        decision['verification_contract']['checks'][0]['enabled'] = False
        self.assertIn('연결된 필수 검증', '\n'.join(verification_feedback(decision)))

if __name__ == '__main__':
    unittest.main()
