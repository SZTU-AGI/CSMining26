#!/bin/bash
# T1 全流程编排(幂等 · 抗重启 · 磁盘安全):产物已存在就跳过,崩了重跑本脚本从断点续。
# 不缓存热图(图最大61MP会撑爆盘)——用 eval_oof_sweep.py 内联评测,只落很小的计数 JSON。
# 顺序:4通道OOF-sweep(split0,1)→合并 → DINO-zs → SAM(尽力)→ DINO通道OOF-sweep → 合并。
cd /root/task1_pipeline || exit 1
export T1_DATA=/root/autodl-tmp/cyberaicup2026/task1
export HF_ENDPOINT=https://hf-mirror.com
PY=/root/miniconda3/bin/python
OUT=outputs
mkdir -p "$OUT"
log(){ echo "[master $(date +%H:%M:%S)] $*"; }

# ---- 1) 4通道 OOF 内联 sweep(幂等:计数 JSON 已在则跳过)----
for S in 0 1; do
  if [ -s "$OUT/sweep_counts_split${S}.json" ]; then
    log "4ch split${S} 计数已存在,跳过"
  else
    log "4ch split${S} OOF-sweep 开始"
    $PY -u eval_oof_sweep.py --split-seed $S > eval_s${S}_4ch.log 2>&1
    [ -s "$OUT/sweep_counts_split${S}.json" ] && log "4ch split${S} 完成" || { log "4ch split${S} 失败,看 eval_s${S}_4ch.log"; exit 1; }
  fi
done
log "4通道合并对比"; $PY -u combine_sweep.py > sweep_4ch.log 2>&1 && log "4通道结果 -> sweep_4ch.log" || log "combine 4ch 失败"

# ---- 2) DINO 零样本 baseline(首次下 dinov2-base;done 标记幂等)----
if [ -f "$OUT/dino_zs.done" ]; then log "DINO-zs 已完成,跳过"; else
  log "DINO-zs baseline"
  $PY -u baseline_dino_zs.py > dino_zs.log 2>&1 && touch "$OUT/dino_zs.done" && log "DINO-zs 完成 -> dino_zs.log" || log "DINO-zs 失败,看 dino_zs.log"
fi

# ---- 3) SAM 零样本 baseline(尽力而为:装包+下权重,失败即跳过不阻塞)----
if [ -f "$OUT/sam.done" ]; then log "SAM 已完成,跳过"; else
  log "SAM 装包 + 权重"
  $PY -m pip install -q segment-anything >/dev/null 2>&1
  [ -s sam_vit_b.pth ] || wget -q -T 120 -O sam_vit_b.pth https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth
  if [ -s sam_vit_b.pth ]; then
    SAM_CKPT=$PWD/sam_vit_b.pth SAM_TYPE=vit_b $PY -u bench_baselines.py --models sam_zs > sam_zs.log 2>&1
  else
    echo "SAM 权重未下到(国内网络),跳过 SAM baseline" > sam_zs.log
  fi
  touch "$OUT/sam.done"; log "SAM 阶段完成 -> sam_zs.log"
fi

# ---- 4) DINO 通道 A/B(5通道 OOF 内联 sweep;复用第2步缓存的 DINO 图)----
for S in 0 1; do
  if [ -s "$OUT/sweep_counts_split${S}_dino.json" ]; then
    log "5ch(DINO) split${S} 已存在,跳过"
  else
    log "5ch(DINO) split${S} OOF-sweep 开始"
    USE_DINO_DIFF=1 $PY -u eval_oof_sweep.py --split-seed $S > eval_s${S}_dino.log 2>&1
    [ -s "$OUT/sweep_counts_split${S}_dino.json" ] && log "5ch(DINO) split${S} 完成" || log "5ch(DINO) split${S} 失败,看 eval_s${S}_dino.log"
  fi
done
log "DINO通道合并对比"; $PY -u combine_sweep.py --dino > sweep_dino.log 2>&1 && log "DINO结果 -> sweep_dino.log" || log "combine dino 失败"

log "ALL DONE"
touch "$OUT/master_all_done"
