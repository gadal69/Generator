import unittest
from app.chief.chief_planner import ChiefPlanner
from app.chief.chief_decision import ChiefDecision
from app.chief.allocation_policy import enforce_allocation


class DomainAnalysisTests(unittest.TestCase):
    def test_domains(self):
        for request, expected in [('프로그램 개발', '개발'), ('영상 콘텐츠 제작', '콘텐츠'),
                ('시장 조사 기획', '기획·분석'), ('보고서 작성', '문서·사무'),
                ('데이터 통계', '데이터'), ('로고 디자인', '디자인'),
                ('일정 관리', '운영·관리'), ('상품 광고 마케팅', '마케팅·영업')]:
            with self.subTest(request=request):
                self.assertIn(expected, ChiefPlanner().rule_plan(request).work_domains)

    def test_video_not_generic_developer(self):
        d = enforce_allocation(ChiefDecision(core_roles=['Software Developer']), '강아지 동영상 콘텐츠 제작')
        self.assertFalse(d.integration_role)
        self.assertNotIn('Software Developer', d.core_roles)
        self.assertEqual(len(d.tool_requests), 2)
        self.assertTrue(all(t['alternative'] and t['status'] != 'READY' for t in d.tool_requests))
        self.assertFalse(d.cloud_ai_required)
        self.assertEqual(d.to_dict(), enforce_allocation(d, '강아지 동영상 콘텐츠 제작').to_dict())

    def test_mixed_code_and_content(self):
        d = ChiefPlanner().rule_plan('동영상 콘텐츠 제작 프로그램 개발')
        self.assertIn('개발', d.work_domains)
        self.assertIn('콘텐츠', d.work_domains)
        self.assertTrue(d.integration_role)

    def test_missing_requirements_and_promised_files(self):
        d = ChiefPlanner().rule_plan('계산기 만들어줘')
        self.assertFalse(d.requirements_ready)
        self.assertTrue(d.clarification_questions)
        d = ChiefPlanner().rule_plan('Python GUI 사칙연산 계산기 만들어줘')
        self.assertTrue(d.requirements_ready)
        d = enforce_allocation(ChiefDecision(task_assignments=[
            {'role': 'Software Developer', 'files': ['calculator.py'], 'deliverable': '계산기'}]), '계산기 만들어줘')
        self.assertTrue(d.file_creation_required)
        self.assertTrue(d.integration_role)
        from app.qa.artifact_validation import validate_artifacts
        self.assertTrue(validate_artifacts([], ['calculator.py'], '계산기'))

    def test_library_request(self):
        d = ChiefPlanner().rule_plan('엑셀 보고서 작성')
        self.assertTrue(any(x['package'] == 'openpyxl' for x in d.library_requirements))
        self.assertTrue(d.clarification_questions)

if __name__ == '__main__':
    unittest.main()
