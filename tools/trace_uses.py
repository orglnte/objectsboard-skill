#!/usr/bin/env python3
"""Every call into an object's functions while a test suite runs, and from
where: the run-time cross-check of extract_interface.py, and the call graph
into the object.

    trace_uses.py --target TARGET --out trace.json \
        [--diff data.json] [--tests-dir tests] -- <pytest arguments>

TARGET is a module (`path/to/mod.py`: every function in it, methods
included), a class (`path/to/mod.py:Name`: its methods and properties) or a
function (`path/to/mod.py:func`). Run it with the project's own interpreter
(it imports the project and needs pytest); caller names are fully qualified
on Python 3.11+.

Run it from the directory the suite is normally run from. It runs pytest in
this process with a profiler that records each call to one of the target's
functions and the frame that made it: the first frame inside the project
and outside the target (decorators, wrappers and the standard library are
walked past; a call from the target's own functions is internal and not
recorded), as file, line and calling function. The callers form the call
graph into the target (caller -> member).

--diff takes extract_interface.py's output and lists, per file, the members
seen at run time but not found statically, and the reverse.

It maps only what the tests run: the call graph is as complete as the
suite's coverage of the code that uses the target, so it needs tests with
good coverage. It cannot see reads of plain attributes, constants or data
objects (no function runs), nor calls made in another process (subprocesses
the tests start). A caller inside the test files counts as a test.
"""
import argparse, ast, json, os, sys, threading
from pathlib import Path


def targets(cfile, name):
    """{(filename, first line): member} for each function of the target; the
    first line is a decorator's when there is one, as in the code object.
    Members are qualified names relative to the target."""
    t = ast.parse(Path(cfile).read_text())
    path = str(Path(cfile).resolve())
    out = {}

    def walk(node, qual):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                q = qual + [ch.name]
                if not isinstance(ch, ast.ClassDef):
                    first = min([ch.lineno] + [d.lineno for d in ch.decorator_list])
                    out[(path, first)] = ".".join(q)
                walk(ch, q)

    walk(t, [])
    if not name:
        return out
    if not any(q == name or q.startswith(name + ".") for q in out.values()):
        sys.exit(f"{name} is not a class or function of {cfile}")
    top = next(n for n in t.body if getattr(n, "name", None) == name)
    pre = name + "."
    return {k: (q[len(pre):] if isinstance(top, ast.ClassDef) else q)
            for k, q in out.items() if q == name or q.startswith(pre)}


def run(cfile, cname, pytest_args):
    import pytest
    sys.path.insert(0, os.getcwd())               # as `python -m pytest` does
    want = targets(cfile, cname)
    own = str(Path(cfile).resolve())
    root = str(Path.cwd().resolve()) + os.sep
    hits = {}

    def prof(frame, event, arg):
        if event != "call":
            return
        c = frame.f_code
        m = want.get((c.co_filename, c.co_firstlineno))
        if m is None:
            return
        b = frame.f_back
        while b is not None and not b.f_code.co_filename.startswith(root):
            b = b.f_back                      # outside the project (contextlib, functools, ...)
        if b is not None and b.f_code.co_filename == own and m.startswith("_") and m != "__init__":
            return                            # a private helper called from the class's own file
        while b is not None:
            bc = b.f_code
            if (bc.co_filename, bc.co_firstlineno) in want:
                return                        # one of the class's own functions: internal
            if bc.co_filename != own and bc.co_filename.startswith(root):
                break
            b = b.f_back                      # a wrapper in the class's file, or outside the project
        if b is None:
            return
        key = (b.f_code.co_filename, b.f_lineno, getattr(b.f_code, "co_qualname", b.f_code.co_name), m)
        hits[key] = hits.get(key, 0) + 1

    sys.setprofile(prof)
    threading.setprofile(prof)
    try:
        rc = pytest.main(pytest_args)
    finally:
        sys.setprofile(None)
        threading.setprofile(None)
    return rc, hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--diff")
    ap.add_argument("--tests-dir", default="tests")
    ap.add_argument("pytest_args", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cfile, _, cname = a.target.partition(":")
    args = a.pytest_args[1:] if a.pytest_args[:1] == ["--"] else a.pytest_args
    rc, hits = run(cfile, cname, args)
    cwd = os.getcwd()
    tests = str(Path(a.tests_dir).resolve())
    uses = []
    for (f, line, caller, member), n in sorted(hits.items()):
        uses.append({"file": os.path.relpath(f, cwd), "line": line, "caller": caller, "member": member,
                     "calls": n, "test": f.startswith(tests + os.sep)})
    edges = sorted({(u["file"] + ":" + u["caller"], u["member"]) for u in uses if not u["test"]})
    out = {"target": a.target, "file": cfile, "pytest_exit": int(rc), "uses": uses,
           "call_graph": [{"caller": c, "member": m} for c, m in edges]}
    if a.diff:
        static = {}
        for u in json.load(open(a.diff))["uses"]:
            if "/tests/" not in u["file"]:
                static.setdefault(os.path.normpath(os.path.relpath(u["file"], cwd)), set()).add(u["member"])
        seen = {}
        for u in uses:
            if not u["test"]:
                seen.setdefault(os.path.normpath(u["file"]), set()).add(u["member"])
        out["diff"] = {f: {"runtime_only": sorted(seen.get(f, set()) - static.get(f, set())),
                           "static_only": sorted(static.get(f, set()) - seen.get(f, set()))}
                       for f in sorted(set(seen) | set(static))}
    json.dump(out, open(a.out, "w"), indent=1)
    prod = [u for u in uses if not u["test"]]
    print(f"{len(prod)} production call sites, {len(uses) - len(prod)} from tests, "
          f"{len(edges)} caller->member edges; pytest exit {rc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
