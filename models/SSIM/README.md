# models/mine — 我的方案

`my_tasl` / `my_tasl_ens`。

| 模型 | 说明 |
|------|------|
| `my_tasl` | Text-Aware Structural Learner: ECC 对齐 + 6 通道小 U-Net(TripletAttn/CorrDFE/深监督/SSIM损失)。`--model my_tasl` |
| `my_tasl_ens` | 同上,3-seed 热图集成。`--model my_tasl_ens` |

```bash
python validate.py --model my_tasl
python validate.py --model my_tasl_ens   # 需 GPU(无则自动用 CPU,较慢)
python run_tasl_kfold.py                 # 4折×3seed OOF
```
