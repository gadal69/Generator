import unittest
from unittest.mock import patch
from app.qa.verification_contract import build_contract,required_unverified,blocking_failure,scope_contract
from app.qa.project_verifier import ProjectVerifier


OUTPUT={'path':'result.png','format':'PNG','size':[2,2],'pixels':[{'xy':[0,0],'rgb':[255,0,0]}]}


class MediaTests(unittest.TestCase):
    def contract(self,kind='image_output',**kwargs):
        check=dict(id='edit',kind=kind,target='editor.py',argv=['python','editor.py'],outputs=[OUTPUT],**kwargs)
        return build_contract('',{'version':1,'requirements':[],'checks':[check]})

    def verify(self,data,contract=None):
        with patch('app.qa.project_verifier.IsolatedRunner.run',return_value={'status':'OBSERVED','observation':data}):
            return ProjectVerifier().verify([{'path':'editor.py','content':''}],contract or self.contract())

    def test_decoded_image_checks_content_dimensions_and_format(self):
        data={'exit_code':0,'images':[dict(path='result.png',exists=True,format='PNG',size=[2,2],pixels=[[255,0,0]])]}
        self.assertEqual(self.verify(data)[0]['status'],'PASS')
        for field,value in [('size',[1,2]),('format','JPEG'),('pixels',[[0,0,0]]),('exists',False),('error','corrupt')]:
            modified=dict(data,images=[dict(data['images'][0],**{field:value})])
            self.assertTrue(blocking_failure(self.verify(modified)),field)

    def test_missing_runtime_dependency_is_not_run(self):
        self.assertTrue(required_unverified(self.verify({'unavailable':'No module named PIL'})))

    def test_gui_compares_widget_and_does_not_claim_screen_review(self):
        c=self.contract('gui',factory='Window',actions=[{'action':'click','widget':'save'}],widgets=[{'widget':'status','expected':'saved'}])
        data={'steps':[{}],'widgets':{'status':'saved'},'images':[dict(exists=True,format='PNG',size=[2,2],pixels=[[255,0,0]])]}
        row=self.verify(data,c)[0]
        self.assertEqual((row['status'],row['screen_review'],row['native_dialogs']),('PASS','NOT_RUN','NOT_RUN'))
        self.assertTrue(blocking_failure(self.verify(dict(data,widgets={'status':'broken'}),c)))
        self.assertEqual(scope_contract(c,['editor.py'])['checks'],[])

    def test_required_screen_review_blocks_approval(self):
        c=build_contract('',{'version':1,'checks':[{'id':'screen','kind':'visual_review','target':'editor.py'}]})
        rows=ProjectVerifier().verify([{'path':'editor.py','content':''}],c)
        self.assertTrue(required_unverified(rows))

    def test_image_existence_alone_is_insufficient_and_paths_cannot_escape(self):
        for spec in [dict(OUTPUT,pixels=[]),dict(OUTPUT,path='../escape.png')]:
            with self.assertRaises(ValueError):
                build_contract('',{'version':1,'checks':[dict(id='x',kind='image_output',target='editor.py',argv=['python','editor.py'],outputs=[spec])]})


if __name__=='__main__':unittest.main()
