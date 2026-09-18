# -*- coding: utf-8 -*-
"""按官方格式写 submission.csv。
列头与 train.csv 一致:template_image, photo_image, left_x, top_y, right_x, bottom_y
每个预测框一行;一对图有多个预测就多行。"""
import csv


def write_submission(pred_dict, test_pairs, out_path):
    """pred_dict: {img_id: [[x1,y1,x2,y2],...]};test_pairs: [Pair,...](提供官方相对路径)。"""
    id2paths = {}
    for pr in test_pairs:
        # 官方相对路径形如 template/test_template_000.png, photo/test_photo_000.png
        t_rel = "template/" + pr.template_path.replace("\\", "/").split("/")[-1]
        p_rel = "photo/" + pr.photo_path.replace("\\", "/").split("/")[-1]
        id2paths[pr.img_id] = (t_rel, p_rel)

    n_boxes = 0
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["template_image", "photo_image", "left_x", "top_y", "right_x", "bottom_y"])
        for pr in test_pairs:
            t_rel, p_rel = id2paths[pr.img_id]
            for (x1, y1, x2, y2) in pred_dict.get(pr.img_id, []):
                w.writerow([t_rel, p_rel, int(x1), int(y1), int(x2), int(y2)])
                n_boxes += 1
    return n_boxes
