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

--spec also lists the moves: each external module whose only concept-class
user is one class is proposed to move into it (its other users, external
modules, would then reach it through that class).

--spec writes a board spec for board.py: one box per class that encapsulates
a concept (--concepts: the concept map's Objects column; without it, every
class); a helper class folds into the concept class of its file, else into
its module; a module called by one concept class only is drawn inside it,
any other module is an external box; no folder boxes and no box around
everything; one arrow per pair of boxes, labelled with the members called,
each with a proof: one of
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


def owners(root, objs):
    """{class id: owner class id} for a class created and kept by exactly one
    other class (`self.x = Other(...)` in its body, also inside `a or b` and
    `a if c else b`). A class kept by several owners has none: it is shared."""
    by_name = {}
    for oid, o in objs.items():
        if o["kind"] == "class":
            by_name.setdefault(o["name"].split(".")[-1], []).append(oid)
    kept = {}
    for p in sorted(Path(root).rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        try:
            t = ast.parse(p.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for c in [n for n in ast.walk(t) if isinstance(n, ast.ClassDef)]:
            owner = f"{p}:{c.name}"
            if owner not in objs:
                continue
            for n in ast.walk(c):
                if not isinstance(n, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                if not any(isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name) and x.value.id == "self"
                           for x in targets) or n.value is None:
                    continue
                for call in [x for x in ast.walk(n.value) if isinstance(x, ast.Call)]:
                    f = call.func
                    name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
                    for kid in by_name.get(name, []):
                        if kid != owner:
                            kept.setdefault(kid, set()).add(owner)
    return {k: next(iter(v)) for k, v in kept.items() if len(v) == 1}


def concept_classes(map_file, objs):
    """The classes the concept map names in its Objects column (backticked
    names in the table rows), among those traced."""
    names = set()
    for line in Path(map_file).read_text().splitlines():
        cells = line.split("|")
        if line.startswith("|") and len(cells) > 4:
            names |= {n.split(".")[-1] if n[:1].isupper() and "." in n else n
                      for n in re.findall(r"`([^`]+)`", cells[4] if len(cells) > 5 else cells[-2])}
    return {oid for oid, o in objs.items() if o["kind"] == "class" and o["name"].split(".")[-1] in names}


def class_facts(root):
    """Per class id: its base names, and whether it is a value class (a
    dataclass, NamedTuple, Enum or TypedDict, or a class with no public
    method of its own)."""
    out = {}
    for p in sorted(Path(root).rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        try:
            t = ast.parse(p.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for c in [n for n in ast.walk(t) if isinstance(n, ast.ClassDef)]:
            bases = [ast.unparse(b).split(".")[-1] for b in c.bases]
            decos = [ast.unparse(d) for d in c.decorator_list]
            public = [f for f in c.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                      and not f.name.startswith("_")]
            value = (any("dataclass" in d for d in decos) or {"NamedTuple", "Enum", "IntEnum", "TypedDict"} & set(bases)
                     or not public)
            makes = {n.func.id if isinstance(n.func, ast.Name) else n.func.attr
                     for n in ast.walk(c) if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute))}
            out[f"{p}:{c.name}"] = {"bases": bases, "value": bool(value), "makes": makes}
        in_classes = {id(x) for c in ast.walk(t) if isinstance(c, ast.ClassDef) for x in ast.walk(c)}
        out[str(p)] = {"bases": [], "value": False,
                       "makes": {n.func.id if isinstance(n.func, ast.Name) else n.func.attr
                                 for n in ast.walk(t) if isinstance(n, ast.Call) and id(n) not in in_classes
                                 and isinstance(n.func, (ast.Name, ast.Attribute))}}
    return out


def importers(root):
    """{module file: {(file, enclosing class or None) that uses it}} for the
    modules under root: an import, or a string naming it (`python -m pkg.mod`,
    its file path), which is how a module run as a separate process is
    reached."""
    files = {p for p in Path(root).rglob("*.py") if "__pycache__" not in p.parts}

    def dotted(p):
        parts = list(p.with_suffix("").parts)
        return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)

    by_name = {dotted(p): str(p) for p in files}
    out = {}
    for p in files:
        try:
            t = ast.parse(p.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        here = dotted(p)
        pkg = here if p.name == "__init__.py" else here.rpartition(".")[0]
        owner_of = {}
        for c in [x for x in ast.walk(t) if isinstance(x, ast.ClassDef)]:
            for x in ast.walk(c):
                owner_of.setdefault(id(x), c.name)
        for n in ast.walk(t):
            mods = []
            if isinstance(n, ast.Import):
                mods = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom):
                base = n.module or ""
                if n.level:
                    up = pkg.split(".")[: len(pkg.split(".")) - (n.level - 1)]
                    base = ".".join(up + ([n.module] if n.module else []))
                mods = [base] + [f"{base}.{a.name}" for a in n.names]
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                mods = [n.value] if n.value in by_name else []
                mods += [m for m, f in by_name.items() if n.value == f]
            for m in mods:
                if m in by_name and by_name[m] != str(p):
                    out.setdefault(by_name[m], set()).add((str(p), owner_of.get(id(n))))
    return out


def spec_of(objs, edges, root, concepts=None):
    """The objects representation. Boxes are the classes that encapsulate a
    concept (`concepts`: the concept map's Objects column; without one, every
    class). A subclass of a concept class folds into its base's box; any other
    class folds into the concept class of its file, else into its module; a
    module's functions fold into the concept class of their file when that
    class is their only caller at run time. A value class goes inside the one
    box whose code constructs it. Another module is drawn inside a concept
    class when every file that uses it (imports it, or launches it as a
    process) belongs to that class, counted through what the class already
    owns, and the run shows no other caller (none at all is noted as
    untested); otherwise it is an external box. One arrow per pair."""
    facts = class_facts(root)
    cls = {oid for oid, o in objs.items() if o["kind"] == "class"}
    keep = concept_classes(concepts, objs) if concepts else set(cls)
    keep_by_name = {objs[k]["name"].split(".")[-1]: k for k in keep}
    for oid in sorted(keep):                  # a concept class that subclasses another folds into it
        base = next((keep_by_name[b] for b in facts.get(oid, {}).get("bases", []) if b in keep_by_name), None)
        if base and base != oid:
            keep.discard(oid)
    by_file = {}
    for oid in sorted(keep):
        by_file.setdefault(objs[oid]["file"], oid)
    box = {}
    for oid, o in objs.items():
        if oid in keep:
            box[oid] = oid
            continue
        base = next((keep_by_name[b] for b in facts.get(oid, {}).get("bases", [])
                     if b in keep_by_name and keep_by_name[b] in keep), None)
        if o["kind"] == "class":
            box[oid] = base or by_file.get(o["file"], o["file"])
        else:
            box[oid] = oid
    # a module's functions fold into the concept class of their file when, at
    # run time, that class (or its own parts) is their only caller
    run_callers = {}
    for e in edges:
        run_callers.setdefault(e["to"], set()).add(box.get(e["from"], e["from"]))
    for oid, o in objs.items():
        if o["kind"] == "module" and o["file"] in by_file:
            owner = by_file[o["file"]]
            if run_callers.get(oid, set()) <= {owner}:
                box[oid] = owner
    # a value class goes inside the one box (class or module) whose code constructs it
    value_in = {}
    for v in [k for k in keep if facts.get(k, {}).get("value")]:
        short = objs[v]["name"].split(".")[-1]
        makers = {box.get(x, x) for x, f in facts.items() if short in f.get("makes", ()) and box.get(x, x) != v}
        if len(makers) == 1:
            value_in[v] = next(iter(makers))
    pairs = {}
    for e in edges:
        a, b = box.get(e["from"], e["from"]), box.get(e["to"], e["to"])
        if a == b:
            continue
        pairs.setdefault((a, b), set()).update(e["members"])
    imp = importers(root)
    callers = {}
    for (a, b) in pairs:
        callers.setdefault(b, set()).add(a)
    mod_ids = {oid for oid, o in objs.items() if o["kind"] == "module" and box.get(oid) == oid}
    inside = {}

    def up(b):
        while b in inside:
            b = inside[b]
        return b

    def box_of_use(use):
        """The box a use belongs to: the class whose code contains it (its
        box), else the box the file's module code is drawn in, followed
        through ownership."""
        f, cname = use
        b = box.get(f"{f}:{cname}") if cname else None
        b = b or box.get(f, f)
        while b in inside:
            b = inside[b]
        return b

    for _ in range(3):                        # ownership through owned modules settles in a few passes
        for m in sorted(mod_ids):
            users = {box_of_use(u) for u in imp.get(objs[m]["file"], set())}
            run = {up(c) for c in callers.get(m, set())}
            owner = next(iter(users)) if len(users) == 1 else None
            if owner in keep and run <= {owner}:
                inside[m] = owner
            else:
                inside.pop(m, None)
    untested = {m for m in inside if m in mod_ids and not callers.get(m)}
    for v, owner in value_in.items():
        inside[v] = owner
    nodes, used = [], {x for pair in pairs for x in pair} | set(inside)
    for oid in sorted(used):
        o = objs.get(oid, {"name": Path(oid).stem, "file": oid, "kind": "module"})
        f = Path(o["file"])
        if oid in keep:
            subs = sorted(objs[k]["name"] for k, b in box.items() if b == oid and k != oid and objs.get(k, {}).get("kind") == "class"
                          and any(x == objs[oid]["name"].split(".")[-1] for x in facts.get(k, {}).get("bases", [])))
            nodes.append({"id": oid, "name": o["name"], "kind": "object", "parent": inside.get(oid),
                          "members": [{"vis": "+", "name": "subclasses: " + ", ".join(subs)}] if subs else [],
                          "note": o["file"]})
        else:
            label = (str(f.parent) + "/") if f.name == "__init__.py" else f.name
            nodes.append({"id": oid, "name": label, "kind": "module" if oid in inside else "external",
                          "parent": inside.get(oid),
                          "note": o["file"] + ("  (untested at run time)" if oid in untested else "")})
    parent = {n["id"]: n["parent"] for n in nodes}

    def nested(a, b):
        p = parent.get(b)
        while p:
            if p == a:
                return True
            p = parent.get(p)
        return False

    E = []
    for (a, b), ms in sorted(pairs.items()):
        if nested(a, b) or nested(b, a):
            continue                          # ownership is the nesting
        ms = sorted(ms)
        label = " · ".join(ms[:3]) + (f"  (+{len(ms) - 3})" if len(ms) > 3 else "")
        callee = objs.get(b, {"name": Path(b).stem, "kind": "module", "file": b})
        names = [m for m in ms if not m.startswith("__")] + [callee["name"].split(".")[-1], Path(callee["file"]).stem]
        pattern = r"\b(" + "|".join(re.escape(n) for n in names) + r")\b"
        caller_file = objs.get(a, {"file": a})["file"]
        try:
            named = re.search(pattern, Path(caller_file).read_text(errors="replace")) is not None
        except OSError:
            named = False
        proof = ({"file": caller_file, "pattern": pattern} if named
                 else {"observed": "called at run time through a subclass, a callback or an injected function"})
        E.append({"from": a, "to": b, "kind": "calls", "label": label + ("" if named else "  [run time only]"),
                  "proof": [proof]})
    # moves: an external module whose only concept-class user is one class
    # (its other users are external modules, or none) belongs in that class
    moves = []
    for oid in sorted(n["id"] for n in nodes if n["kind"] == "external"):
        users = ({box_of_use(u) for u in imp.get(objs.get(oid, {"file": oid})["file"], set())}
                 | {up(c) for c in callers.get(oid, set())})
        users.discard(oid)
        concept_users = users & keep
        if len(concept_users) == 1:
            owner = next(iter(concept_users))
            others = sorted(users - {owner})
            moves.append({"module": oid, "into": owner, "other_users": others,
                          "why": (f"{objs[owner]['name']} is its only concept-class user"
                                  + (f"; {len(others)} external module(s) also use it and would reach it through "
                                     f"{objs[owner]['name']}" if others else ""))})
    return {"name": f"{root} — objects", "nodes": nodes, "edges": E, "moves": moves}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--spec")
    ap.add_argument("--concepts", help="the concept map (markdown); its Objects column picks the classes drawn")
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
        spec = spec_of(objs, edges, a.root, a.concepts)
        json.dump(spec, open(a.spec, "w"), indent=1)
        for m in spec["moves"]:
            print(f"move {m['module']} into {m['into']}: {m['why']}", file=sys.stderr)
    print(f"{len({e['from'] for e in edges} | {e['to'] for e in edges})} objects, {len(edges)} object pairs; "
          f"pytest exit {rc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
