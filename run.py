# -*- coding: utf-8 -*-
"""出提交:用全部 200 训练对训练,预测 100 测试对,写官方格式 submission.csv。

用法:
  python run.py --model unet
  python run.py --model classical --out my_submission.csv
"""
import argparse, os, time
import config as C
import data as D
import submission as S
import models  # 触发注册


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="unet", help=f"模型名,已注册:{models.list_models()}")
    ap.add_argument("--out", default=os.path.join(C.OUT_DIR, "submission.csv"))
    ap.add_argument("--no-tta", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    train_pairs = D.load_train_pairs()
    test_pairs = D.load_test_pairs()
    print(f"训练对 {len(train_pairs)},测试对 {len(test_pairs)};数据根 {C.DATA_ROOT}")

    kw = {}
    if args.model == "unet" and args.no_tta:
        kw["tta"] = False
    model = models.get_model(args.model, **kw)

    model.fit(train_pairs)
    pred = {}
    for i, pr in enumerate(test_pairs):
        pred[pr.img_id] = [list(map(int, b)) for b in model.predict(pr.template, pr.photo)]
        if (i + 1) % 20 == 0:
            print(f"  预测测试 {i+1}/{len(test_pairs)}", flush=True)

    n = S.write_submission(pred, test_pairs, args.out)
    cnts = [len(v) for v in pred.values()]
    print(f"\n[提交] {args.out}  总框={n}  图={len(test_pairs)}  "
          f"每图中位={int(sorted(cnts)[len(cnts)//2]) if cnts else 0}  用时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
