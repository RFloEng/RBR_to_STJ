"""One-off helper: list the surface id -> name facts from the reference notes and cross-check them.

Reads reference/rerbr/crates/rbr-formats/src/lsp_keys.rs (MaterialManager keys) and
reference/rerbr/data/physics-lsp-fields.md ("surface id" rows). Prints both tables' agreement.
It only reads two text files; no reRBR code is reused. Output (id,name pairs) are format facts.
"""
import re, sys, json
from pathlib import Path
ref = Path(__file__).resolve().parents[1] / "reference" / "rerbr"
a = {}
for m in re.finditer(r'C::MaterialManager,\s*"([^"]+)",\s*(0x[0-9a-fA-F]+|\d+)', (ref/"crates/rbr-formats/src/lsp_keys.rs").read_text(encoding="utf-8")):
    a[int(m.group(2), 0)] = m.group(1)
b = {}
for m in re.finditer(r'^\|\s*([A-Za-z0-9_]+)\s*\|\s*id (\d+)\s*\(`w2=\d+', (ref/"data/physics-lsp-fields.md").read_text(encoding="utf-8"), re.M):
    b[int(m.group(2))] = m.group(1)
print("lsp_keys.rs:", len(a), "physics-lsp-fields.md:", len(b))
diff = {k: (a.get(k), b.get(k)) for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)}
print("differences:", diff)
if "--json" in sys.argv:
    print(json.dumps({str(k): v for k, v in sorted(a.items())}, indent=0))
