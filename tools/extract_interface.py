#!/usr/bin/env python3
"""An object's full interface and every use of it outside its file.

    extract_interface.py --target TARGET \
        --scan lib=path/to/lib --scan tests=path/to/tests [--scan app=...] \
        [--factory make_thing] [--stand-in _ShimThing] [--param-name thing] \
        [--attr thing] --out data.json

TARGET is a module (`path/to/mod.py`), a class (`path/to/mod.py:Name`) or a
top-level function (`path/to/mod.py:func`). Python only: it parses Python
source with the standard `ast` module.

Module and function targets. The interface is the module's top-level names
(functions, classes, assignments; imports excluded), or the one function. A
use is a read of `alias.name` after `import pkg.mod [as alias]` or
`from pkg import mod [as alias]`, and every load of a name brought in by
`from pkg.mod import name [as other]`, relative imports included. Not seen:
`importlib`/`__import__` with computed names, a module-level `__getattr__`,
names re-exported through another module (`from pkg import name` where pkg
re-exports it: give pkg/__init__.py as the target to see those).

Class targets. A use is an attribute read (or getattr/hasattr/setattr with a constant name)
on: a name bound to ClassName(...) / ClassName.__new__ / a --factory call
(also through an `a if c else b`), a --stand-in instance, a parameter named
each --param-name (repeatable; default: the class name lower-cased),
`self.<param-name>`, an
attribute or property named --attr (`<anything>.thing`, and a name assigned
from one), or the class itself. Anything reached through another name is
missed: follow a function that returns an instance with --factory, and an
attribute or property that holds one with --attr.

Members come from the class body (methods, properties, static/class methods,
UPPER constants) plus every `self.x = ...` in it. Sections are the class's own
`# --- name ---` comments. A file under a path containing /tests/ counts as a
test whatever --scan tag it came from.
"""
import argparse, ast, json, os, pathlib, re, sys

ap = argparse.ArgumentParser()
ap.add_argument("--target", required=True)
ap.add_argument("--scan", action="append", default=[])
ap.add_argument("--factory", action="append", default=[])
ap.add_argument("--stand-in", dest="standin", action="append", default=[])
ap.add_argument("--param-name", action="append", default=[])
ap.add_argument("--attr", action="append", default=[])
ap.add_argument("--out", required=True)
a = ap.parse_args()

cfile, _, cname = a.target.partition(":")
CFILE = pathlib.Path(cfile).resolve()
src = CFILE.read_text(); lines = src.splitlines(); T = ast.parse(src)


def dotted(path):
    """The module's import name: its path up to the first folder without an
    __init__.py."""
    path = pathlib.Path(path).resolve()
    parts = [path.stem] if path.stem != "__init__" else []
    d = path.parent
    while (d / "__init__.py").exists():
        parts.insert(0, d.name)
        d = d.parent
    return ".".join(parts)


def module_mode():
    mod = dotted(CFILE)
    top = {}
    for n in T.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            top[n.name] = {"name": n.name, "kind": "class" if isinstance(n, ast.ClassDef) else "function",
                           "line": n.lineno, "doc": (ast.get_docstring(n) or "").strip().split("\n\n")[0][:400]}
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                if isinstance(t, ast.Name):
                    top[t.id] = {"name": t.id, "kind": "constant" if t.id.isupper() else "variable", "line": n.lineno}
    if cname:
        if cname not in top:
            sys.exit(f"{cname} is not a top-level name of {cfile}")
        top = {cname: top[cname]}
    uses = []
    for spec in a.scan:
        tag, base = spec.split("=", 1)
        for p in sorted(pathlib.Path(base).rglob("*.py")):
            if "__pycache__" in p.parts or p.resolve() == CFILE:
                continue
            try:
                text = p.read_text(); t = ast.parse(text)
            except (SyntaxError, UnicodeDecodeError):
                continue
            pl = text.splitlines()
            here = dotted(p)
            pkg = here if p.name == "__init__.py" else here.rpartition(".")[0]
            aliases, names = set(), {}
            for n in ast.walk(t):
                if isinstance(n, ast.Import):
                    for al in n.names:
                        if al.name == mod:
                            aliases.add(al.asname or al.name)
                elif isinstance(n, ast.ImportFrom):
                    base_mod = n.module or ""
                    if n.level:
                        up = pkg.split(".")[: len(pkg.split(".")) - (n.level - 1)] if pkg else []
                        base_mod = ".".join(up + ([n.module] if n.module else []))
                    for al in n.names:
                        if f"{base_mod}.{al.name}" == mod:
                            aliases.add(al.asname or al.name)
                        elif base_mod == mod and al.name in top:
                            names[al.asname or al.name] = al.name
                            uses.append({"scope": tag, "file": str(p), "line": n.lineno, "caller": "<import>",
                                         "member": al.name, "via": "from-import", "code": pl[n.lineno - 1].strip()[:200]})

            def visit(node, qual):
                for ch in ast.iter_child_nodes(node):
                    q = qual + [ch.name] if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else qual
                    if isinstance(ch, ast.Attribute) and isinstance(ch.value, ast.Name) and ch.value.id in aliases \
                            and ch.attr in top:
                        uses.append({"scope": tag, "file": str(p), "line": ch.lineno, "caller": ".".join(qual) or "<module>",
                                     "member": ch.attr, "via": "module." + ch.attr, "code": pl[ch.lineno - 1].strip()[:200]})
                    elif isinstance(ch, ast.Name) and isinstance(ch.ctx, ast.Load) and ch.id in names:
                        uses.append({"scope": tag, "file": str(p), "line": ch.lineno, "caller": ".".join(qual) or "<module>",
                                     "member": names[ch.id], "via": "name", "code": pl[ch.lineno - 1].strip()[:200]})
                    visit(ch, q)
            visit(t, [])
    json.dump({"target": a.target, "module": mod, "kind": "function" if cname else "module", "file": os.path.relpath(CFILE),
               "file_lines": len(lines), "members": list(top.values()), "uses": uses, "sections": [], "callers": []},
              open(a.out, "w"), indent=1)
    print(f"{len(top)} names, {len(uses)} uses", file=sys.stderr)
    sys.exit(0)


cls = next((n for n in ast.walk(T) if isinstance(n, ast.ClassDef) and n.name == cname), None) if cname else None
if cls is None:
    module_mode()
pnames = set(a.param_name) or {cname.lower()}

members = {}
def add(name, kind, **kw):
    m = members.setdefault(name, {"name": name, "kind": kind, "internal": 0})
    m.update(kw)
for n in cls.body:
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
        decos = [ast.unparse(d) for d in n.decorator_list]
        kind = ("property" if "property" in decos or any(d.endswith(".setter") for d in decos)
                else "classmethod" if "classmethod" in decos else "staticmethod" if "staticmethod" in decos else "method")
        if n.name in members and kind == "method":
            continue
        ar = n.args
        params = [x.arg for x in ar.posonlyargs + ar.args][(0 if kind == "staticmethod" else 1):]
        sig = []
        for i, p in enumerate(params):
            d = i - (len(params) - len(ar.defaults))
            sig.append(p + ("=" + ast.unparse(ar.defaults[d]) if d >= 0 else ""))
        if ar.vararg: sig.append("*" + ar.vararg.arg)
        for k, dflt in zip(ar.kwonlyargs, ar.kw_defaults): sig.append(k.arg + ("=" + ast.unparse(dflt) if dflt else ""))
        if ar.kwarg: sig.append("**" + ar.kwarg.arg)
        doc = (ast.get_docstring(n) or "").strip().split("\n\n")[0].replace("\n", " ")
        add(n.name, kind, sig=", ".join(sig), doc=doc[:400], line=n.lineno, end=n.end_lineno)
    elif isinstance(n, (ast.Assign, ast.AnnAssign)):
        for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
            if isinstance(t, ast.Name):
                add(t.id, "constant", value=ast.unparse(n.value)[:80], line=n.lineno)
for f in ast.walk(cls):
    if isinstance(f, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
        for t in (f.targets if isinstance(f, ast.Assign) else [f.target]):
            for x in ast.walk(t):
                if isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name) and x.value.id == "self" and isinstance(x.ctx, ast.Store):
                    if x.attr not in members:
                        add(x.attr, "attribute", line=x.lineno)
                    members[x.attr].setdefault("set_at", []).append(x.lineno)
for x in ast.walk(cls):
    if isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name) and x.value.id in ("self", "cls") and isinstance(x.ctx, ast.Load):
        if x.attr in members:
            members[x.attr]["internal"] += 1

# sections: the class's own "# --- name ---" comments
marks = [(i + 1, m.group(1).strip()) for i, l in enumerate(lines)
         if cls.lineno <= i + 1 <= cls.end_lineno and (m := re.match(r"\s*#\s*-{2,}\s*(.+?)\s*-{2,}\s*$", l))]
first = (marks[0][0] if marks else cls.end_lineno + 1)
sections = ([] if first <= cls.lineno + 1 else ["(top)"]) + [n for _, n in marks]
bounds = [(cls.lineno, "(top)")] + marks
for m in members.values():
    m["section"] = [n for ln, n in bounds if m.get("line", 0) >= ln][-1]
if not any(m["section"] == "(top)" for m in members.values()) and "(top)" in sections:
    sections.remove("(top)")

def ctor(call):
    if isinstance(call, ast.IfExp):
        return ctor(call.body) or ctor(call.orelse)
    if not isinstance(call, ast.Call):
        return None
    f = call.func
    nm = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
    if nm in (cname, "_" + cname): return cname
    if nm in a.standin: return nm
    if nm == "__new__" and call.args and ast.unparse(call.args[0]).endswith(cname): return cname
    if any(fx in ast.unparse(call.func) for fx in a.factory): return cname
    return None

def instance(v):
    """cname when the expression yields an instance: a constructor or factory
    call, or an attribute/property named by --attr."""
    if isinstance(v, ast.Attribute) and v.attr in a.attr:
        return "." + v.attr
    return ctor(v)

uses = []
for spec in a.scan:
    tag, base = spec.split("=", 1)
    for p in sorted(pathlib.Path(base).rglob("*.py")):
        if "__pycache__" in p.parts or p.resolve() == CFILE:
            continue
        try:
            text = p.read_text(); t = ast.parse(text)
        except (SyntaxError, UnicodeDecodeError):
            continue
        pl = text.splitlines()
        def base_kind(v, bound):
            if isinstance(v, (ast.Call, ast.IfExp)) and ctor(v):
                return ctor(v) + "(...) inline"
            if isinstance(v, ast.Name):
                if v.id in bound: return bound[v.id]
                if v.id in (cname, "_" + cname): return "class"
            if isinstance(v, ast.Attribute):
                if v.attr in pnames and isinstance(v.value, ast.Name) and v.value.id == "self": return "self." + v.attr
                if v.attr in a.attr: return "." + v.attr
                if v.attr == cname: return "class"
            return None
        def check(n, qual, bound):
            if isinstance(n, ast.Attribute):
                k = base_kind(n.value, bound)
                if k and n.attr != "__new__":
                    uses.append({"scope": tag, "file": str(p), "line": n.lineno, "caller": ".".join(qual) or "<module>",
                                 "member": n.attr, "via": k, "code": pl[n.lineno - 1].strip()[:200]})
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("getattr", "hasattr", "setattr") and len(n.args) >= 2:
                k = base_kind(n.args[0], bound)
                if k and isinstance(n.args[1], ast.Constant):
                    uses.append({"scope": tag, "file": str(p), "line": n.lineno, "caller": ".".join(qual) or "<module>",
                                 "member": n.args[1].value, "via": f"{k} ({n.func.id})", "code": pl[n.lineno - 1].strip()[:200]})
        def visit(node, qual, bound):
            for ch in ast.iter_child_nodes(node):
                if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    nb = dict(bound)
                    if not isinstance(ch, ast.ClassDef):
                        for arg in ch.args.args + ch.args.kwonlyargs:
                            if arg.arg in pnames: nb[arg.arg] = "param"
                        for s in ast.walk(ch):
                            if isinstance(s, ast.Assign) and len(s.targets) == 1 and isinstance(s.targets[0], ast.Name):
                                k = instance(s.value)
                                if k: nb[s.targets[0].id] = k
                            if isinstance(s, ast.withitem) and isinstance(s.optional_vars, ast.Name):
                                k = ctor(s.context_expr)
                                if k: nb[s.optional_vars.id] = k
                    visit(ch, qual + [ch.name], nb)
                else:
                    check(ch, qual, bound); visit(ch, qual, bound)
        visit(t, [], {})

seen, U = set(), []
for u in uses:
    key = (u["file"], u["line"], u["member"])
    if key not in seen:
        seen.add(key); U.append(u)
init = next((n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"), None)
json.dump({"cls": cname, "file": os.path.relpath(CFILE), "file_lines": len(lines), "span": [cls.lineno, cls.end_lineno],
           "classdoc": (ast.get_docstring(cls) or "").strip().split("\n\n")[0].replace("\n", " ")[:300],
           "init_sig": ast.unparse(init.args).replace("self, ", "").replace("self", "") if init else "",
           "sections": sections, "members": list(members.values()), "uses": U, "callers": []},
          open(a.out, "w"), indent=1)
print(f"{len(members)} members, {len(U)} uses, sections: {sections}", file=sys.stderr)
