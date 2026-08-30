from pathlib import Path

p = Path("evidence_reasoning_v2.py")

if not p.exists():
    raise SystemExit(
        "Run this script from the Core directory containing evidence_reasoning_v2.py"
    )

s = p.read_text(encoding="utf-8")

marker = "def _alignment_pairs("

if marker not in s:
    raise SystemExit(
        "Could not find def _alignment_pairs("
    )

needed = (
    "ALIGNMENT_RELATIONS",
    "PROOF_ROLES",
    "EVIDENCE_ALIGNMENT_SYSTEM",
)

present = {
    name: (name in s)
    for name in needed
}

print("Current D7.8 prelude state:")
for name, ok in present.items():
    print(f"  {name}: {ok}")

if all(present.values()):
    print(
        "D7.8 alignment prelude already present; no change made."
    )
    raise SystemExit(0)

# The counterevidence-retrieval installer accidentally replaced the whole
# source span between retrieve_requirement_candidates() and _alignment_pairs(),
# deleting this prelude. Restore the original definitions only.
alignment_prelude = r