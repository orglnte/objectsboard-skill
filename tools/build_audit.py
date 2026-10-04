#!/usr/bin/env python3
"""Fill interface-audit.template.html with extract_interface.py's data plus
hand-written annotations, and write the page to publish.

    build_audit.py data.json annotations.json out.html

annotations.json (every key optional):
  title         page title, e.g. "Order Interface Audit"
  stamp         repo heads the data was read at
  intro         one paragraph of HTML
  callers       [{group, route, file, fn, uses: [..], why, finding?}]  -- why is read from each call site
  routes        [{route, css: process|object|standin|const, title, text}]
  facts         [html, ...]  -- numbered findings, each checkable
  ctor_callers  ["module.function", ...]  -- who constructs the class
  notPrivate    ["_Prefix", ...]  -- names starting with _ that are not private members (e.g. a stand-in class)
  footer        HTML replacing the default analysis note
"""
import json, pathlib, sys

data = json.load(open(sys.argv[1]))
ann = json.load(open(sys.argv[2]))
data.update(ann)
tpl = (pathlib.Path(__file__).parent / "interface-audit.template.html").read_text()
title = ann.get("title") or f"{data['cls']} Interface Audit"
html = (tpl.replace("__TITLE__", title)
           .replace("__DATA__", json.dumps(data, separators=(",", ":")).replace("</", "<\\/")))
pathlib.Path(sys.argv[3]).write_text(html)
print(f"wrote {sys.argv[3]} ({len(html)} bytes)")
