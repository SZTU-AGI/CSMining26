from pathlib import Path

p = Path("freca_core_v1.py")
s = p.read_text(encoding="utf-8")

if "run_from_evaluate_locals" in s:
    print("Requirement-level evidence hook already installed.")
    raise SystemExit(0)

needle = "BM25 evidence retrieval"
pos = s.find(needle)
if pos < 0:
    raise SystemExit("Could not locate the '[2/4] BM25 evidence retrieval' marker.")

# Insert immediately before the print(...) block containing the marker.
print_start = s.rfind("\n    print(", 0, pos)
if print_start < 0:
    raise SystemExit("Could not locate the print block before BM25 evidence retrieval.")

insert_at = print_start + 1
hook = '''    # --------------------------------------------------------\n    # Requirement-level evidence reasoning pilot\n    # D2.8 + D7.1 + D7.8 + D7.14\n    #\n    # Local import avoids circular import because\n    # evidence_reasoning_v2 imports freca_core_v1.\n    # --------------------------------------------------------\n    from evidence_reasoning_v2 import (\n        run_from_evaluate_locals,\n        print_requirement_result,\n    )\n\n    requirement_reasoning = run_from_evaluate_locals(\n        locals(),\n        retrieval_top_k=12,\n    )\n\n    print_requirement_result(\n        requirement_reasoning\n    )\n\n'''

s = s[:insert_at] + hook + s[insert_at:]
p.write_text(s, encoding="utf-8")
print("Installed requirement-level evidence hook into freca_core_v1.py")
