import json
from pathlib import Path
import tempfile
import unittest
from nightly.progress import summarize,read_progress

class ProgressTests(unittest.TestCase):
    def test_real_synthesis_steps(self):
        p=summarize('semantic_done','[YuE2] Running Synthesizing audio: 16/32 steps (50%)')
        self.assertEqual((p['phase'],p['current'],p['total']),('synthesis',16,32))
        self.assertIn('曲谱已完成',p['completed'])
    def test_no_percentage_for_token_budgets(self):
        for stage in ('abc:500','semantic:2000'):
            self.assertIsNone(summarize(stage)['total'])
    def test_decode_not_stale_synthesis(self):
        p=summarize('synthesized','[YuE2] Running Decoding audio: 7/20 chunks')
        self.assertEqual((p['phase'],p['current'],p['total']),('decode',7,20))
    def test_direct_mode_does_not_claim_score(self):
        self.assertEqual(summarize('semantic:50',direct=True)['completed'],['曲谱规划已跳过（直接生成）'])
    def test_storage_overrides_old_model_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);out=folder/'output';out.mkdir()
            (out/'progress.json').write_text(json.dumps([{'stage':'complete'}]))
            (out/'storage-progress.json').write_text(json.dumps(dict(phase='mp3',text='正在转为 MP3',current=30,total=120)))
            job=dict(status='running',request={})
            p=read_progress(job,folder)
            self.assertEqual((p['phase'],p['current'],p['total']),('mp3',30,120))
            job['status']='failed';self.assertEqual(read_progress(job,folder)['phase'],'failed')

if __name__=='__main__':unittest.main()
