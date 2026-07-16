# -*- coding: utf-8 -*-
"""本地评测:把 200 训练对划成 训练/验证,在验证集上算 全局 F1。
别的同学换上自己的模型后,主要就跑这个看分数。

用法:
  python validate.py --model classical
  python validate.py --model unet
  python validate.py --model unet --no-tta        # 关掉 TTA 对比
"""
import argparse, time
import config as C
import data as D
import evaluate as E
import models  # 触发模型注册


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="unet", help=f"模型名,已注册:{models.list_models()}")
    ap.add_argument("--val-size", type=int, default=C.VAL_SIZE)
    ap.add_argument("--seed", type=int, default=C.VAL_SEED)
    ap.add_argument("--iou", type=float, default=C.IOU_THRESH)
    ap.add_argument("--no-tta", action="store_true", help="U-Net 关闭 TTA")
    args = ap.parse_args()

    t0 = time.time()
    pairs = D.load_train_pairs()
    tr, va = D.train_val_split(pairs, args.val_size, args.seed)
    print(f"训练对 {len(pairs)} → 训练 {len(tr)} / 验证 {len(va)};数据根 {C.DATA_ROOT}")

    kw = {}
    if args.model == "unet" and args.no_tta:
        kw["tta"] = False
    model = models.get_model(args.model, **kw)

    model.fit(tr)
    res, _ = E.evaluate_model(model, va, thr=args.iou)
    print("\n===== 验证结果 =====")
    print(f"  模型: {args.model}   IoU阈值: {args.iou}")
    print(f"  全局 F1 = {res['f1']:.4f}   P = {res['precision']:.4f}   R = {res['recall']:.4f}")
    print(f"  TP={res['tp']}  FP={res['fp']}  FN={res['fn']}   用时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
