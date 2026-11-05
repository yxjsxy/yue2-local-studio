import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import soundfile as sf
from nightly.audio import convert, preferred_audio

class StorageTests(unittest.TestCase):
    def source(self,folder):
        signal=.1*np.sin(2*np.pi*440*np.arange(48000)/48000)
        sf.write(folder/'audio.flac',np.column_stack([signal,signal]),48000)

    def test_convert_validate_delete_and_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);self.source(folder)
            from nightly.audio import storage_progress
            with patch('nightly.audio.storage_progress',wraps=storage_progress) as progress:
                a=convert(folder)
            self.assertTrue(any(c.args[1]=='mp3' and len(c.args)>4 and c.args[3]>0 and c.args[4]==1 for c in progress.call_args_list))
            self.assertTrue(any(c.args[1]=='verify' for c in progress.call_args_list))
            self.assertTrue((folder/'audio.flac').exists())
            self.assertTrue(a['decode_verified'])
            self.assertEqual(preferred_audio(folder).name,'audio.mp3')
            b=convert(folder,delete_source=True)
            self.assertEqual(a['mp3_sha256'],b['mp3_sha256'])
            self.assertFalse((folder/'audio.flac').exists())
            self.assertEqual(convert(folder),a)
            self.assertEqual(json.loads((folder/'storage-progress.json').read_text())['phase'],'done')

    def test_validation_failure_preserves_original(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);self.source(folder);before=(folder/'audio.flac').read_bytes()
            with patch('nightly.audio.subprocess.run',side_effect=subprocess.CalledProcessError(1,'ffmpeg')):
                with self.assertRaises(subprocess.CalledProcessError):convert(folder,delete_source=True)
            self.assertEqual((folder/'audio.flac').read_bytes(),before)
            self.assertFalse((folder/'audio.mp3').exists())

    def test_encoder_failure_preserves_original(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);self.source(folder)
            with patch('nightly.audio.encode',side_effect=RuntimeError('encoder failed')):
                with self.assertRaises(RuntimeError):convert(folder,delete_source=True)
            self.assertTrue((folder/'audio.flac').exists())
            self.assertEqual(json.loads((folder/'storage-progress.json').read_text())['phase'],'storage_failed')

if __name__=='__main__': unittest.main()
