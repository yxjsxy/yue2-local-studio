"""Song curation checks use disposable folders, never the user's music."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from nightly.collection import favorite, delete_track, read_state, CollectionError, deleted_request
from nightly.library import catalog


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.data=self.root/'studio-data';self.data.mkdir()
        self.jobs={}

    def job(self,key='a'*16,slot='folk-male',status='completed',audio=b'ID3-disposable-audio'):
        folder=self.data/key/'output';folder.mkdir(parents=True)
        (folder/'audio.mp3').write_bytes(audio);(folder/'latent.npy').write_bytes(b'large-intermediate'*100)
        job=dict(id=key,status=status,created_at=1,request={'lyrics':'[Verse]\n这是测试专用歌词'},
                 metadata={'title':'可删除的测试歌','batch':'2099-01-01','slot':slot,'genre':slot.split('-')[0],'voice':slot.split('-')[1]},
                 client_key='2099-01-01-'+slot+'-0')
        self.jobs[key]=job;(self.data/key/'job.json').write_text(json.dumps(job));return key

    def test_favorite_persists_and_unfavorite_updates_same_catalog(self):
        key=self.job();favorite(self.root,self.data,self.jobs,key,True)
        self.assertTrue(catalog(self.root,self.data,self.jobs)['tracks'][0]['favorite'])
        self.assertIn(key,read_state(self.data)['favorites'])
        favorite(self.root,self.data,self.jobs,key,False)
        self.assertFalse(catalog(self.root,self.data,self.jobs)['tracks'][0]['favorite'])
        with self.assertRaises(CollectionError):favorite(self.root,self.data,self.jobs,key,'true')

    def test_delete_reclaims_audio_intermediates_retry_and_blocks_resubmission(self):
        key=self.job();retry=self.job('b'*16,status='failed');other=self.job('c'*16,slot='folk-female',status='running')
        self.jobs[retry]['client_key']='2099-01-01-folk-male-1'
        models=self.root/'models';models.mkdir();(models/'weights').write_bytes(b'keep-shared-model')
        favorite(self.root,self.data,self.jobs,key,True)
        manifest=self.root/'nightly-data/2099-01-01/manifest.json';manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({'batch':'2099-01-01','songs':[{'slot':'folk-male','genre':'folk','voice':'male','title':'可删除的测试歌','lyrics':'文本','style':'folk'}]}))
        before=sum(p.stat().st_size for p in self.data.glob('*/**/*') if p.is_file())
        result=delete_track(self.root,self.data,self.jobs,key,'可删除的测试歌')
        self.assertGreater(result['removed_bytes'],1000)
        self.assertFalse((self.data/key).exists());self.assertFalse((self.data/retry).exists())
        self.assertTrue((self.data/other/'output/audio.mp3').exists());self.assertTrue((models/'weights').exists())
        self.assertNotIn(key,self.jobs);state=read_state(self.data);self.assertNotIn(key,state['favorites'])
        self.assertTrue(deleted_request(state,'2099-01-01-folk-male-99',{'batch':'2099-01-01','slot':'folk-male'}))
        self.assertFalse(deleted_request(state,None,{'batch':'2099-01-02','slot':'folk-male'}))
        self.assertFalse(any(t['voice']=='male' for t in catalog(self.root,self.data,self.jobs)['tracks']))
        self.assertEqual(delete_track(self.root,self.data,self.jobs,key,'可删除的测试歌')['removed_bytes'],result['removed_bytes'])

    def test_deleted_copy_is_not_republished_from_archive(self):
        for area in ('outputs','public'):
            folder=self.root/area/'test-case';folder.mkdir(parents=True)
            (folder/'audio.mp3').write_bytes(b'same-disposable-audio')
            (folder/'request.json').write_text(json.dumps({'lyrics':'测试样例歌词'}))
            (folder/'result.json').write_text(json.dumps({'audio_seconds':180}))
        tracks=catalog(self.root,self.data,self.jobs)['tracks'];self.assertEqual(len(tracks),1)
        t=tracks[0];favorite(self.root,self.data,self.jobs,t['id'],True)
        delete_track(self.root,self.data,self.jobs,t['id'],t['title'])
        self.assertFalse((self.root/'outputs/test-case').exists());self.assertFalse((self.root/'public/test-case').exists())
        self.assertEqual(catalog(self.root,self.data,self.jobs)['tracks'],[])

    def test_reject_live_job_wrong_confirmation_and_symlink(self):
        key=self.job(status='running')
        with self.assertRaises(CollectionError) as result:delete_track(self.root,self.data,self.jobs,key,'可删除的测试歌')
        self.assertEqual(result.exception.status,409)
        self.jobs[key]['status']='completed'
        with self.assertRaises(CollectionError):delete_track(self.root,self.data,self.jobs,key,'另一个歌名')
        self.assertTrue((self.data/key/'output/audio.mp3').exists())
        outside=self.root/'precious';outside.mkdir();(outside/'keep').write_text('keep')
        (self.data/key/'output/audio.mp3').unlink();(self.data/key/'output/audio.mp3').symlink_to(outside/'keep')
        with self.assertRaises(CollectionError):delete_track(self.root,self.data,self.jobs,key,'可删除的测试歌')
        self.assertTrue((outside/'keep').exists())

    def test_cleanup_failure_can_be_retried_without_losing_intent(self):
        key=self.job()
        with patch('nightly.collection.shutil.rmtree',side_effect=PermissionError('test refusal')):
            with self.assertRaises(PermissionError):delete_track(self.root,self.data,self.jobs,key,'可删除的测试歌')
        self.assertFalse(read_state(self.data)['deleted'][key]['cleanup_complete'])
        self.assertEqual(catalog(self.root,self.data,self.jobs)['tracks'],[])
        self.assertTrue(delete_track(self.root,self.data,self.jobs,key,'可删除的测试歌')['deleted'])
        self.assertFalse((self.data/key).exists())

if __name__=='__main__':unittest.main()
