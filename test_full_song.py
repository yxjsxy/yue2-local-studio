"""Boundary and numerical tests; no model weights or private jobs are loaded."""
import fcntl
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest

import torch
from mps_attention import stable_sdpa

ROOT = Path(__file__).resolve().parent


class FullSongTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        shutil.copyfile(ROOT / "studio_server.py", cls.root / "studio_server.py")
        spec = importlib.util.spec_from_file_location("isolated_studio", cls.root / "studio_server.py")
        cls.server = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.server)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_complete_lyrics_and_native_budget(self):
        request = self.server.validate({"lyrics": "歌" * 4000, "style": "中文"})
        self.assertEqual(len(request["lyrics"]), 4000)
        self.assertEqual(set(request), {"lyrics", "style", "cot", "seed"})
        self.assertEqual(self.server.validate_options({})["max_audio_seconds"], 360)
        for mode in ("full", "melody", "off"):
            self.assertEqual(self.server.validate({"lyrics": "歌", "style": "中文", "mode": mode})["cot"], mode)
        with self.assertRaises(ValueError):
            self.server.validate({"lyrics": "歌" * 4001, "style": "中文"})

    def test_budget_boundaries(self):
        for body in (
            {"target_seconds": True}, {"target_seconds": 240, "max_audio_seconds": 240},
            {"max_audio_seconds": 361}, {"max_abc_tokens": 8193}, {"timeout_minutes": 121},
        ):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.server.validate_options(body)

    def test_process_lock_prevents_parallel_cli_and_web(self):
        self.assertFalse(self.server.external_busy())
        with (self.root / "evidence/inference.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertTrue(self.server.external_busy())
        self.assertFalse(self.server.external_busy())

    def test_attention_across_blocks_matches_reference(self):
        torch.manual_seed(4)
        q = torch.randn(1, 4, 257, 32)
        k = torch.randn(1, 2, 257, 32)
        v = torch.randn_like(k)
        for causal in (False, True):
            actual = stable_sdpa(q, k, v, is_causal=causal)
            expected = torch.nn.functional.scaled_dot_product_attention(
                q, k.repeat_interleave(2, 1), v.repeat_interleave(2, 1), is_causal=causal)
            torch.testing.assert_close(actual, expected, atol=2e-5, rtol=2e-4)


if __name__ == "__main__":
    unittest.main()
