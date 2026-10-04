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
        if p.name == "__init__.py" and not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                                               for n in t.body):
            continue                          # a package __init__ that only re-exports uses nothing itself
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
                mods += [n.value + ".__main__"] if n.value + ".__main__" in by_name else []
                mods += [m for m, f in by_name.items() if n.value == f]
            for m in mods:
                if m in by_name and by_name[m] != str(p):
                    out.setdefault(by_name[m], set()).add((str(p), owner_of.get(id(n))))
    return out


def map_rows(map_file):
    """[(objects cell text)] for each row of the concept map's table."""
    rows = []
    for line in Path(map_file).read_text().splitlines():
        cells = [c.strip() for c in line.split("|")]
        if line.startswith("|") and len(cells) > 6 and "`" in cells[4]:
            rows.append(cells[4])
    return rows


def concept_classes(map_file, objs):
    """The classes the concept map names in its Objects column."""
    names = {n.split(".")[0] if n[:1].isupper() else n for row in map_rows(map_file)
             for n in re.findall(r"`([^`]+)`", row)}
    return {oid for oid, o in objs.items() if o["kind"] == "class" and o["name"].split(".")[-1] in names}


def declared(map_file, keep, objs, root):
    """{module path: owner class id or None} from the concept map: a module
    or folder named in rows that each name exactly one concept class, the
    same one, belongs to it; one marked "(external)" is external. A module
    the rows assign differently is left to the other rules."""
    out = {}
    cls_by_name = {objs[k]["name"].split(".")[-1]: k for k in keep}
    files = [str(p) for p in Path(root).rglob("*.py") if "__pycache__" not in p.parts]

    def match(name):
        name = name.rstrip("/")
        return [f for f in files if f.endswith("/" + name) or f.endswith("/" + name + ".py")
                or ("/" + name + "/") in f or f.endswith("/" + name + "/__init__.py")]

    votes = {}
    for row in map_rows(map_file):
        names = re.findall(r"`([^`]+)`(\s*\(external\))?", row)
        owners = {cls_by_name[n.split(".")[0]] for n, _ in names if n.split(".")[0] in cls_by_name}
        for n, ext in names:
            if n.split(".")[0] in cls_by_name:
                continue
            for f in match(n):
                votes.setdefault(f, set()).add(None if ext else (next(iter(owners)) if len(owners) == 1 else "?"))
    for f, v in votes.items():
        if len(v) == 1 and "?" not in v:
            out[f] = next(iter(v))            # every row naming it agrees
    return out


def spec_of(objs, edges, root, concepts=None):
    """The objects representation. Placement comes from structure and the
    concept map, never from usage; usage only lists the bypasses.

    1. The concept map declares it: a module named in a row with exactly one
       concept class belongs to that class; one marked "(external)" is
       external.
    2. The file holds a concept class: its functions and helper classes
       belong to that class (the class named after the file when it holds
       several; the others nest inside it).
    3. The package names its class: any other module belongs to the concept
       class named after its folder (`cell/` -> Cell, `variants/` ->
       Variant), or the nearest enclosing folder's; never the root's.
    4. A concept class created and kept by exactly one other nests in it; a
       subclass folds into its base.
    5. Everything else is external.
    6. A use of an owned module or class from outside its owner is a bypass,
       listed as a move. One arrow per pair of boxes."""
    rootp = Path(root)
    facts = class_facts(root)
    keep = concept_classes(concepts, objs) if concepts else {k for k, o in objs.items() if o["kind"] == "class"}
    byname = {objs[k]["name"].split(".")[-1]: k for k in keep}
    for k in sorted(keep):                    # 4b: a subclass folds into its base
        if any(b in byname and byname[b] != k for b in facts.get(k, {}).get("bases", [])):
            keep.discard(k)
    decl = declared(concepts, keep, objs, root) if concepts else {}

    def primary_of_file(f):
        ks = [k for k in keep if objs[k]["file"] == f]
        stem = Path(f).parent.name if Path(f).name == "__init__.py" else Path(f).stem
        named = [k for k in ks if objs[k]["name"].split(".")[-1].lower() == stem.replace("_", "").lower()]
        return (named or sorted(ks) or [None])[0]

    def primary_of_dir(d):
        for k in sorted(keep):
            n = objs[k]["name"].split(".")[-1].lower()
            if Path(objs[k]["file"]).parent == d and d.name.lower() in (n, n + "s"):
                return k
        return None

    def owner_of_file(f):
        """(owner class id or None, rule) for the code in file f."""
        p = Path(f)
        for key in (f, *[str(x) for x in p.parents]):
            if key in decl:
                return decl[key], 1
        c = primary_of_file(f)
        if c:
            return c, 2
        for d in p.parents:
            if d == rootp or rootp not in d.parents:
                break
            c = primary_of_dir(d)
            if c:
                return c, 3
        return None, 5

    # every traced object -> the box it is drawn in, and that box's owner
    box, parent = {}, {}
    for oid, o in objs.items():
        if oid in keep:
            box[oid] = oid
            prim = primary_of_file(o["file"])
            dec = decl.get(o["file"])
            if dec and dec != oid:
                parent[oid] = dec             # rule 1: its file is declared another class's
            elif prim and prim != oid:
                parent[oid] = prim            # rule 2: a second concept class in a file nests in its primary
            continue
        if o["kind"] == "class":
            base = next((byname[b] for b in facts.get(oid, {}).get("bases", []) if b in byname and byname[b] in keep), None)
            if base:
                box[oid] = base
                continue
        owner, rule = owner_of_file(o["file"])
        if rule == 2:
            box[oid] = owner                  # helpers of the class in their file
        elif owner is not None:
            box[oid] = o["file"]              # a module of its owner's package: drawn inside it
            parent[o["file"]] = owner
        else:
            box[oid] = o["file"]              # external
    kept_by = owners(root, objs)
    for k in sorted(keep):                    # 4a: composition
        o = kept_by.get(k)
        if o in keep and o != k and k not in parent:
            parent[k] = o
    pairs = {}
    for e in edges:
        a, b = box.get(e["from"], e["from"]), box.get(e["to"], e["to"])
        if a != b:
            pairs.setdefault((a, b), set()).update(e["members"])

    def top(b):
        while b in parent:
            b = parent[b]
        return b

    def within(a, b):                         # a is b, or holds b
        while b is not None:
            if a == b:
                return True
            b = parent.get(b)
        return False

    used = {x for pair in pairs for x in pair}
    frontier = set(used)
    while frontier:
        frontier = {parent[x] for x in frontier if x in parent} - used
        used |= frontier
    nodes = []
    for b in sorted(used):
        if b in objs and objs[b]["kind"] == "class":
            o = objs[b]
            subs = sorted(objs[k]["name"] for k, v in box.items() if v == b and k != b
                          and objs.get(k, {}).get("kind") == "class"
                          and o["name"].split(".")[-1] in facts.get(k, {}).get("bases", []))
            nodes.append({"id": b, "name": o["name"], "kind": "object", "parent": parent.get(b),
                          "members": [{"vis": "+", "name": "subclasses: " + ", ".join(subs)}] if subs else [],
                          "note": o["file"]})
        else:
            f = Path(b)
            label = (str(f.parent) + "/") if f.name == "__init__.py" else f.name
            nodes.append({"id": b, "name": label, "kind": "module" if b in parent else "external",
                          "parent": parent.get(b), "note": b})
    E = []
    for (a, b), ms in sorted(pairs.items()):
        if within(a, b) or within(b, a):
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
    # 6: bypasses, from imports and from the run
    imp = importers(root)
    moves = {}

    def box_of_use(use):
        f, cname = use
        return box.get(f"{f}:{cname}") if cname and f"{f}:{cname}" in box else box.get(f, f)

    for b in used:
        if b not in parent:
            continue
        owner = top(b)
        users = set()
        if b in objs:
            users |= {box.get(e["from"], e["from"]) for e in edges if box.get(e["to"]) == b}
        else:
            users |= {box_of_use(u) for u in imp.get(b, set())}
            users |= {box.get(e["from"], e["from"]) for e in edges if box.get(e["to"]) == b}
        out = sorted(u for u in users if top(u) != owner)
        if out:
            moves[b] = {"module": b, "into": owner, "other_users": out,
                        "why": f"belongs to {objs[owner]['name']} (rule {owner_of_file(objs.get(b, {'file': b})['file'])[1]}); "
                               f"{len(out)} user(s) outside it should go through {objs[owner]['name']}"}
    # flags: a part called directly from outside its owner (bypassed), and a
    # module that sits in an owner's folder but belongs to no class (misplaced)
    N = {n["id"]: n for n in nodes}
    for b, m in moves.items():
        if b in N:
            N[b]["flag"] = "called directly from outside " + objs[m["into"]]["name"] + ": " + ", ".join(
                Path(u.split(":")[0]).name + (":" + u.split(":")[1] if ":" in u else "") for u in m["other_users"])
    for n in nodes:
        if n["kind"] == "external" and owner_of_file(n["id"])[1] == 1:
            p = Path(n["id"])
            if any(primary_of_dir(d) for d in p.parents if d != rootp and rootp in d.parents):
                n["flag"] = f"misplaced: in {p.parent}/ but no class owns it"
    for e in E:
        b = e["to"]
        if b in parent and not within(top(b), e["from"]):
            e["flag"] = "bypass: reaches " + N[b]["name"] + " directly, not through " + objs[top(b)]["name"]
    return {"name": f"{root} — objects", "nodes": nodes, "edges": E, "moves": [moves[k] for k in sorted(moves)]}


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
