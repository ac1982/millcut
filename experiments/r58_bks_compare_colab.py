"""Compare archived public-benchmark runs (R52 Berkey dev, R55 Martello test) with
PackingSolver's own 3NHO best-known-solution index (3-stage, non-exact, horizontal first, oriented).
Runs on Colab. Inputs uploaded to /content/bks/."""
import csv, json, math
from collections import defaultdict
from pathlib import Path

B = Path("/content/bks")
def _load(path):
    d = {}
    for r in csv.DictReader(open(path)):
        v = (r["Best known solution value"] or "").strip()
        d[r["Path"]] = int(float(v)) if v else None
    return d

bks = _load(B / "data_bin_packing_3nho.csv")
uo = _load(B / "data_bin_packing_oriented.csv")
manifest = json.load(open(B / "manifest.json"))
name2path = {m["name"]: m["source"]["path"] for m in manifest if m.get("source", {}).get("kind") == "public_pinned_repository"}
name2lb = {m["name"]: m["features"]["area_lower_bound"] for m in manifest if m["name"] in name2path}

out = {"index_rule": "PackingSolver predefined 3NHO = 3 stages, NonExact, first cut Horizontal, Oriented (no rotation); identical to project rules",
       "index_rows": len(bks), "index_rows_missing_value": sum(1 for v in bks.values() if v is None),
       "index_rows_differing_from_oriented_unconstrained": sum(1 for k in bks if bks[k] is not None and uo.get(k) is not None and bks[k] != uo[k])}

def compare(rows_file, label):
    rows = json.load(open(B / rows_file))
    per = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["instance"] in name2path:
            per[r["instance"]][r["method"]].append(int(r["bins"]))
    methods = sorted({m for d in per.values() for m in d})
    res = {"instances": len(per), "methods": {}}
    detail = []
    for inst, d in sorted(per.items(), key=lambda kv: name2path[kv[0]]):
        p = name2path[inst]
        detail.append({"instance": inst, "path": p, "bks_3nho": bks.get(p), "area_lb": name2lb[inst],
                       **{m: d[m] for m in methods}})
    for m in methods:
        runs = [(b, bks[name2path[i]]) for i, d in per.items() for b in d[m] if bks.get(name2path[i]) is not None]
        res["methods"][m] = {
            "runs": len(runs),
            "total_sheets": sum(b for b, _ in runs),
            "total_bks": sum(k for _, k in runs),
            "runs_equal_bks": sum(1 for b, k in runs if b == k),
            "runs_worse_than_bks": sum(1 for b, k in runs if b > k),
            "runs_better_than_bks": sum(1 for b, k in runs if b < k),
            "extra_sheets_vs_bks": sum(b - k for b, k in runs),
        }
    res["bks_equals_area_lb_instances"] = sum(1 for x in detail if x["bks_3nho"] == x["area_lb"])
    res["detail"] = detail
    out[label] = res

compare("r55-test-rows.json", "R55_martello_heldout")
compare("r52-dev-rows.json", "R52_berkey_dev")
Path("/content/bks/bks-comparison.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
print(json.dumps({k: (v if not isinstance(v, dict) or "methods" not in v else {"instances": v["instances"], "methods": v["methods"], "bks_equals_area_lb_instances": v["bks_equals_area_lb_instances"]}) for k, v in out.items()}, indent=1, ensure_ascii=False))
