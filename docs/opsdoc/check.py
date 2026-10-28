"""Release checks: no forbidden word, no password from the credentials file (if KS_CRED_FILE is set), footer on
every page. Prints counts only - never the secret itself."""
import os, re, subprocess, sys, zipfile
from pathlib import Path
docx, pdf = sys.argv[1], sys.argv[2]
xml = "".join(zipfile.ZipFile(docx).read(n).decode("utf8", "ignore") for n in zipfile.ZipFile(docx).namelist() if n.endswith(".xml"))
txt = subprocess.run(["pdftotext", pdf, "-"], capture_output=True, text=True).stdout
npages = int([l for l in subprocess.run(["pdfinfo", pdf], capture_output=True, text=True).stdout.splitlines() if l.startswith("Pages")][0].split()[-1])
bad = "d" + "emo"
ok = True
for label, body in (("docx", xml), ("pdf", txt)):
    n = len(re.findall(bad, body, re.I)); print(f"forbidden word in {label}: {n}"); ok &= n == 0
cred = os.environ.get("KS_CRED_FILE")
if cred and Path(cred).exists():
    secrets = {f[2] for f in (l.split("\t") for l in Path(cred).read_text().splitlines() if not l.startswith("#")) if len(f) == 3 and f[2]}
    n = sum(s in xml or s in txt for s in secrets); print(f"password matches: {n} (of {len(secrets)} checked)"); ok &= n == 0
foot = txt.count("Prototype software, not a certified medical device")
print(f"pages {npages}; footer notice found {foot} times"); ok &= foot >= npages
print(f"size docx {Path(docx).stat().st_size/1e6:.2f} MB, pdf {Path(pdf).stat().st_size/1e6:.2f} MB")
print("CHECKS", "PASSED" if ok else "FAILED"); sys.exit(0 if ok else 1)
