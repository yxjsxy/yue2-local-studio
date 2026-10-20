import torch
from mps_attention import stable_sdpa
for device in ['cpu','mps']:
 for causal in [False,True]:
  torch.manual_seed(4)
  q=torch.randn(1,4,257,32,device=device)
  k=torch.randn(1,2,257,32,device=device)
  v=torch.randn_like(k)
  actual=stable_sdpa(q,k,v,is_causal=causal)
  expected=torch.nn.functional.scaled_dot_product_attention(q.cpu(),k.cpu().repeat_interleave(2,1),v.cpu().repeat_interleave(2,1),is_causal=causal)
  assert torch.allclose(actual.cpu(),expected,atol=2e-5,rtol=2e-4),(device,causal,(actual.cpu()-expected).abs().max())
 q=torch.randn(1,4,1,32,device=device,dtype=torch.bfloat16)
 k=torch.randn(1,2,7000,32,device=device,dtype=torch.bfloat16);v=torch.randn_like(k)
 actual=stable_sdpa(q,k,v)
 assert torch.isfinite(actual).all()
 print(device,'causal/cross-block/GQA parity and long-key BF16 finite PASS')
