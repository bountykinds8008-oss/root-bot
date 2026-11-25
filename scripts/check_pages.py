"""公開前チェック：画面（HTML）の中のプログラムに文法の間違いがないか node で確かめる。
   1か所でも間違いがあると、その画面のプログラム全体が動かなくなるため。  python scripts/check_pages.py"""
import pathlib, re, subprocess, sys, tempfile

bad = 0
for f in sorted(pathlib.Path("dashboard").glob("*.html")):
    for i, m in enumerate(re.finditer(r"<script(?![^>]*\bsrc=)([^>]*)>(.*?)</script>", f.read_text(), re.S)):
        mod = "module" in m.group(1)
        with tempfile.NamedTemporaryFile("w", suffix=".mjs" if mod else ".js", delete=False) as t:
            t.write(m.group(2))
        r = subprocess.run(["node", "--check", t.name], capture_output=True, text=True)
        if r.returncode:
            bad += 1
            print(f"NG {f.name} script#{i}:\n{r.stderr.strip().splitlines()[-1] if r.stderr else ''}")
for f in sorted(pathlib.Path("dashboard").rglob("*.js")):
    r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
    if r.returncode:
        bad += 1
        print(f"NG {f}: {r.stderr.strip().splitlines()[-1]}")
print("OK" if not bad else f"{bad}件の間違い"); sys.exit(1 if bad else 0)
