#!/usr/bin/env python3
"""Which objects call which, recorded while a test suite runs: the object
graph of a whole codebase, one arrow per pair of objects.

    trace_objects.py --root path/to/pkg --out objects.json [--spec board.json] \
        [--worklist worklist.txt] [--tests-dir tests] -- <pytest arguments>

An object is a class, or a module that has functions of its own (in Python a
module is an object too). Every function under --root belongs to one: a
method to its class, a top-level function to its module. The profiler
records each call from one object into another; calls inside one object are
not recorded. Closures (decorator wrappers, lambdas) and frames outside the
project are walked past, so a call is charged to the object that made it.

--worklist FILE (with --spec) writes the refactoring worklist: private
access (red) first, then owner bypasses and shared data (amber); one entry
per part, the most reached first, with every arrow that reaches it and its call sites as file:line,
read from the code (the imports naming the part, and the uses of its class
name, of names imported from it and of the members called; data: the lines
matching the resource's pattern). An attribute is matched by name, so a
same-named attribute of another object can show up. Route the first entry through its owner,
re-run, take the next. The colours are METHOD.md's (Arrow colours).

--spec writes a board spec for board.py, the code as it is: one box per
class with behaviour of its own, placed by the code's structure (see
spec_of); --concepts, optional, lays a concept map over it; --data (or the
concept map's own Resources table) names the data; the
spec also lists each owned part used from outside its owner, with those
users (the bypasses). No folder boxes and no box around everything; one
arrow per pair of boxes, labelled with the members called, each with a
proof: one of
those members, or the callee's name, appears in the caller's file; when
neither does (a subclass, a callback, an injected function), the arrow is
marked "[run time only]" and its proof is the observation itself.

Run it with the project's own interpreter from the project root (it imports
the project and needs pytest). It maps only what the tests run, so it needs
tests with good coverage; plain attribute, constant and data reads, and calls
inside other processes, are not seen. A module the code starts as its own
process is drawn as a process box from its command line (processes()), and
data reached through a helper that returns its path is its callers'
(path_helpers()).
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


def _called_name(call):
    f = call.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None


def _top_calls(expr):
    """The calls an expression evaluates to: itself, or the branches of
    `a or b` and `a if c else b`; never a call nested in an argument."""
    if isinstance(expr, ast.Call):
        return [expr]
    if isinstance(expr, ast.BoolOp):
        return [c for v in expr.values for c in _top_calls(v)]
    if isinstance(expr, ast.IfExp):
        return _top_calls(expr.body) + _top_calls(expr.orelse)
    return []


def _private(name):
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def private_reaches(root, objs):
    """Every reach of a private name from code under root, read from the
    code: an attribute `x._name` (not on self, cls or super()) whose name
    exactly one project class or module defines (a method, `self._name =`, a
    top-level def or assignment), an import of a private name, and an import
    through a private module or package (`pkg._impl`). Each is
    {"file", "cls", "line", "name", "definer", "kind", "holds"}: kind
    "class" (definer a class id; holds the classes the attribute is built
    from), "module" (definer a module file) or "path" (definer the private
    module's file, name its private segment)."""
    files = sorted(p for p in Path(root).rglob("*.py") if "__pycache__" not in p.parts)
    trees = []
    for p in files:
        try:
            trees.append((p, ast.parse(p.read_text())))
        except (SyntaxError, UnicodeDecodeError):
            continue
    by_name = {}
    for oid, o in objs.items():
        if o["kind"] == "class":
            by_name.setdefault(o["name"].split(".")[-1], []).append(oid)

    def made(expr):
        return {nm for c in ast.walk(expr) if isinstance(c, ast.Call) and (nm := _called_name(c)) in by_name}

    defs = {}                                 # private name -> {(kind, definer)}
    holds = {}
    for p, t in trees:
        for c in [n for n in ast.walk(t) if isinstance(n, ast.ClassDef)]:
            cid = f"{p}:{c.name}"
            for n in ast.walk(c):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _private(n.name) and n in c.body:
                    defs.setdefault(n.name, set()).add(("class", cid))
                elif isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None:
                    for x in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                        if isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name) and x.value.id == "self" \
                                and _private(x.attr):
                            defs.setdefault(x.attr, set()).add(("class", cid))
                            holds.setdefault((cid, x.attr), set()).update(made(n.value))
        for n in t.body:
            names = [n.name] if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else \
                [x.id for x in (n.targets if isinstance(n, ast.Assign) else [n.target]) if isinstance(x, ast.Name)] \
                if isinstance(n, (ast.Assign, ast.AnnAssign)) else []
            for nm in names:
                if _private(nm):
                    defs.setdefault(nm, set()).add(("module", str(p)))

    def dotted(p):
        parts = list(p.with_suffix("").parts)
        return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)

    mod_file = {dotted(p): str(p) for p in files}
    out = []
    for p, t in trees:
        spans = [(c.lineno, c.end_lineno, c.name) for c in ast.walk(t) if isinstance(c, ast.ClassDef)]

        def cls_at(ln):
            inner = [c for c in spans if c[0] <= ln <= c[1]]
            return min(inner, key=lambda c: c[1] - c[0])[2] if inner else None

        here = dotted(p)
        pkg = here if p.name == "__init__.py" else here.rpartition(".")[0]
        for n in ast.walk(t):
            if isinstance(n, ast.Attribute) and _private(n.attr):
                v = n.value
                if isinstance(v, ast.Name) and v.id in ("self", "cls") or \
                        isinstance(v, ast.Call) and _called_name(v) == "super":
                    continue
                d = defs.get(n.attr, set())
                if len(d) == 1:
                    kind, definer = next(iter(d))
                    out.append({"file": str(p), "cls": cls_at(n.lineno), "line": n.lineno, "name": n.attr,
                                "definer": definer, "kind": kind, "holds": sorted(holds.get((definer, n.attr), ()))})
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                if isinstance(n, ast.Import):
                    mods = [(a.name, None) for a in n.names]
                else:
                    base = n.module or ""
                    if n.level:
                        up = pkg.split(".")[: len(pkg.split(".")) - (n.level - 1)]
                        base = ".".join(up + ([n.module] if n.module else []))
                    mods = [(base, a.name) for a in n.names]
                for base, name in mods:
                    full = f"{base}.{name}" if name and f"{base}.{name}" in mod_file else base
                    segs = full.split(".")
                    for i, seg in enumerate(segs):
                        if _private(seg) and ".".join(segs[: i + 1]) in mod_file:
                            out.append({"file": str(p), "cls": cls_at(n.lineno), "line": n.lineno, "name": seg,
                                        "definer": mod_file[".".join(segs[: i + 1])], "kind": "path",
                                        "holds": [], "container": str(Path(*segs[:i]))})
                            break
                    else:
                        if name and _private(name) and base in mod_file:
                            out.append({"file": str(p), "cls": cls_at(n.lineno), "line": n.lineno, "name": name,
                                        "definer": mod_file[base], "kind": "module", "holds": []})
    return out


def reexports(root, objs, owner_of_file, primary_of_dir):
    """{(part file, owner class id): {name}}: the names an owner's own module,
    or its package's `__init__.py`, imports at module level from one of its
    parts (`from ._stock import count`): what the owner exposes in its
    module's namespace is its interface."""
    files = sorted(p for p in Path(root).rglob("*.py") if "__pycache__" not in p.parts)

    def dotted(p):
        parts = list(p.with_suffix("").parts)
        return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)

    mod_file = {dotted(p): str(p) for p in files}
    out = {}
    for p in files:
        owner = owner_of_file(str(p))[0]
        if owner is None and p.name == "__init__.py" and str(p) in objs:
            owner = str(p)                    # a package with no class: its own module
        elif owner is None or not (objs[owner]["file"] == str(p) or
                                   (p.name == "__init__.py" and primary_of_dir(p.parent) == owner)):
            continue
        try:
            t = ast.parse(p.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        here = dotted(p)
        pkg = here if p.name == "__init__.py" else here.rpartition(".")[0]
        for n in t.body:
            if not isinstance(n, ast.ImportFrom):
                continue
            base = n.module or ""
            if n.level:
                up = pkg.split(".")[: len(pkg.split(".")) - (n.level - 1)]
                base = ".".join(up + ([n.module] if n.module else []))
            if base in mod_file:
                out.setdefault((mod_file[base], owner), set()).update(a.asname or a.name for a in n.names)
    return out


def processes(root):
    """Every command line in the code that starts a project module as its own
    Python process: a list or tuple with "-m" followed by a project module
    (`[sys.executable, "-m", "pkg.mod", ...]`), or an interpreter followed by
    the file itself (`[sys.executable, os.path.abspath(__file__), ...]`).
    Only argv literals count, never a mention in a docstring or a log line.
    Each is {"file", "cls", "line", "module", "entry", "pattern"}."""
    files = sorted(p for p in Path(root).rglob("*.py") if "__pycache__" not in p.parts)

    def dotted(p):
        parts = list(p.with_suffix("").parts)
        return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)

    mod_file = {dotted(p): str(p) for p in files}
    out = []
    for p in files:
        try:
            t = ast.parse(p.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        spans = [(c.lineno, c.end_lineno, c.name) for c in ast.walk(t) if isinstance(c, ast.ClassDef)]

        def cls_at(ln):
            inner = [c for c in spans if c[0] <= ln <= c[1]]
            return min(inner, key=lambda c: c[1] - c[0])[2] if inner else None

        for n in ast.walk(t):
            if not isinstance(n, (ast.List, ast.Tuple)):
                continue
            el = n.elts
            for i, x in enumerate(el[:-1]):
                nxt = el[i + 1]
                if isinstance(x, ast.Constant) and x.value == "-m" and isinstance(nxt, ast.Constant) \
                        and isinstance(nxt.value, str):
                    mod = nxt.value
                    entry = mod_file.get(mod + ".__main__") or mod_file.get(mod)
                    if entry:
                        out.append({"file": str(p), "cls": cls_at(n.lineno), "line": n.lineno, "module": mod,
                                    "entry": entry, "pattern": r"[\"']-m[\"']\s*,\s*[\"']" + re.escape(mod) + r"[\"']"})
                    break
                if i == 0 and "__file__" in ast.unparse(nxt) and (
                        "executable" in ast.unparse(x) or (isinstance(x, ast.Constant) and str(x.value).startswith("python"))):
                    out.append({"file": str(p), "cls": cls_at(n.lineno), "line": n.lineno, "module": dotted(p),
                                "entry": str(p), "pattern": r"__file__"})
                    break
    return out


def owners(root, objs):
    """{class id: owner class id} for a class created and kept by exactly one
    other class (`self.x = Other(...)` in its body, also inside `a or b` and
    `a if c else b`, or `self.x = make(...)` where `make` is a project
    function whose `return Other(...)` builds it). A class kept by several
    owners has none: it is shared."""
    by_name = {}
    for oid, o in objs.items():
        if o["kind"] == "class":
            by_name.setdefault(o["name"].split(".")[-1], []).append(oid)
    trees = []
    for p in sorted(Path(root).rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        try:
            trees.append((p, ast.parse(p.read_text())))
        except (SyntaxError, UnicodeDecodeError):
            continue
    makes = {}                                # factory name -> class names its returns build
    for _, t in trees:
        for fn in [n for n in ast.walk(t) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            if fn.name in by_name:
                continue
            for r in [n for n in ast.walk(fn) if isinstance(n, ast.Return) and n.value is not None]:
                makes.setdefault(fn.name, set()).update(
                    nm for c in _top_calls(r.value) if (nm := _called_name(c)) in by_name)
    kept = {}
    for p, t in trees:
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
                    name = _called_name(call)
                    names = [name] if name in by_name else sorted(makes.get(name, ()))
                    for kid in (k for nm in names for k in by_name.get(nm, [])):
                        if kid != owner:
                            kept.setdefault(kid, set()).add(owner)
    return {k: next(iter(v)) for k, v in kept.items() if len(v) == 1}


def _handed_out(fn, built, by_name):
    """Class names a function returns: built in a return (`built(expr)`),
    bound to a name it returns (`x = C()`, `xs.append(C(...))`), or named
    in its return annotation (`-> list[C]`)."""
    bound = {}
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    bound.setdefault(t.id, set()).update(built(n.value))
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name) \
                and n.func.attr in ("append", "add", "extend", "insert"):
            for a in n.args:
                bound.setdefault(n.func.value.id, set()).update(built(a))
    out = set()
    for r in [n for n in ast.walk(fn) if isinstance(n, ast.Return) and n.value is not None]:
        out |= built(r.value)
        if isinstance(r.value, ast.Name):
            out |= bound.get(r.value.id, set())
    if fn.returns is not None:
        out |= {x.id if isinstance(x, ast.Name) else x.attr for x in ast.walk(fn.returns)
                if isinstance(x, (ast.Name, ast.Attribute)) and (x.id if isinstance(x, ast.Name) else x.attr) in by_name}
    return out


def exposed(root, objs):
    """{(owner class id, class name)}: the classes an owner hands out, through
    a public attribute (`self.x = Other(...)`, directly or through a project
    factory) or a public method or property that returns one (built in the
    return, collected in a name it returns, or named in its annotation), or
    is named after it (`def workspace` -> Workspace). What is public is the
    owner's interface: a call into a part it hands out goes through the owner."""
    by_name = {}
    for oid, o in objs.items():
        if o["kind"] == "class":
            by_name.setdefault(o["name"].split(".")[-1], []).append(oid)
    snake = {re.sub(r"(?<!^)(?=[A-Z])", "_", n).lower(): n for n in by_name}
    trees = []
    for p in sorted(Path(root).rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        try:
            trees.append((p, ast.parse(p.read_text())))
        except (SyntaxError, UnicodeDecodeError):
            continue
    def direct(expr):
        return {nm for c in ast.walk(expr) if isinstance(c, ast.Call) and (nm := _called_name(c)) in by_name}

    makes = {}
    for _, t in trees:
        for fn in [n for n in ast.walk(t) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            makes.setdefault(fn.name, set()).update(_handed_out(fn, direct, by_name))

    def built(expr):
        out = set()
        for call in [x for x in ast.walk(expr) if isinstance(x, ast.Call)]:
            name = _called_name(call)
            out |= {name} if name in by_name else makes.get(name, set())
        return out

    out = set()
    for p, t in trees:
        for c in [n for n in ast.walk(t) if isinstance(n, ast.ClassDef)]:
            owner = f"{p}:{c.name}"
            if owner not in objs:
                continue
            for n in ast.walk(c):
                if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None:
                    targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                    if any(isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name) and x.value.id == "self"
                           and not x.attr.startswith("_") for x in targets):
                        out |= {(owner, nm) for nm in built(n.value)}
            for f in c.body:
                if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and not f.name.startswith("_"):
                    out |= {(owner, nm) for nm in _handed_out(f, built, by_name)}
                    if f.name in snake:
                        out.add((owner, snake[f.name]))
    return out


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


def concept_rows(map_file):
    """[(concept, [code names])] for each row of the concept map's table
    (Concept | Description | Aliases | Objects | Concerns)."""
    rows = []
    for line in Path(map_file).read_text().splitlines():
        cells = [c.strip() for c in line.split("|")]
        if line.startswith("|") and len(cells) > 6 and "`" in cells[4]:
            rows.append((cells[1].strip("* "), re.findall(r"`([^`]+)`", cells[4])))
    return rows


GENERIC = {"path", "get", "open", "read", "write", "load", "save", "run", "main", "name"}
PATH_CALLS = {"Path", "PurePath", "joinpath", "with_name", "with_suffix", "resolve", "expanduser", "join"}


def _pathish(v):
    """Is the expression a path, or a command line, rather than something
    computed from one (a test, a read, a command's output)?"""
    if isinstance(v, ast.BinOp) and isinstance(v.op, ast.Div):
        return True
    if isinstance(v, (ast.List, ast.Tuple, ast.JoinedStr)):
        return True
    if isinstance(v, ast.Constant) and isinstance(v.value, str):
        return True
    return isinstance(v, ast.Call) and _called_name(v) in PATH_CALLS


def path_helpers(root, res_rows):
    """{function name: (resource index, file, enclosing class or None)}: the
    project functions that return a resource's path: a return expression, or
    the name it returns, matches the resource's pattern.
    Their callers are the ones that read or write it. Matched by name, so a
    name two functions share, or a generic one, is left out."""
    found, seen = {}, {}
    for p in sorted(Path(root).rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        try:
            text = p.read_text()
            t = ast.parse(text)
        except (SyntaxError, UnicodeDecodeError):
            continue
        owner_of = {}
        for c in [x for x in ast.walk(t) if isinstance(x, ast.ClassDef)]:
            for f in c.body:
                owner_of[id(f)] = c.name
        for fn in ast.walk(t):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            seen[fn.name] = seen.get(fn.name, 0) + 1
            if fn.name.lstrip("_") in GENERIC:
                continue
            bound = {}                        # name -> the expression it was assigned
            for x in ast.walk(fn):
                if isinstance(x, ast.Assign):
                    for tgt in x.targets:
                        if isinstance(tgt, ast.Name):
                            bound[tgt.id] = x.value
            returned = []
            for x in ast.walk(fn):
                if isinstance(x, ast.Return) and x.value is not None:
                    v = bound.get(x.value.id, x.value) if isinstance(x.value, ast.Name) else x.value
                    if _pathish(v):
                        returned.append(ast.get_source_segment(text, v) or "")
            for ri, (_n, _k, _o, pats) in enumerate(res_rows):
                if any(re.search(pat, r) for pat in pats for r in returned):
                    found[fn.name] = (ri, str(p), owner_of.get(id(fn)))
                    break
    return {k: v for k, v in found.items() if seen.get(k) == 1}


def resource_rows(map_file):
    """[(name, kind, declared owner or None, [regex])] from the concept
    map's resources table (columns Resource | Kind | Owner | Reached by).
    The declared owner is not used: the board's owner is the data's only
    writer in the code."""
    rows, on = [], False
    for line in Path(map_file).read_text().splitlines():
        cells = [c.strip() for c in line.split("|")]
        if line.startswith("|") and len(cells) >= 6 and cells[1] == "Resource" and cells[4].startswith("Reached"):
            on = True
            continue
        if on and line.startswith("|") and len(cells) >= 6:
            if set(cells[1]) <= set("-: "):
                continue
            owner = re.findall(r"`([^`]+)`", cells[3])
            rows.append((cells[1].replace("`", ""), cells[2], owner[0] if owner else None,
                         re.findall(r"`([^`]+)`", cells[4])))
        elif on and not line.startswith("|"):
            on = False
    return rows


WRITES = re.compile(r"write_text|write_bytes|\.write\(|mkdir|rename|unlink|touch|\.save\(|open\([^)]*[\"'][wa]")


def is_exception(k, facts, seen=()):
    bases = facts.get(k, {}).get("bases", [])
    return any(b in ("BaseException", "Exception") or b.endswith(("Error", "Exception", "Warning")) for b in bases)


def spec_of(objs, edges, root, concepts=None, data=None):
    """The objects representation of the code as it is. Boxes and placement
    come from the code's structure, never from usage (usage only lists the
    bypasses) and never from the concept map (concepts do not translate
    into classes: the map is an overlay that informs the design, which the
    person makes on the board).

    A box is a class with behaviour of its own: a public method, and not a
    value class (dataclass, NamedTuple, Enum, TypedDict) or an exception.

    1. The file holds a box class: its functions and helper classes belong
       to that class (the class named after the file when it holds several;
       the others nest inside it). Several and none named after the file:
       no owner class, each stands alone.
    2. The package names its class: any other module, and a box class that
       rule 3 does not place, belongs to the box class named after its
       folder (`cell/` -> Cell, `variants/` -> Variant), or the nearest
       enclosing folder's; never the root's. A package with no class
       holds its modules in its own `__init__` module, when that has code.
    3. A box class created and kept by exactly one other nests in it
       (`self.x = Other(...)`, or through a project factory function whose
       `return Other(...)` builds it); a subclass folds into its base.
    4. Everything else is external.
    5. A use of an owned module or class from outside its owner is an owner
       bypass (amber), listed as a move, unless the owner hands it out (a
       public attribute, method or property, or an object a public method
       returns: what is public is the owner's interface). A private name
       reached from outside its Python container is private access (red).
       One arrow per pair of boxes.

    Data (a Resources table names it and how code reaches it: `data`, or the
    concept map's own table): its
    owner is its only writer in the code (a write anywhere in the function
    that reaches it); a reach from anyone else is an
    owner bypass; several writers mean no single owner, and each write is
    flagged as shared.

    The concept map, when given, is an overlay: each box is labelled with
    the concepts whose Objects cell names it, and a concept spread over
    several boxes or a box carrying several concepts is flagged
    ("concept: ..."); a concept naming nothing in the code is listed under
    "unmapped". None of it moves a box."""
    rootp = Path(root)
    facts = class_facts(root)
    keep = {k for k, o in objs.items() if o["kind"] == "class"
            and not facts.get(k, {}).get("value") and not is_exception(k, facts)}
    byname = {objs[k]["name"].split(".")[-1]: k for k in keep}
    for k in sorted(keep):                    # 3b: a subclass folds into its base
        if any(b in byname and byname[b] != k for b in facts.get(k, {}).get("bases", [])):
            keep.discard(k)

    def primary_of_file(f):
        ks = [k for k in keep if objs[k]["file"] == f]
        stem = Path(f).parent.name if Path(f).name == "__init__.py" else Path(f).stem
        named = [k for k in ks if objs[k]["name"].split(".")[-1].lower() == stem.replace("_", "").lower()]
        return (named or (ks if len(ks) == 1 else []) or [None])[0]

    def primary_of_dir(d):
        for k in sorted(keep):
            n = objs[k]["name"].split(".")[-1].lower()
            if Path(objs[k]["file"]).parent == d and d.name.lower() in (n, n + "s"):
                return k
        return None

    def owner_of_file(f):
        """(owner class id or None, rule) for the code in file f."""
        p = Path(f)
        c = primary_of_file(f)
        if c:
            return c, 1
        for d in p.parents:
            if d == rootp or rootp not in d.parents:
                break
            c = primary_of_dir(d)
            if c:
                return c, 2
        return None, 4

    # every traced object -> the box it is drawn in, and that box's owner
    box, parent, sub_of = {}, {}, {}
    for oid, o in objs.items():
        if oid in keep:
            box[oid] = oid
            prim = primary_of_file(o["file"])
            if prim and prim != oid:
                parent[oid] = prim            # rule 1: a second box class in a file nests in its primary
            continue
        if o["kind"] == "class":
            base = next((byname[b] for b in facts.get(oid, {}).get("bases", []) if b in byname and byname[b] in keep), None)
            if base:
                box[oid] = sub_of[oid] = base
                continue
        owner, rule = owner_of_file(o["file"])
        if rule == 1:
            box[oid] = owner                  # helpers of the class in their file
        elif owner is not None:
            box[oid] = o["file"]              # a module of its owner's package: drawn inside it
            parent[o["file"]] = owner
        else:
            box[oid] = o["file"]              # external, or a module of a package with no class:
            init = str(Path(o["file"]).parent / "__init__.py")
            if init in objs and init != o["file"] and Path(o["file"]).parent != rootp:
                parent[o["file"]] = init      # its package's own module holds it
    kept_by = {}                              # a kept subclass keeps its base
    for k, o in owners(root, objs).items():
        kb, ob = sub_of.get(k, k), box.get(o, o)
        if kb in keep and ob in keep and kb != ob:
            kept_by.setdefault(kb, set()).add(ob)
    def holds(a, b):                          # a is b, or holds b, through parent
        while b is not None:
            if a == b:
                return True
            b = parent.get(b)
        return False

    for k in sorted(keep):                    # 3a: composition
        o = kept_by.get(k, set())
        if len(o) == 1 and k not in parent and not holds(k, next(iter(o))):
            parent[k] = next(iter(o))
    for k in sorted(keep):                    # 2, for a box class no one keeps alone: its package's class
        if k in parent:
            continue
        for d in Path(objs[k]["file"]).parents:
            if d == rootp or rootp not in d.parents:
                break
            c = primary_of_dir(d)
            if c and c != k:
                if not holds(k, c):
                    parent[k] = c
                break
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

    shown = exposed(root, objs)

    def is_exposed(b):
        """b, and each box holding it, is a class its holder exposes."""
        while b in parent:
            if b not in objs or objs[b]["kind"] != "class" or \
                    (parent[b], objs[b]["name"].split(".")[-1]) not in shown:
                return False
            b = parent[b]
        return True

    def handed(oid):
        """A class instance its owner hands out: a class (not a box) whose
        name a holder of its box exposes (`Shop.sales()` returning `Sale`s)."""
        if oid not in objs or objs[oid]["kind"] != "class" or oid in keep:
            return False
        name, b = objs[oid]["name"].split(".")[-1], box.get(oid)
        while b is not None:
            if (b, name) in shown:
                return True
            b = parent.get(b)
        return False

    def module_box(f):
        if f in box:
            return box[f]
        return next((box[k] for k in sorted(objs) if objs[k]["file"] == f and k in box), f)

    def inside(f, cname, kind, definer, container=None):
        """Is code in file f (class cname) inside the container of a private
        name: its module, its class or a subclass of it, or the package that
        holds a private module."""
        if kind == "path":
            return Path(container) in Path(f).parents
        if kind == "module":
            return f == definer
        dfile, dcls = definer.split(":", 1)
        bases = facts.get(f"{f}:{cname}", {}).get("bases", []) if cname else []
        return f == dfile or dcls.split(".")[-1] in bases

    reexported = reexports(root, objs, owner_of_file, primary_of_dir)

    reached_cache = {}

    def names_reached(f):
        """The names f's code reaches on another object or imports (`x.queued`,
        `from m import queued`); a bare name or one on self is a parameter, a
        local or its own attribute, so a call through it is a function the
        caller was handed."""
        if f not in reached_cache:
            try:
                t = ast.parse(Path(f).read_text(errors="replace"))
            except (OSError, SyntaxError):
                t = ast.Module(body=[], type_ignores=[])
            reached_cache[f] = {n.attr for n in ast.walk(t) if isinstance(n, ast.Attribute)
                                and not (isinstance(n.value, ast.Name) and n.value.id in ("self", "cls"))
                                and not (isinstance(n.value, ast.Call) and _called_name(n.value) == "super")} | \
                {a.asname or a.name for n in ast.walk(t) if isinstance(n, ast.ImportFrom) for a in n.names}
        return reached_cache[f]

    def through_owner(e):
        """A reach the owner allows: an object it hands out, names its own
        module or package re-exports from the part (`from ._stock import
        count` in the owner's file), or a function the caller was handed
        (its code names none of the members: a callback)."""
        if handed(e["to"]):
            return True
        tfile = objs.get(e["to"], {"file": e["to"]})["file"]
        owner = top(box.get(e["to"], e["to"]))
        if all(m in reexported.get((tfile, owner), ()) for m in e["members"]):
            return True
        reached = names_reached(objs.get(e["from"], {"file": e["from"]})["file"])
        return not any(m in reached for m in e["members"] if not m.startswith("__"))

    red, open_pairs = {}, set()
    for e in edges:
        a, b = box.get(e["from"], e["from"]), box.get(e["to"], e["to"])
        if a == b:
            continue
        if not through_owner(e):
            open_pairs.add((a, b))
        ffile = objs.get(e["from"], {"file": e["from"]})["file"]
        tfile = objs.get(e["to"], {"file": e["to"]})["file"]
        priv = [m for m in e["members"] if _private(m)]
        if not priv:
            continue
        reached = names_reached(ffile)
        cname = objs[e["from"]]["name"].split(".")[0] if objs.get(e["from"], {}).get("kind") == "class" else None
        kind = "class" if objs.get(e["to"], {}).get("kind") == "class" else "module"
        for m in priv:                        # a private name the caller's code reaches: not a callback it was handed
            if m in reached and not inside(ffile, cname, kind, e["to"] if kind == "class" else tfile):
                r = red.setdefault((a, b), {"members": set(), "where": objs.get(e["to"], {"name": tfile})["name"]})
                r["members"].add(m)
    for u in private_reaches(root, objs):
        if inside(u["file"], u["cls"], u["kind"], u["definer"], u.get("container")):
            continue
        a = box.get(f"{u['file']}:{u['cls']}") if u["cls"] and f"{u['file']}:{u['cls']}" in box else box.get(u["file"], u["file"])
        if u["kind"] == "class":
            held = [box[k] for nm in u["holds"] for k in sorted(objs) if objs[k]["kind"] == "class"
                    and objs[k]["name"].split(".")[-1] == nm and k in box]
            targets, where = held or [box.get(u["definer"], u["definer"])], objs[u["definer"]]["name"]
        else:
            targets = [module_box(u["definer"])]
            where = Path(u["definer"]).name if u["kind"] == "module" else u["container"] + "/"
        for b in targets:
            if a == b:
                continue
            r = red.setdefault((a, b), {"members": set(), "where": where})
            r["members"].add(u["name"])
            pairs.setdefault((a, b), set()).add(u["name"])
            open_pairs.add((a, b))

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
            nodes.append({"id": b, "name": label, "kind": "module",
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
                  "members": ms, "proof": [proof]})
    # processes: a module started as its own process is a box of its own,
    # with an arrow from the code that builds its command line
    for pr in processes(root):
        a = box.get(f"{pr['file']}:{pr['cls']}") if pr["cls"] and f"{pr['file']}:{pr['cls']}" in box \
            else box.get(pr["file"], pr["file"])
        pid = f"process:{pr['module']}"
        if pid not in {n["id"] for n in nodes}:
            nodes.append({"id": pid, "name": f"python -m {pr['module']}", "kind": "process", "parent": None,
                          "note": f"runs {pr['entry']} in a process of its own"})
        if a not in {n["id"] for n in nodes}:
            o = objs.get(a, {"name": Path(a).stem, "file": a, "kind": "module"})
            nodes.append({"id": a, "name": o["name"] if o.get("kind") == "class" else Path(o["file"]).name,
                          "kind": "object" if o.get("kind") == "class" else "module",
                          "parent": parent.get(a), "note": o["file"]})
        if not any(e["from"] == a and e["to"] == pid for e in E):
            E.append({"from": a, "to": pid, "kind": "spawns", "label": "starts it",
                      "proof": [{"file": pr["file"], "pattern": pr["pattern"]}]})
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
        users |= {box.get(e["from"], e["from"]) for e in edges if box.get(e["to"]) == b and not through_owner(e)}
        if b not in objs:
            users |= {box_of_use(u) for u in imp.get(b, set())}
        out = sorted(u for u in users if top(u) != owner) if not is_exposed(b) else []
        if out:
            moves[b] = {"module": b, "into": owner, "other_users": out,
                        "why": f"belongs to {objs[owner]['name']} (rule {owner_of_file(objs.get(b, {'file': b})['file'])[1]}); "
                               f"{len(out)} user(s) outside it should go through {objs[owner]['name']}"}
    # flags: a part called directly from outside its owner (bypassed), and a
    # module that sits in an owner's folder but belongs to no class (misplaced)
    N = {n["id"]: n for n in nodes}
    for b, m in moves.items():
        if b in N:
            N[b]["flag"] = "owner bypass: called directly from outside " + objs[m["into"]]["name"] + ": " + ", ".join(
                Path(u.split(":")[0]).name + (":" + u.split(":")[1] if ":" in u else "") for u in m["other_users"])
    # resources (data): one box each, an arrow from every box whose code
    # reaches it directly; its owner is its only writer, and a reach from
    # anyone else is a bypass
    res_rows = resource_rows(data or concepts) if (data or concepts) else []
    if res_rows:
        N = {n["id"]: n for n in nodes}
        hits = {}
        helpers = path_helpers(root, res_rows)
        helper_call = re.compile(r"(?<![\w])(" + "|".join(map(re.escape, helpers)) + r")\(") if helpers else None
        for pth in sorted(rootp.rglob("*.py")):
            if "__pycache__" in pth.parts:
                continue
            try:
                text = pth.read_text()
                tree = ast.parse(text)
            except (SyntaxError, UnicodeDecodeError):
                continue
            spans = [(c.lineno, c.end_lineno, c.name) for c in ast.walk(tree) if isinstance(c, ast.ClassDef)]
            lines_ = text.splitlines()
            fspans = [(f.lineno, f.end_lineno) for f in ast.walk(tree) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))]

            def writes_near(ln):
                """A write in the innermost function around line ln, else on
                the line; when the line keeps the path in a name (`self.x =`,
                `X =`), a write through that name in the same class or module."""
                inner = [f for f in fspans if f[0] <= ln <= f[1]]
                a, b = min(inner, key=lambda f: f[1] - f[0]) if inner else (ln, ln)
                if any(WRITES.search(x) for x in lines_[a - 1:b] if not x.lstrip().startswith("#")):
                    return True
                m = re.match(r"\s*((?:self\.)?\w+)\s*(?::[^=]*)?=[^=]", lines_[ln - 1])
                if not m:
                    return False
                cls = [c for c in spans if c[0] <= ln <= c[1]]
                a, b = (min(cls, key=lambda c: c[1] - c[0])[:2]) if cls and m.group(1).startswith("self.") else (1, len(lines_))
                name = re.compile(r"(?<![\w.])" + re.escape(m.group(1)) + r"\b")
                return any(name.search(x) and WRITES.search(x) for i, x in enumerate(lines_[a - 1:b], a)
                           if i != ln and not x.lstrip().startswith("#"))
            for ln, line in enumerate(text.splitlines(), 1):
                if line.strip().startswith("#"):
                    continue
                for ri, (rname, rkind, rowner, pats) in enumerate(res_rows):
                    for pat in pats:
                        if re.search(pat, line):
                            inner = [c for c in spans if c[0] <= ln <= c[1]]
                            cname = min(inner, key=lambda c: c[1] - c[0])[2] if inner else None
                            src = box.get(f"{pth}:{cname}") if cname and f"{pth}:{cname}" in box else box.get(str(pth), str(pth))
                            h = hits.setdefault((src, ri), {"write": False, "file": str(pth), "pattern": pat, "names": []})
                            h["direct"] = True
                            h["write"] |= writes_near(ln)
                            short = pat.replace("\\", "").strip('/ "').strip('"')
                            if short not in h["names"]:
                                h["names"].append(short)
                            break
                m = helper_call.search(line) if helper_call and not line.lstrip().startswith("def ") else None
                if m:                         # a path a helper returns: the write is the caller's
                    ri = helpers[m.group(1)][0]
                    inner = [c for c in spans if c[0] <= ln <= c[1]]
                    cname = min(inner, key=lambda c: c[1] - c[0])[2] if inner else None
                    src = box.get(f"{pth}:{cname}") if cname and f"{pth}:{cname}" in box else box.get(str(pth), str(pth))
                    h = hits.setdefault((src, ri), {"write": False, "file": str(pth),
                                                    "pattern": re.escape(m.group(1)) + r"\(", "names": []})
                    h["write"] |= writes_near(ln)
                    h.setdefault("helpers", set()).add(m.group(1))
                    if m.group(1) + "()" not in h["names"]:
                        h["names"].append(m.group(1) + "()")
        def box_name(b):
            return objs[b]["name"] if b in objs and objs[b]["kind"] == "class" else (
                (str(Path(b).parent) + "/") if Path(b).name == "__init__.py" else Path(b).name)

        writers = {}
        for (src, ri), h in hits.items():
            if h["write"]:
                writers.setdefault(ri, set()).add(top(src))
        for ri, (rname, rkind, _declared, pats) in enumerate(res_rows):
            if not any(k[1] == ri for k in hits):
                continue
            w = sorted(writers.get(ri, set()))
            note = (f"owner: {box_name(w[0])}" if len(w) == 1 else
                    "no single owner: written by " + ", ".join(box_name(x) for x in w) if w else "written by no code here")
            nodes.append({"id": f"resource:{rname}", "name": rname,
                          "kind": rkind if rkind in ("folder", "file") else "external", "parent": None, "note": note})
        N = {n["id"]: n for n in nodes}
        for (src, ri), h in sorted(hits.items(), key=lambda kv: (kv[0][1], str(kv[0][0]))):
            rname, rkind, rowner, pats = res_rows[ri]
            if src not in N:                  # a box that only reaches data: draw it where it belongs
                o = objs.get(src, {"name": Path(src).stem, "file": src, "kind": "module"})
                f = Path(o["file"])
                nodes.append({"id": src, "name": o["name"] if o.get("kind") == "class" else
                              ((str(f.parent) + "/") if f.name == "__init__.py" else f.name),
                              "kind": "object" if o.get("kind") == "class" else "module",
                              "parent": parent.get(src), "note": o["file"]})
                N[src] = nodes[-1]
            w = sorted(writers.get(ri, set()))
            label = " · ".join(h["names"][:3]) + (f"  (+{len(h['names']) - 3})" if len(h["names"]) > 3 else "")
            e = {"from": src, "to": f"resource:{rname}", "kind": "writes" if h["write"] else "reads", "label": label,
                 "proof": [{"file": h["file"], "pattern": h["pattern"]}]}
            def handed_by_owner(name):        # the owner's own public helper gave the path
                _ri, hf, hc = helpers[name]
                hb = box.get(f"{hf}:{hc}") if hc and f"{hf}:{hc}" in box else box.get(hf, hf)
                return not _private(name) and top(hb) == w[0]
            through_owner = not h.get("direct") and all(handed_by_owner(n) for n in h.get("helpers", ()))
            if len(w) == 1 and top(src) != w[0] and not through_owner:
                e["flag"] = f"owner bypass: reaches {box_name(w[0])}'s data directly"
            elif len(w) > 1 and h["write"]:
                e["flag"] = "shared: written by " + ", ".join(box_name(x) for x in w)
            E.append(e)
    for n in nodes:
        f = n["note"].split("  ")[0] if n["id"] not in objs or objs[n["id"]]["kind"] == "module" else None
        if not f:
            continue
        doubts = []
        if n["id"] in parent:
            owner = top(n["id"])
            users = {top(box.get(e["from"], e["from"])) for e in edges if box.get(e["to"]) == n["id"]}
            if users and owner not in users and len(users & keep) == 1:
                doubts.append(f"{objs[owner]['name']} never uses it; only {objs[next(iter(users & keep))]['name']} does")
        if doubts:
            n["flag"] = "doubt: " + "; ".join(doubts) + (" — " + n["flag"] if n.get("flag") else "")
    for e in E:
        b, key = e["to"], (e["from"], e["to"])
        if key in red:
            e["flag"] = (f"private: reaches {', '.join(sorted(red[key]['members']))} of {red[key]['where']} "
                         "from outside it")
        elif b in parent and not within(top(b), e["from"]) and not is_exposed(b) and key in open_pairs:
            e["flag"] = "owner bypass: reaches " + N[b]["name"] + " directly, not through " + objs[top(b)]["name"]
    # every legitimate call or access between two owners is one arrow between
    # the owners themselves; a bypass keeps its own arrow, from the exact part
    merged, keep_edges = {}, []
    for e in E:
        if e.get("flag"):
            keep_edges.append(e)
            continue
        a2, b2 = top(e["from"]), top(e["to"]) if not e["to"].startswith("resource:") else e["to"]
        if a2 == b2:
            continue
        m = merged.setdefault((a2, b2), {"from": a2, "to": b2, "kinds": [], "labels": [], "proof": []})
        m["kinds"].append(e["kind"])
        for part in (x.strip() for x in e["label"].replace("  [run time only]", "").split(" · ")):
            if part and not part.startswith("(+") and part not in m["labels"]:
                m["labels"].append(part)
        m["proof"] += [p for p in e["proof"] if p not in m["proof"]]
    for (a2, b2), m in sorted(merged.items()):
        labs = m["labels"]
        keep_edges.append({"from": a2, "to": b2, "kind": next(k for k in ("writes", "spawns", "calls", "reads") if k in m["kinds"]),
                           "label": " · ".join(labs[:3]) + (f"  (+{len(labs) - 3})" if len(labs) > 3 else ""),
                           "proof": m["proof"][:1]})
    E[:] = keep_edges
    unmapped = overlay(concepts, objs, box, nodes, top) if concepts else []
    return {"name": f"{root} — objects", "nodes": nodes, "edges": E, "moves": [moves[k] for k in sorted(moves)],
            "unmapped": unmapped}


def overlay(concepts, objs, box, nodes, top):
    """Label each box with the concepts whose Objects cell names code in
    it, and flag the mismatches; returns the concepts naming nothing here."""
    N = {n["id"]: n for n in nodes}
    by_class = {}
    for oid, o in objs.items():
        if o["kind"] == "class":
            by_class.setdefault(o["name"].split(".")[-1], []).append(oid)

    def boxes_of(name):
        head = name.split(".")[0].rstrip("/")
        found = {box.get(oid) for oid in by_class.get(head, [])}
        if not found - {None}:
            found = {box.get(oid, oid) for oid, o in objs.items() if o["kind"] == "module" and (
                Path(o["file"]).stem == head or f"/{head}/" in "/" + o["file"])}
        return {b for b in found if b in N}

    carried, unmapped = {}, []
    for concept, names in concept_rows(concepts):
        bs = set().union(*[boxes_of(n) for n in names]) if names else set()
        if not bs:
            unmapped.append(concept)
            continue
        for b in bs:
            carried.setdefault(b, []).append(concept)
        tops = sorted({top(b) for b in bs})
        if len(tops) > 1:
            for b in bs:
                N[b].setdefault("_findings", []).append(
                    f"concept: {concept} is spread over " + ", ".join(N[t]["name"] for t in tops))
    for b, cs in carried.items():
        n = N[b]
        n["concepts"] = cs
        n["note"] = (n["note"] + "  " if n["note"] else "") + "concepts: " + ", ".join(cs)
        if len(cs) > 1:
            n.setdefault("_findings", []).append("concept: carries " + ", ".join(cs))
    for n in nodes:
        f = n.pop("_findings", [])
        if f:
            n["flag"] = " — ".join(([n["flag"]] if n.get("flag") else []) + f)
    return unmapped


def worklist(spec):
    """The refactoring worklist: one entry per part, private access (red)
    first, then owner bypasses (amber), then data with several writers;
    within each, the most reached part first, each with every arrow that
    reaches it and its call sites (file:line)."""
    N = {n["id"]: n for n in spec["nodes"]}

    def top(i):
        while N.get(i, {}).get("parent"):
            i = N[i]["parent"]
        return i

    def owner(part):
        if part.startswith("resource:"):
            return N[part]["note"].removeprefix("owner: ")
        return N[top(part)]["name"]

    def code_sites(file, members, callee):
        """Lines of `file` that use the callee: an import naming it, its class
        name, a name imported from it, or an attribute access to a member."""
        try:
            tree = ast.parse(Path(file).read_text(errors="replace"))
        except (OSError, SyntaxError):
            return None
        stem = Path(callee["note"].split("  ")[0]).stem
        cls = callee["name"].split(".")[-1]
        lines, imported = set(), set()
        for n in ast.walk(tree):
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                mod = getattr(n, "module", None) or ""
                names = [x.name for x in n.names]
                if stem in mod.split(".") or any(stem in x.split(".") or x == cls or x in members for x in names):
                    lines.add(n.lineno)
                    imported |= {x.asname or x.name for x in n.names if x.name in members or x.name == cls}
        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute) and n.attr in members:
                lines.add(n.lineno)
            elif isinstance(n, ast.Name) and (n.id in imported or n.id == cls):
                lines.add(n.lineno)
        return [f"{file}:{i}" for i in sorted(lines)]

    def sites(e):
        out = []
        for p in e["proof"]:
            if "file" not in p:
                out.append("run time only")
                continue
            got = None
            if not e["to"].startswith("resource:"):
                got = code_sites(p["file"], {m for m in e.get("members", []) if not m.startswith("__")}, N[e["to"]])
            if got is None:
                try:
                    lines = Path(p["file"]).read_text(errors="replace").splitlines()
                except OSError:
                    continue
                got = [f"{p['file']}:{i}" for i, ln in enumerate(lines, 1)
                       if not ln.lstrip().startswith("#") and re.search(p["pattern"], ln)]
            out += got
        return out

    groups = {}
    for e in spec["edges"]:
        flag = e.get("flag", "")
        kind = next((k for k in ("private", "owner bypass", "shared") if flag.startswith(k + ":")), None)
        if kind is None:
            continue
        g = groups.setdefault((kind, e["to"]), {"kind": kind, "colour": "red" if kind == "private" else "amber",
                                                "part": N[e["to"]]["name"], "owner": owner(e["to"]), "arrows": []})
        g["arrows"].append({"from": N.get(e["from"], {"name": e["from"]})["name"],
                            "uses": e["label"].replace("  [run time only]", ""), "sites": sites(e)})
    order = {"private": 0, "owner bypass": 1, "shared": 2}
    return sorted(groups.values(), key=lambda g: (order[g["kind"]], -len(g["arrows"]),
                                                  -sum(len(a["sites"]) for a in g["arrows"]), g["part"]))


def print_worklist(items, out=sys.stdout):
    heads = {"private": "Private access (red): reach it through its public interface, or make the name public",
             "owner bypass": "Owner bypass (amber): route it through the owner, have the owner hand it out, "
                             "or make it private",
             "shared": "Shared data (amber): several writers; decide which one owns it"}
    kind = None
    for i, g in enumerate(items, 1):
        if g["kind"] != kind:
            kind = g["kind"]
            print(("\n" if i > 1 else "") + heads[kind], file=out)
        what = {"private": "private access(es)", "owner bypass": "bypass(es)", "shared": "write(s) or read(s)"}[kind]
        print(f"{i}. {g['part']} (owner {g['owner']}): {len(g['arrows'])} {what}", file=out)
        for a in g["arrows"]:
            print(f"   from {a['from']}: {a['uses']}", file=out)
            for s_ in a["sites"]:
                print(f"      {s_}", file=out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--spec")
    ap.add_argument("--concepts", help="optional: the concept map (markdown), laid over the boxes; its Resources table, "
                                       "if any, names the data")
    ap.add_argument("--data", help="optional: a markdown Resources table (Resource | Kind | Owner | Reached by) naming "
                                   "the data, without a concept map")
    ap.add_argument("--tests-dir", default="tests")
    ap.add_argument("--worklist", metavar="FILE",
                    help="with --spec: write the refactoring worklist: private access (red), then owner bypasses "
                         "and shared data (amber), the most reached part first")
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
        spec = spec_of(objs, edges, a.root, a.concepts, a.data)
        json.dump(spec, open(a.spec, "w"), indent=1)
        for m in spec["moves"]:
            print(f"bypassed: {m['module']} ({m['why']})", file=sys.stderr)
        if spec["unmapped"]:
            print("concepts naming nothing in the code: " + ", ".join(spec["unmapped"]), file=sys.stderr)
        if a.worklist:
            with open(a.worklist, "w") as out:
                print_worklist(worklist(spec), out)
    print(f"{len({e['from'] for e in edges} | {e['to'] for e in edges})} objects, {len(edges)} object pairs; "
          f"pytest exit {rc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
