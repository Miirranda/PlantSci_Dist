"""Extract claims C20-C30 from JSONL with claim_zh, classify_evidences, review_evidences."""
import json, sys

with open("outputs/P001/A001/claim_evidence_pairs.jsonl", "r", encoding="utf-8") as f:
    lines = f.readlines()

out = []
for i in range(19, min(30, len(lines))):
    d = json.loads(lines[i])
    out.append({
        "claim_id": d["claim_id"],
        "claim_zh": d["claim_zh"],
        "classify_evidences": d.get("classify_evidences", []),
        "review_evidences": d.get("review_evidences", []),
    })

with open("data/annotations/P001/_batches/C20_C30_input.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f"Extracted {len(out)} claims (C20-C30) to _batches/C20_C30_input.json")
