#!/usr/bin/env python3

from pathlib import Path
from collections import Counter
import pandas as pd

HERE = Path(__file__).resolve().parent

INPUT = HERE / "FRECA_TASK2_FINAL_SUBMISSION_20260830.xlsx"
OUTPUT = HERE / "FRECA_TASK2_FINAL_SUBMISSION_20260830.csv"

if not INPUT.exists():
    raise FileNotFoundError(f"Input XLSX not found: {INPUT}")

# IMPORTANT:
# preserve literal "N/A" instead of converting it to NaN
df = pd.read_excel(
    INPUT,
    dtype=str,
    keep_default_na=False,
)

expected = ["RE Number"] + [f"CP{i}" for i in range(1, 42)]

if list(df.columns) != expected:
    raise RuntimeError(
        f"Unexpected columns:\n{list(df.columns)}"
    )

if df.shape != (100, 42):
    raise RuntimeError(
        f"Expected shape (100, 42), got {df.shape}"
    )

allowed = {"0", "1", "N/A"}

for cp in expected[1:]:
    values = set(df[cp].astype(str))
    bad = values - allowed

    if bad:
        raise RuntimeError(
            f"{cp}: invalid values {bad}"
        )

if (df["RE Number"].astype(str).str.strip() == "").any():
    raise RuntimeError("Empty RE Number detected")

all_verdicts = df[expected[1:]].to_numpy().ravel()
counts = Counter(all_verdicts)

expected_counts = {
    "0": 2652,
    "1": 1261,
    "N/A": 187,
}

if counts != Counter(expected_counts):
    raise RuntimeError(
        f"Unexpected verdict counts:\n"
        f"got={dict(counts)}\n"
        f"expected={expected_counts}"
    )

df.to_csv(
    OUTPUT,
    index=False,
    encoding="utf-8",
)

print("Saved:", OUTPUT)
print("Shape:", df.shape)
print("Decisions:", len(all_verdicts))
print("Verdict counts:", dict(counts))
print("CSV BUILD PASS")
