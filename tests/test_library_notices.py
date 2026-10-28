import unittest
from unittest.mock import patch
from importlib.metadata import PackageNotFoundError

from app.tools.dependency_preflight import inspect_request, normalize_libraries
from app.chief.chief_planner import ChiefPlanner
import json


class LibraryNoticesTests(unittest.TestCase):
    def test_image_request_fallback_explains_docker_scope(self):
        with patch('app.tools.dependency_preflight.version', side_effect=PackageNotFoundError):
            rows = inspect_request('이미지 수정 프로그램을 만들어줘', {})
        self.assertEqual(rows[0]['install_command'], 'python -m pip install Pillow')
        self.assertFalse(rows[0]['installed'])
        self.assertIn('별도 이미지', rows[0]['when_needed'])

    def test_model_dependencies_survive_parsing(self):
        planner = ChiefPlanner()
        fallback = planner.rule_plan('프로그램 작성')
        decision = planner.parse_llm(json.dumps({
            **fallback.to_dict(), 'library_requirements': [
                {'package': 'opencv-python', 'purpose': '영상 처리', 'runtime': 'python'}]
        }), fallback)
        self.assertEqual(decision.library_requirements[0]['package'], 'opencv-python')

    def test_reject_commands_urls_and_standard_library(self):
        items = [{'package': p} for p in ('Pillow;whoami', '--index-url', 'https://x/a', 'json')]
        self.assertEqual(normalize_libraries(items), [])

    def test_deduplicate_and_node_notice(self):
        rows = inspect_request('웹 프로그램', {'library_requirements': [
            {'package': 'express', 'runtime': 'node'},
            {'package': 'express', 'runtime': 'node'}]})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['install_command'], 'npm install express')
        self.assertFalse(rows[0]['installed'])
