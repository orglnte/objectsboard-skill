#!/usr/bin/env python3
"""Which objects call which, recorded while a test suite runs: the object
graph of a whole codebase, one arrow per pair of objects.

    trace_objects.py --root path/to/pkg --out objects.json [--spec board.json] \
        [--tests-dir tests] -- <pytest arguments>

An object is a class, or a module that has functions of its own (in Python a
module is an object too). Every function under --root belongs to one: a
method to its class, a top-level function to its module. The profiler
records each call from one object into another; calls inside one object are
not recorded. Closures (decorator wrappers, lambdas) and frames outside the
project are walked past, so a call is charged to the object that made it.

--spec writes a board spec for board.py: one box per object, grouped by its
folder (the folders under --root, never one box around everything), one
arrow per pair labelled with the members called, each with a proof: one of
those members, or the callee's name, appears in the caller's file; when
neither does (a subclass, a callback, an injected function), the arrow is
marked "[run time only]" and its proof is the observation itself.

Run it with the project's own interpreter from the project root (it imports
the project and needs pytest). It maps only what the tests run, so it needs
tests with good coverage; plain attribute, constant and data reads and calls
in other processes are not seen.
"""
import argparse, ast, json, os, re, sys, threading
from pathlib import Path


def objects(root):
    """{(filename, first line): (object id, member)} for every function under
    root, and {object id: {"file", "name", "kind"}}."""
    fns, objs = {}, {}
    for p in sorted(Path(root).rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        try:
            t = ast.parse(p.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        path, rel = str(p.resolve()), str(p)
        mod_id = rel

        def walk(node, owner, qual):
            for ch in ast.iter_child_nodes(node):
                if isinstance(ch, ast.ClassDef):
                    oid = f"{rel}:{'.'.join(qual + [ch.name])}"
                    objs[oid] = {"file": rel, "name": ".".join(qual + [ch.name]), "kind": "class"}
                    walk(ch, oid, qual + [ch.name])
                elif isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if owner == mod_id:
                        objs.setdefault(mod_id, {"file": rel, "name": p.stem, "kind": "module"})
                    first = min([ch.lineno] + [d.lineno for d in ch.decorator_list])
                    fns[(path, first)] = (owner, ch.name)
                    walk(ch, owner, qual + [ch.name])

        walk(t, mod_id, [])
    return fns, objs


def run(root, pytest_args, tests_dir):
    import pytest
    sys.path.insert(0, os.getcwd())               # as `python -m pytest` does
    fns, objs = objects(root)
    proj = str(Path.cwd().resolve()) + os.sep
    tests = str(Path(tests_dir).resolve()) + os.sep
    hits = {}

    def prof(frame, event, arg):
        if event != "call":
            return
        c = frame.f_code
        callee = fns.get((c.co_filename, c.co_firstlineno))
        if callee is None or "<locals>" in getattr(c, "co_qualname", ""):
            return
        b = frame.f_back
        while b is not None:
            bc = b.f_code
            fn = bc.co_filename
            if fn.startswith(proj) and "<locals>" not in getattr(bc, "co_qualname", ""):
                break
            b = b.f_back                      # outside the project, or a closure
        if b is None or b.f_code.co_filename.startswith(tests):
            return
        caller = fns.get((b.f_code.co_filename, b.f_code.co_firstlineno))
        if caller is None or caller[0] == callee[0]:
            return
        key = (caller[0], callee[0], callee[1])
        hits[key] = hits.get(key, 0) + 1

    sys.setprofile(prof)
    threading.setprofile(prof)
    try:
        rc = pytest.main(pytest_args)
    finally:
        sys.setprofile(None)
        threading.setprofile(None)
    return rc, hits, objs


def spec_of(objs, edges, root):
    used = {e["from"] for e in edges} | {e["to"] for e in edges}
    folders, nodes = {}, []
    for oid in sorted(used):
        o = objs[oid]
        d = str(Path(o["file"]).parent)
        folders.setdefault(d, f"f{len(folders) + 1}")
    for d, fid in folders.items():
        nodes.append({"id": fid, "name": d + "/", "kind": "folder", "parent": None})
    for oid in sorted(used):
        o = objs[oid]
        nodes.append({"id": oid, "name": o["name"] + ("" if o["kind"] == "class" else ".py"),
                      "kind": "object" if o["kind"] == "class" else "module",
                      "parent": folders[str(Path(o["file"]).parent)], "note": o["file"]})
    E = []
    for e in edges:
        ms = e["members"]
        label = " · ".join(ms[:3]) + (f"  (+{len(ms) - 3})" if len(ms) > 3 else "")
        names = [m for m in ms if not m.startswith("__")] + [objs[e["to"]]["name"].split(".")[-1]]
        if objs[e["to"]]["kind"] == "module":
            names.append(Path(objs[e["to"]]["file"]).stem)
        pattern = r"\b(" + "|".join(re.escape(n) for n in names) + r")\b"
        caller_file = objs[e["from"]]["file"]
        try:
            named = re.search(pattern, Path(caller_file).read_text(errors="replace")) is not None
        except OSError:
            named = False
        proof = ({"file": caller_file, "pattern": pattern} if named
                 else {"observed": "called at run time through a subclass, a callback or an injected function"})
        E.append({"from": e["from"], "to": e["to"], "kind": "calls",
                  "label": label + ("" if named else "  [run time only]"), "proof": [proof]})
    return {"name": f"{root} — objects", "nodes": nodes, "edges": E}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--spec")
    ap.add_argument("--tests-dir", default="tests")
    ap.add_argument("pytest_args", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    args = a.pytest_args[1:] if a.pytest_args[:1] == ["--"] else a.pytest_args
    rc, hits, objs = run(a.root, args, a.tests_dir)
    pairs = {}
    for (src, dst, member), n in hits.items():
        p = pairs.setdefault((src, dst), {"members": set(), "calls": 0})
        p["members"].add(member)
        p["calls"] += n
    edges = [{"from": s, "to": d, "members": sorted(v["members"]), "calls": v["calls"]}
             for (s, d), v in sorted(pairs.items())]
    json.dump({"root": a.root, "pytest_exit": int(rc), "objects": objs, "edges": edges},
              open(a.out, "w"), indent=1)
    if a.spec:
        json.dump(spec_of(objs, edges, a.root), open(a.spec, "w"), indent=1)
    print(f"{len({e['from'] for e in edges} | {e['to'] for e in edges})} objects, {len(edges)} object pairs; "
          f"pytest exit {rc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
