import os
import unittest
from unittest.mock import patch


class ApprovalRequestDisplayTests(unittest.TestCase):
    def test_analysis_approval_without_team_run_shows_project_request(self):
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        from qtpy import QtWidgets
        from app.gui import qt_dashboard
        from tests.test_universal_workflow import UniversalFlowTests
        fixture = UniversalFlowTests('test_no_verification_contract_does_not_auto_approve')
        fixture.setUp()
        window = None
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        try:
            request = '강아지 동영상 콘텐츠 제작 요청'
            project = fixture.o.create_project('Gemini 교차 분석 승인', request)
            approval = fixture.o.team_runner.approval_gate.request_cloud(
                project, 'AGT-CHIEF-001', 'Chief 요청 교차 검토',
                'chief_cross_review', '요청문', '미산정')
            with patch.object(qt_dashboard, 'make_database', return_value=fixture.o.database):
                window = qt_dashboard.Dashboard()
                with patch.object(QtWidgets.QMessageBox, 'information') as help_message:
                    window.help_button.click()
                    help_message.assert_called_once()
                    self.assertIn('새로고침', help_message.call_args.args[2])
                self.assertEqual(window.exit_button.text(), '종료')

                window._refresh_approvals()
                item = window._approval_details['cloud:' + approval]
                self.assertEqual(item['request'], request)
                self.assertEqual(item['status'], 'PENDING')
                window._approval_details['cloud:' + approval]['request'] = '\n'.join(str(i) for i in range(200))
                window._show_approval_result('cloud:' + approval)
                window.approval_result.verticalScrollBar().setValue(80)
                before = window.approval_result.verticalScrollBar().value()
                window._show_approval_result('cloud:' + approval)
                self.assertEqual(window.approval_result.verticalScrollBar().value(), before)
                window._refresh_approvals()

                window._run_succeeded({'mode': 'analyze', 'request': request,
                    'chief_decision': {'work_summary': request, 'rationale': '콘텐츠 제작 분석'},
                    'cloud_pending': True, 'gemini_review': True})
                self.assertIn('콘텐츠 제작 분석', window.decision_summary.toPlainText())
                self.assertIn('승인 대기', window.run_state.text())
                self.assertFalse(window.execute_button.isEnabled())
                self.assertIsNone(window.pending_analysis)
                from unittest.mock import Mock
                window.worker = Mock(mode='analyze')
                window.worker.isRunning.return_value = True
                window._stage_started = __import__('time').monotonic()
                window.run_button.setEnabled(True)
                window.run_button.setText('분석중')
                window.run_button.click()
                window.worker.request_cancel.assert_called_once()
                self.assertEqual(window.run_button.text(), '비서실장 업무 분석')
                window._run_succeeded({'mode': 'analyze', 'chief_decision': {'rationale': 'late result'}})
                self.assertIsNone(window.pending_analysis)
                window.worker.isRunning.return_value = False
                window.elapsed_timer = Mock()
                window._worker_finished()
                self.assertTrue(window.run_button.isEnabled())
                window.worker = None
                window._analysis_supplement = '대표가 확인할 보완문'
                def accept_supplement():
                    from qtpy import QtCore
                    dialog = app.activeModalWidget()
                    editor = dialog.findChild(QtWidgets.QPlainTextEdit)
                    editor.setPlainText('대표가 수정한 요구사항')
                    for button in dialog.findChildren(QtWidgets.QPushButton):
                        if button.text() == '수정한 문장 복사':
                            button.click()
                    self.assertEqual(app.clipboard().text(), '대표가 수정한 요구사항')
                    for button in dialog.findChildren(QtWidgets.QPushButton):
                        if button.text() == '확인한 문장 반영 후 재분석':
                            button.click()
                            break
                from qtpy import QtCore
                with patch.object(window, 'analyze_request') as reanalyze:
                    QtCore.QTimer.singleShot(0, accept_supplement)
                    window.review_analysis_supplement()
                    reanalyze.assert_called_once()
                self.assertEqual(window.request_input.toPlainText(), '대표가 수정한 요구사항')
                self.assertFalse(window.execute_button.isEnabled())

                window.request_input.setPlainText(request)
                window._analysis_cloud_approval_id = approval
                with patch.object(window, 'analyze_request') as restart:
                    window.decide_selected_approval('APPROVED', 'cloud:' + approval)
                    restart.assert_called_once()
                window._run_succeeded({'mode': 'analyze', 'request': request,
                    'chief_decision': {'work_summary': request, 'rationale': '검토 완료'}})
                self.assertTrue(window.execute_button.isEnabled())
                self.assertEqual(window.execute_button.text(), '분석 확인 후 실행')



                with fixture.o.database.connection() as conn:
                    conn.execute('UPDATE projects SET description=? WHERE project_id=?', ('   ', project))
                window._refresh_approvals()
                self.assertEqual(window._approval_details['cloud:' + approval]['request'], '업무 요청 내용 없음')
        finally:
            if window:
                window.refresh_timer.stop()
                window.close()
                app.processEvents()
            fixture.tearDown()

if __name__ == '__main__':
    unittest.main()
