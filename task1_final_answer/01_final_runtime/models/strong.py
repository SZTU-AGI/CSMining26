# -*- coding: utf-8 -*-
"""强 baseline①:现代分割骨干(via segmentation_models_pytorch)。

保持与我们 U-Net **完全相同**的 4 通道输入 + 切片 + 光度增广 + 滑窗 TTA + 连通域取框,
**只把中间的分割网络换成更强/SOTA 的架构**——apples-to-apples,单独隔离"网络架构"这个变量。

  unet_r34    : U-Net + ResNet34(ImageNet 预训练编码器)—— 强预训练骨干
  unetpp      : U-Net++(ResNet34)—— 嵌套密集跳连
  deeplabv3p  : DeepLabV3+(ResNet34)—— 空洞卷积 / ASPP(SOTA 语义分割)
  segformer   : SegFormer(MiT-b2)—— Transformer 分割

未装 smp 时自动跳过(不影响其他 baseline)。预训练权重下载失败则降级为 from-scratch 并打印提示。
"""
import warnings
import torch.nn as nn

from models.base import register
from models.unet import UNetModel

try:
    import segmentation_models_pytorch as smp
    _SMP_OK = True
except Exception as e:  # pragma: no cover
    _SMP_OK = False
    warnings.warn(f"[strong] 未装 segmentation_models_pytorch({e});smp 系强 baseline 不可用。")


class _Squeeze(nn.Module):
    """smp 输出 (B,1,H,W) → (B,H,W),对齐 pipeline 的损失/推理接口。"""
    def __init__(self, net):
        super().__init__()
        self.net = net

    def forward(self, x):
        return self.net(x).squeeze(1)


def _smp(arch, encoder, pretrained="imagenet"):
    """建一个 4 通道输入、单类输出的 smp 模型;预训练失败自动降级 None。"""
    try:
        net = getattr(smp, arch)(encoder_name=encoder, encoder_weights=pretrained,
                                 in_channels=4, classes=1)
    except Exception as e:
        print(f"[strong] {arch}/{encoder} 预训练权重加载失败({e.__class__.__name__});"
              f"降级为 from-scratch(仍是有效架构对照,只是无预训练)。", flush=True)
        net = getattr(smp, arch)(encoder_name=encoder, encoder_weights=None,
                                 in_channels=4, classes=1)
    return _Squeeze(net)


if _SMP_OK:
    @register("unet_r34")
    class UNetR34(UNetModel):
        """U-Net + ResNet34(ImageNet 预训练)。"""
        def _build_net(self):
            return _smp("Unet", "resnet34")

    @register("unetpp")
    class UNetPP(UNetModel):
        """U-Net++(ResNet34):嵌套密集跳连。"""
        def _build_net(self):
            return _smp("UnetPlusPlus", "resnet34")

    @register("deeplabv3p")
    class DeepLabV3P(UNetModel):
        """DeepLabV3+(ResNet34):空洞卷积 / ASPP。"""
        def _build_net(self):
            return _smp("DeepLabV3Plus", "resnet34")

    @register("segformer")
    class SegFormerB2(UNetModel):
        """SegFormer(MiT-b2):Transformer 分割骨干。"""
        def _build_net(self):
            # SegFormer 用 MiT 编码器;若 mit_b2 不可用/权重下不动,退回 mit_b0
            for enc in ("mit_b2", "mit_b0"):
                try:
                    return _smp("Segformer", enc)
                except Exception as e:
                    print(f"[strong] SegFormer/{enc} 失败({e.__class__.__name__}),尝试下一个", flush=True)
            # 最后兜底:U-Net + mit_b0 编码器(仍是 transformer 编码器)
            return _smp("Unet", "mit_b0")
