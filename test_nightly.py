"""Behavior checks without loading YuE2 or creating real songs."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent


class NightlyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='nightly-test-data-',dir=ROOT)
        os.environ['YUE2_STUDIO_DATA_DIR']=Path(cls.temp.name).name
        import studio_server
        cls.studio=studio_server
        cls.stub=patch.object(studio_server,'run_job',lambda job: None);cls.stub.start()
        cls.busy=patch.object(studio_server,'external_busy',lambda:False);cls.busy.start()
        cls.server=studio_server.StudioHTTPServer(('127.0.0.1',0),studio_server.Handler)
        cls.base='http://127.0.0.1:'+str(cls.server.server_port)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()
        cls.stub.stop();cls.busy.stop();cls.temp.cleanup()

    def request(self,path,body=None,token=True):
        headers={'Origin':self.base,'X-Studio-Token':self.studio.TOKEN if token else 'bad','Content-Type':'application/json'}
        req=urllib.request.Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:
            with urllib.request.urlopen(req) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as e:return e.code,json.load(e)

    def test_browser_reconnect_burst(self):
        from concurrent.futures import ThreadPoolExecutor
        barrier=threading.Barrier(16)
        def fetch(_):
            barrier.wait(timeout=5)
            with urllib.request.urlopen(self.base+'/library',timeout=3) as response:
                return response.status, b'id="audio"' in response.read()
        with ThreadPoolExecutor(max_workers=16) as pool:
            results=list(pool.map(fetch,range(16)))
        self.assertEqual(results,[(200,True)]*16)

    def test_collection_http_auth_delete_and_idempotency(self):
        key='ffffffffffffffff';folder=Path(self.temp.name)/key/'output';folder.mkdir(parents=True,exist_ok=True)
        (folder/'audio.mp3').write_bytes(b'ID3disposable');(folder/'latent.npy').write_bytes(b'intermediate')
        self.studio.JOBS[key]=dict(id=key,status='completed',created_at=1,request={'lyrics':'测试'},
            metadata={'title':'删除接口测试歌','batch':'2098-01-01','slot':'pop-female','genre':'pop','voice':'female'},client_key='deleted-slot')
        url='/api/library/tracks/'+key
        self.assertEqual(self.request(url+'/favorite',{'favorite':True},token=False)[0],403)
        self.assertEqual(self.request(url+'/favorite',{'favorite':True})[0],200)
        self.assertTrue(next(t for t in self.request('/api/library')[1]['tracks'] if t['id']==key)['favorite'])
        self.assertEqual(self.request(url+'/delete',{'confirm_title':'错误歌名'})[0],409)
        self.assertEqual(self.request(url+'/delete',{'confirm_title':'删除接口测试歌'})[0],200)
        self.assertFalse(folder.parent.exists())
        with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(self.base+'/media/'+key+'/audio.mp3')
        self.assertEqual(error.exception.code,404)
        body={'lyrics':'测试','style':'Mandarin pop','client_key':'deleted-slot', 'metadata':{'batch':'2098-01-01','slot':'pop-female'}}
        self.assertEqual(self.request('/api/jobs',body)[0],410)
        self.assertEqual(self.request(url+'/delete',{'confirm_title':'删除接口测试歌'})[0],200)

    def test_pipeline_cli_imports_from_outside_project(self):
        import subprocess,sys
        with tempfile.TemporaryDirectory() as workdir:
            result=subprocess.run([sys.executable,str(ROOT/'nightly/pipeline.py'),'status',
                '--manifest',str(ROOT/'nightly-data/2098-01-01/manifest.json')],cwd=workdir,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('status',json.loads(result.stdout))

    def test_idempotent_submission_and_conflicts(self):
        self.studio.JOBS.clear();self.studio.ACTIVE=None
        body=dict(lyrics='[Verse]\n测试歌词不运行推理',style='Mandarin folk',client_key='test-folk-male-0',metadata={'title':'测试'},seed=42)
        code,a=self.request('/api/jobs',body);self.assertEqual(code,202)
        code,b=self.request('/api/jobs',body);self.assertEqual(code,200)
        self.assertEqual(a['job']['id'],b['job']['id']);self.assertEqual(len(self.studio.JOBS),1)
        code,_=self.request('/api/jobs',{**body,'seed':43});self.assertEqual(code,409)
        code,_=self.request('/api/jobs',{**body,'client_key':'other'});self.assertEqual(code,409)
        code,_=self.request('/api/jobs',body,token=False);self.assertEqual(code,403)

    def test_catalog_keeps_more_than_40_songs_and_pending_slots(self):
        from nightly.library import catalog
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);data=root/'studio-data';data.mkdir()
            jobs={}
            for i in range(45):
                key=f'{i:016x}';folder=data/key/'output';folder.mkdir(parents=True);(folder/'audio.flac').write_bytes(b'fixture')
                jobs[key]={'id':key,'status':'completed','created_at':i,'request':{'lyrics':'[Verse]\n对应歌词'+str(i)},'metrics':{'duration_seconds':180}}
            c=catalog(root,data,jobs)
            self.assertEqual(len(c['tracks']),45)
            self.assertEqual(len({t['id'] for t in c['tracks']}),45)
            self.assertTrue(all(t['audio_url'] for t in c['tracks']))
            self.assertFalse(any(t['voice_verified'] for t in c['tracks']))

    def test_mp3_catalog_and_http_range(self):
        key='eeeeeeeeeeeeeeee'
        folder=Path(self.temp.name)/key/'output';folder.mkdir(parents=True,exist_ok=True)
        payload=b'ID3test-mp3-data';(folder/'audio.mp3').write_bytes(payload)
        self.studio.JOBS[key]=dict(id=key,status='completed',created_at=1,request={'lyrics':'测试'},metadata={'title':'门口那盏灯'},metrics={'duration_seconds':180})
        code,c=self.request('/api/library');self.assertEqual(code,200)
        track=next(t for t in c['tracks'] if t['id']==key)
        self.assertEqual(track['audio_url'],f'/media/{key}/audio.mp3')
        req=urllib.request.Request(self.base+track['audio_url'],headers={'Range':'bytes=0-2'})
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.status,206);self.assertEqual(r.headers.get_content_type(),'audio/mpeg');self.assertEqual(r.read(),b'ID3')

    def test_download_has_unicode_filename_and_real_extension(self):
        from urllib.parse import unquote
        key='bbbbbbbbbbbbbbbb';folder=Path(self.temp.name)/key/'output';folder.mkdir(parents=True,exist_ok=True)
        (folder/'audio.mp3').write_bytes(b'ID3audio')
        self.studio.JOBS[key]=dict(id=key,status='completed',created_at=1,request={'lyrics':'测试'},metadata={'title':'门口那盏灯'})
        code,c=self.request('/api/library');t=next(t for t in c['tracks'] if t['id']==key)
        self.assertTrue(unquote(t['download_url']).endswith('/小Rei-门口那盏灯.mp3'))
        for url,kind in ((t['download_url'],'attachment'),(t['audio_url'],'inline')):
            with urllib.request.urlopen(self.base+url) as r:
                disposition=r.headers['Content-Disposition']
                self.assertTrue(disposition.startswith(kind+';'))
                self.assertTrue(unquote(disposition).endswith("filename*=UTF-8''小Rei-门口那盏灯.mp3"))
                self.assertEqual(r.headers.get_content_type(),'audio/mpeg')
                self.assertEqual(r.read(),b'ID3audio')

    def test_state_readable_during_mp3_conversion(self):
        from unittest.mock import Mock
        from nightly.audio import storage_progress
        key='dddddddddddddddd';folder=Path(self.temp.name)/key;out=folder/'output';out.mkdir(parents=True,exist_ok=True)
        (out/'audio.flac').write_bytes(b'fixture')
        (out/'signal_metrics.json').write_text(json.dumps({'finite':True,'duration_seconds':180,'truncated':{'abc':False,'semantic':False}}))
        job=dict(id=key,status='queued',created_at=1,request={'lyrics':'测试'})
        self.studio.JOBS[key]=job;self.studio.ACTIVE=key
        entered=threading.Event();release=threading.Event()
        def slow_convert(output,delete_source=False):
            storage_progress(output,'mp3','正在转为 MP3',90,180);entered.set();release.wait(5);return {'codec':'mp3'}
        process=Mock();process.poll.return_value=0;process.returncode=0
        with patch.object(self.studio.subprocess,'Popen',return_value=process),patch.object(self.studio,'convert',side_effect=slow_convert):
            thread=threading.Thread(target=self.stub.temp_original,args=(job,));thread.start()
            try:
                self.assertTrue(entered.wait(2))
                with urllib.request.urlopen(self.base+'/api/state',timeout=1) as r: state=json.load(r)
                live=next(j for j in state['jobs'] if j['id']==key)
                self.assertEqual(live['status'],'running')
                self.assertEqual(live['progress']['current'],90)
            finally:release.set();thread.join(3)
        self.assertEqual(job['status'],'completed')

    def test_manifest_rejects_duplicate_slots(self):
        from nightly import pipeline
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);p=root/'nightly-data/2026-10-21/manifest.json';p.parent.mkdir(parents=True)
            source={'batch':'2026-10-21','songs':[{'slot':s} for s in sorted(pipeline.COMBINATIONS)]}
            source['songs'][1]['slot']=source['songs'][0]['slot'];p.write_text(json.dumps(source))
            with patch.object(pipeline,'ROOT',root):
                with self.assertRaisesRegex(ValueError,'恰好一首'):pipeline.load_manifest(p)


if __name__=='__main__':unittest.main()
