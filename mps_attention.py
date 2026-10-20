"""Explicit FP32 attention on MPS, with bounded query blocks.

Keeps all keys/context and the original model weights. This is a local numerical
adaptation, not a shorter context, quantization, or independent music chunks.
"""
import hashlib
import math
from pathlib import Path

import torch


def stable_sdpa(query, key, value, *, attn_mask=None, is_causal=False, block=128):
    dtype = query.dtype
    groups = query.shape[1] // key.shape[1]
    if groups != 1:
        key = key.repeat_interleave(groups, dim=1)
        value = value.repeat_interleave(groups, dim=1)
    key, value = key.float(), value.float()
    results = []
    for start in range(0, query.shape[-2], block):
        end = min(start + block, query.shape[-2])
        scores = (query[..., start:end, :].float() @ key.transpose(-2, -1)) / math.sqrt(query.shape[-1])
        if is_causal:
            mask = torch.arange(key.shape[-2], device=query.device)[None, :] <= torch.arange(start, end, device=query.device)[:, None]
            scores.masked_fill_(~mask, float("-inf"))
        if attn_mask is not None:
            mask = attn_mask if attn_mask.shape[-2] == 1 else attn_mask[..., start:end, :]
            if mask.dtype == torch.bool:
                scores.masked_fill_(~mask, float("-inf"))
            else:
                scores = scores + mask.float()
        weights = scores.softmax(-1)
        results.append((weights @ value).to(dtype))
    return torch.cat(results, dim=-2)


def stable_nar_attention(q, k, v, *, causal=False, backend="sdpa", query_chunk_size=None):
    return stable_sdpa(
        q.transpose(0, 1)[None], k.transpose(0, 1)[None], v.transpose(0, 1)[None],
        is_causal=causal, block=query_chunk_size or 128,
    )[0].transpose(0, 1)


def install():
    from yue2 import modeling_yue2, nar
    modeling_yue2.sdpa = stable_sdpa
    nar.attention = stable_nar_attention
    return {
        "name": "mps-explicit-fp32-attention-v1",
        "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "query_block": 128,
        "context_shortened": False,
        "weights_quantized": False,
    }
