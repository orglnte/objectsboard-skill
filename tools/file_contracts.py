#!/usr/bin/env python3
"""Who reads and writes an object's files on disk, bypassing the object.

    file_contracts.py --name config.json --name state.db --name .lock \
        --root lib=path/to/lib --root app=path/to/app [--with-tests]

Prints one line per quoted mention ("name" or '/name') with the next two
lines of context. A mention is not yet a read or a write: classify each from
its context (write_text / open("a") / unlink = write; read_text / exists /
parse = read) before drawing it.
"""
import argparse, pathlib, re

ap = argparse.ArgumentParser()
ap.add_argument("--name", action="append", required=True)
ap.add_argument("--root", action="append", required=True)
ap.add_argument("--with-tests", action="store_true")
a = ap.parse_args()
for spec in a.root:
    tag, base = spec.split("=", 1)
    for p in sorted(pathlib.Path(base).rglob("*.py")):
        if "__pycache__" in p.parts or (not a.with_tests and "/tests/" in str(p)):
            continue
        L = p.read_text(errors="replace").splitlines()
        for i, line in enumerate(L, 1):
            for n in a.name:
                if re.search(r'["\'/]' + re.escape(n) + r'["\']', line):
                    ctx = " ⏎ ".join(x.strip() for x in L[i - 1:i + 2])[:230]
                    print(f"{n}\t{tag}:{p}:{i}\t{ctx}")
