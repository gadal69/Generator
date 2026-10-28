import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app.qa import project_environment as env
from app.qa.isolated_runner import IsolatedRunner
from app.qa.project_verifier import ProjectVerifier
from app.qa.verification_contract import required_unverified
from app.runtime.run_control import RunCancelled, RunControl
from app.tools.dependency_preflight import normalize_libraries


BASE = 'sha256:' + 'a'*64
IMAGE = 'sha256:' + 'b'*64
LIBS = [{'package':'Pillow', 'version':'11.3.0', 'runtime':'python', 'purpose':'편집'}]
REPORT = {'checked':[{'package':'Pillow','version':'11.3.0','imports':['PIL']}],
          'resolved':[{'package':'Pillow','version':'11.3.0'}], 'pip_check':'PASS'}


class ProjectEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = {'images':{'python':BASE}, 'other':'preserved'}
        self.folder = self.root/'projects'/'PRJ-ABCD1234'/'environment'

    def tearDown(self): self.temp.cleanup()

    def build(self, argv, timeout, control=None):
        if argv[1]=='image': return ''
        self.assertEqual(argv[1], 'build')
        context=Path(argv[-1])
        self.assertEqual({p.name for p in context.iterdir()}, {'Dockerfile','requirements.txt'})
        self.assertIn('--only-binary=:all:', (context/'Dockerfile').read_text())
        self.assertEqual((context/'requirements.txt').read_text(), 'Pillow==11.3.0\n')
        Path(argv[argv.index('--iidfile')+1]).write_text(IMAGE)
        return ''

    def prepare(self, libraries=LIBS):
        return env.prepare_project(self.root, 'PRJ-ABCD1234', libraries, self.settings)

    @patch.object(env, 'smoke', return_value=REPORT)
    @patch.object(env, 'inspect_environment', return_value={'status':'READY','docker':'docker','image':BASE})
    def test_only_activate_after_install_and_smoke_and_preserve_global(self, inspect, smoke):
        with patch.object(env, '_command', side_effect=self.build):
            settings, record=self.prepare()
        self.assertEqual(record['status'],'READY')
        self.assertEqual(settings['images']['python'],IMAGE)
        self.assertEqual(self.settings['images']['python'],BASE)
        self.assertEqual(settings['other'],'preserved')
        self.assertEqual((self.folder/'requirements.lock.txt').read_text(),'Pillow==11.3.0\n')
        self.assertEqual(json.loads((self.folder/'dependencies.json').read_text(encoding='utf-8'))['image'],IMAGE)
        smoke.assert_called_once()

    @patch.object(env, 'inspect_environment', return_value={'status':'READY','docker':'docker','image':BASE})
    def test_install_failure_never_uses_default_image(self, inspect):
        with patch.object(env, '_command', side_effect=RuntimeError('No matching distribution found')):
            settings, record=self.prepare()
        self.assertEqual(settings['images'],{})
        self.assertIn('No matching distribution', record['reason'])
        self.assertIn('wheel',record['remedy'])
        with patch('app.qa.isolated_runner.inspect_environment') as probe:
            self.assertIsNone(IsolatedRunner(settings).available('python')[0])
            probe.assert_not_called()

    @patch.object(env, 'inspect_environment', return_value={'status':'READY','docker':'docker','image':BASE})
    def test_smoke_failure_never_activates_image(self, inspect):
        with patch.object(env,'_command',side_effect=self.build), patch.object(env,'smoke',side_effect=RuntimeError('import failed')):
            settings,record=self.prepare()
        self.assertEqual(record['status'],'NOT_READY')
        self.assertNotIn('python',settings['images'])
        self.assertEqual((self.folder/'requirements.lock.txt').read_text(),'')

    def test_no_packages_needs_no_docker(self):
        with patch.object(env,'inspect_environment') as inspect:
            settings,record=self.prepare([])
        self.assertEqual(record['status'],'NOT_NEEDED')
        inspect.assert_not_called()
        self.assertEqual(settings,self.settings)

    def test_qt_packages_inherit_reviewed_gui_base_and_keep_project_image(self):
        self.settings['images']['gui']=BASE
        libs=[{'package':'PySide6','version':'6.8.3','runtime':'python'}]
        report={'checked':[{'package':'PySide6','version':'6.8.3'}],
                'resolved':[{'package':'PySide6','version':'6.8.3'}],'pip_check':'PASS'}
        def command(argv,timeout,control=None):
            if argv[1]=='build':Path(argv[argv.index('--iidfile')+1]).write_text(IMAGE)
            return ''
        with patch.object(env,'inspect_environment',return_value={'status':'READY','docker':'docker','image':BASE}) as inspect,\
             patch.object(env,'_command',side_effect=command),patch.object(env,'smoke',return_value=report):
            settings,record=self.prepare(libs)
        self.assertEqual(inspect.call_args_list[0].args[1],'gui')
        self.assertEqual(record['status'],'READY')
        self.assertEqual(settings['images']['gui'],IMAGE)
        self.assertEqual(settings['images']['python'],IMAGE)

    def test_invalid_versions_and_unsupported_runtime_fail_closed(self):
        for libraries in ([{'package':'Pillow','version':'1;whoami'}], [{'package':'express','runtime':'node'}]):
            with self.subTest(libraries=libraries), patch.object(env,'_command') as command:
                settings,record=self.prepare(libraries)
                self.assertEqual(record['status'],'NOT_READY')
                self.assertEqual(settings['images'],{})
                command.assert_not_called()

    @patch.object(env, 'smoke', return_value=REPORT)
    @patch.object(env, 'inspect_environment', return_value={'status':'READY','docker':'docker','image':BASE})
    def test_reuse_requires_matching_input_and_rechecks_imports(self, inspect, smoke):
        with patch.object(env,'_command',side_effect=self.build) as command:
            self.prepare()
            self.prepare()
        self.assertEqual(sum(call.args[0][1]=='build' for call in command.call_args_list),1)
        self.assertEqual(smoke.call_count,2)

    @patch.object(env, 'inspect_environment', return_value={'status':'READY','docker':'docker','image':BASE})
    def test_cancel_persists_and_propagates(self, inspect):
        with patch.object(env,'_command',side_effect=RunCancelled('stop')):
            with self.assertRaises(RunCancelled): self.prepare()
        self.assertEqual(json.loads((self.folder/'dependencies.json').read_text(encoding='utf-8'))['status'],'CANCELLED')

    def test_environment_failure_blocks_approval_even_if_static_check_passes(self):
        rows=ProjectVerifier({'project_dependencies':{'status':'NOT_READY','reason':'install failed'}}).verify(
            [{'path':'x.py','content':'pass'}], {'source':'USER','checks':[
                {'id':'exists','kind':'file','target':'x.py'}]})
        self.assertTrue(required_unverified(rows))
        self.assertEqual(rows[0]['type'],'DEPENDENCIES')

    def test_version_is_preserved_and_cannot_be_shell_syntax(self):
        self.assertEqual(normalize_libraries(LIBS)[0]['version'],'11.3.0')
        self.assertEqual(normalize_libraries([{'package':'Pillow','version':'==11.3.0\nfoo'}]),[])

    def test_project_path_validation(self):
        with self.assertRaises(ValueError): env.prepare_project(self.root,'../outside',[],{})
