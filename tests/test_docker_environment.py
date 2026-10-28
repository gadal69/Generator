import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app.qa.docker_environment import inspect_environment,resolve_docker
from app.qa.isolated_runner import IsolatedRunner
from scripts import setup_docker_verification as setup
from app.tools.dependency_preflight import inspect_verification_environment


class DockerEnvironmentTests(unittest.TestCase):
    def test_missing_docker_is_not_ready_and_does_not_run_commands(self):
        with patch('app.qa.docker_environment.resolve_docker',return_value=None),\
             patch('app.qa.docker_environment.subprocess.run') as run:
            state=inspect_environment()
        self.assertEqual(state['code'],'DOCKER_MISSING');run.assert_not_called()

    def test_engine_unavailable_is_distinct_from_missing_image(self):
        with patch('app.qa.docker_environment.resolve_docker',return_value='docker'),\
             patch('app.qa.docker_environment.subprocess.run',return_value=subprocess.CompletedProcess([],1,'','engine down')):
            self.assertEqual(inspect_environment()['code'],'ENGINE_UNAVAILABLE')

    def test_windows_containers_are_not_accepted(self):
        with patch('app.qa.docker_environment.resolve_docker',return_value='docker'),\
             patch('app.qa.docker_environment.subprocess.run',return_value=subprocess.CompletedProcess([],0,'windows\n','')) as run:
            self.assertEqual(inspect_environment()['code'],'LINUX_ENGINE_REQUIRED')
            self.assertEqual(run.call_count,1)

    def test_image_must_be_configured_and_present(self):
        linux=subprocess.CompletedProcess([],0,'linux\n','')
        with patch('app.qa.docker_environment.resolve_docker',return_value='docker'),\
             patch('app.qa.docker_environment.subprocess.run',return_value=linux):
            self.assertEqual(inspect_environment()['code'],'IMAGE_UNCONFIGURED')
        with patch('app.qa.docker_environment.resolve_docker',return_value='docker'),\
             patch('app.qa.docker_environment.subprocess.run',side_effect=[linux,subprocess.CompletedProcess([],1)]):
            self.assertEqual(inspect_environment({'images':{'python':'reviewed'}})['code'],'IMAGE_MISSING')

    def test_ready_engine_and_image_do_not_pull_anything(self):
        with patch('app.qa.docker_environment.resolve_docker',return_value='docker'),\
             patch('app.qa.docker_environment.subprocess.run',side_effect=[subprocess.CompletedProcess([],0,'linux\n',''),subprocess.CompletedProcess([],0)]) as run:
            available,reason=IsolatedRunner({'images':{'python':'reviewed'}}).available('python')
        self.assertEqual(available,('docker','reviewed'));self.assertIsNone(reason)
        self.assertEqual([call.args[0][1] for call in run.call_args_list],['info','image'])

    def test_fresh_windows_install_is_found_without_restarting_process(self):
        with patch('app.qa.docker_environment.os.name','nt'),\
             patch('app.qa.docker_environment.shutil.which',return_value=None),\
             patch('app.qa.docker_environment.Path.is_file',return_value=True):
            self.assertTrue(resolve_docker().endswith('docker.exe'))

    def test_failed_smoke_does_not_activate_image(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.json';original='{"ai":{"default_model":"unchanged"},"verification":{"images":{}}}'
            path.write_text(original,encoding='utf-8')
            with patch.object(setup,'inspect_environment',return_value={'status':'READY','docker':'docker'}),\
                 patch.object(setup.subprocess,'run',side_effect=[subprocess.CompletedProcess([],0),subprocess.CompletedProcess([],0,'sha256:'+'a'*64)]),\
                 patch.object(setup,'smoke',side_effect=RuntimeError('not verified')):
                with self.assertRaisesRegex(RuntimeError,'not verified'):setup.prepare(path,True)
            self.assertEqual(path.read_text(encoding='utf-8'),original)

    def test_successful_smoke_activates_immutable_id_preserving_other_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.json';path.write_text(json.dumps({'ai':{'default_model':'unchanged'},'verification':{'images':{'node':'existing-node'}}}),encoding='utf-8')
            image='sha256:'+'a'*64
            with patch.object(setup,'inspect_environment',return_value={'status':'READY','docker':'docker'}),\
                 patch.object(setup.subprocess,'run',side_effect=[subprocess.CompletedProcess([],0),subprocess.CompletedProcess([],0,image)]),\
                 patch.object(setup,'smoke',return_value=[{'status':'PASS'},{'status':'PASS'}]):
                result=setup.prepare(path,True)
            config=json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(result['code'],'CONFIGURED');self.assertEqual(config['verification']['images']['python'],image)
            self.assertEqual(config['verification']['images']['node'],'existing-node');self.assertEqual(config['ai']['default_model'],'unchanged')

    def test_setup_does_not_build_if_prerequisites_are_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.json';path.write_text('{}',encoding='utf-8')
            with patch.object(setup,'inspect_environment',return_value={'status':'NOT_READY','code':'DOCKER_MISSING'}),\
                 patch.object(setup.subprocess,'run') as run:
                self.assertEqual(setup.prepare(path,True)['status'],'NOT_READY');run.assert_not_called()
            self.assertEqual(path.read_text(encoding='utf-8'),'{}')

    def test_analysis_explains_missing_runtime_without_blocking_code_creation(self):
        with patch('app.qa.docker_environment.resolve_docker',return_value=None):
            rows=inspect_verification_environment({'checks':[{'kind':'command','runtime':'python'},
                {'kind':'python_function','engine':'docker','runtime':'python'}]})
        self.assertEqual(len(rows),1);self.assertFalse(rows[0]['installed']);self.assertFalse(rows[0]['blocking'])
        self.assertIn('Docker',rows[0]['when_needed']);self.assertIn('--build',rows[0]['install_command'])

    def test_pure_function_analysis_does_not_probe_docker(self):
        with patch('app.qa.docker_environment.inspect_environment') as probe:
            self.assertEqual(inspect_verification_environment({'checks':[{'kind':'python_function'}]}),[])
            probe.assert_not_called()


if __name__=='__main__':unittest.main()
