"""Rebuild static/playbook-connect.zip from extension/playbook-connect. Run from the repo root: python3 tools/build_ext.py"""
import os, zipfile
src = os.path.join("extension", "playbook-connect"); out = os.path.join("static", "playbook-connect.zip")
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for root, _, files in os.walk(src):
        for f in sorted(files):
            if f.startswith("."): continue
            p = os.path.join(root, f)
            zi = zipfile.ZipInfo(os.path.relpath(p, "extension"), date_time=(2026, 1, 1, 0, 0, 0)); zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            z.writestr(zi, open(p, "rb").read())
print(out, os.path.getsize(out), "bytes:", zipfile.ZipFile(out).namelist())
