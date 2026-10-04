#!/usr/bin/env python3
"""Seed, check and simplify an Objects Board diagram.

    board.py check    SPEC [--root DIR]
    board.py build    SPEC [--root DIR] [--keep CURRENT.json] [--stamp REV] --out DOC.json
    board.py collapse DOC  --out COLLAPSED.json
    board.py simplify DOC  --out SIMPLE.json

SPEC is the diagram to draw, with the evidence for every arrow:

    {"name": "orders",
     "nodes": [{"id": "order", "name": "Order (app/order.py)", "kind": "object",
                "parent": null, "members": [{"vis": "+", "name": "total()"}], "note": ""}],
     "edges": [{"from": "cart", "to": "order", "kind": "calls", "label": "total()",
                "proof": [{"file": "app/cart.py", "pattern": "order\\\\.total\\\\("}]}]}

check   every edge's proof patterns (regexes) must match in their files,
        relative to --root; a proof {"observed": "..."} (a call recorded at
        run time) is accepted as it is; an edge with no proof, or between a
        box and a box nested in it (ownership is the nesting, not an arrow),
        is refused. Exit 1 on any
        failure, listing each.
build   check, then lay out: a node already in --keep (the diagram as the
        board holds it now, e.g. from `ArtifactData get`) keeps its position
        and size; a new node goes next to the node it shares most arrows
        with, without overlapping. --stamp appends "(REV)" to the name.
        DOC.json holds {name, nodes, edges}, ready for `ArtifactData update`
        pinned to the version that was read.
collapse  the same boxes, one arrow per pair of boxes: every arrow between
        the same two boxes becomes one, labels merged (three, then "(+n)"),
        the strongest kind kept. The detailed diagram has one arrow per call
        or file access; the collapsed one shows who depends on whom.
simplify  one box per top-level owner, one arrow per pair: labels merged
        (three, then "(+n)"), the strongest kind kept (writes > spawns >
        calls > reads), arrows between a box and its own parts dropped.
"""
import argparse, json, math, pathlib, re, sys

PAD, HEAD, LINE, CPAD = 10, 34, 17, 14
KIND_RANK = ["writes", "spawns", "calls", "reads"]


# --- geometry: the board page's own box sizes --------------------------------

def own(n):
    lines = [] if n.get("collapsed") else n.get("members", [])
    w = max(150, len(n["name"]) * 7.4 + 70)
    for m in lines:
        w = max(w, len(("" if m.get("vis") == " " else m.get("vis", "+") + " ") + m["name"]) * 6.95 + PAD * 2 + 4)
    note = n.get("note") and not n.get("collapsed")
    if note:
        w = max(w, min(340, len(n["note"]) * 5.6 + PAD * 2))
    h = HEAD + len(lines) * LINE + (LINE if note else 0) + (6 if (lines or note) else 0)
    return math.ceil(max(w, n.get("w") or 0)), math.ceil(max(h, n.get("h") or 0))


def bounds(d):
    nodes = {n["id"]: n for n in d["nodes"]}
    kids = {}
    for n in d["nodes"]:
        if n.get("parent"):
            kids.setdefault(n["parent"], []).append(n["id"])
    out = {}

    def calc(i):
        if i in out:
            return out[i]
        n = nodes[i]
        w, h = own(n)
        x0, y0, x1, y1 = n["x"], n["y"], n["x"] + w, n["y"] + h
        for c in kids.get(i, []):
            b = calc(c)
            x0, y0 = min(x0, b[0] - CPAD), min(y0, b[1] - CPAD - 4)
            x1, y1 = max(x1, b[0] + b[2] + CPAD), max(y1, b[1] + b[3] + CPAD)
        out[i] = (x0, y0, x1 - x0, y1 - y0)
        return out[i]

    for i in nodes:
        calc(i)
    return out


def ancestors(d, i):
    nodes = {n["id"]: n for n in d["nodes"]}
    a, p = set(), nodes[i].get("parent")
    while p:
        a.add(p)
        p = nodes[p].get("parent")
    return a


def overlaps(d, i):
    B = bounds(d)
    a, anc = B[i], ancestors(d, i)
    return [j for j, b in B.items() if j != i and j not in anc and i not in ancestors(d, j)
            and a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]]


def move_tree(d, i, dx, dy):
    for n in d["nodes"]:
        if n["id"] == i:
            n["x"] += dx
            n["y"] += dy
    for n in d["nodes"]:
        if n.get("parent") == i:
            move_tree(d, n["id"], dx, dy)


def place_near(d, i, anchor, gap=40, rings=30):
    for k in range(rings):
        B = bounds(d)
        a, b, g = B[anchor], B[i], gap + k * gap
        for tx, ty in ((a[0] + a[2] + g, a[1]), (a[0] - b[2] - g, a[1]), (a[0], a[1] + a[3] + g),
                       (a[0], a[1] - b[3] - g), (a[0] + a[2] + g, a[1] + a[3] + g)):
            cur = bounds(d)[i]
            move_tree(d, i, tx - cur[0], ty - cur[1])
            if not overlaps(d, i):
                return True
    return False


# --- check -----------------------------------------------------------------------

def check(spec, root):
    ids = {n["id"] for n in spec["nodes"]}
    parent = {n["id"]: n.get("parent") for n in spec["nodes"]}

    def owns(a, b):
        p = parent.get(b)
        while p:
            if p == a:
                return True
            p = parent.get(p)
        return False

    bad = []
    for e in spec["edges"]:
        tag = f"{e['from']} -> {e['to']} ({e.get('label', '')})"
        if e["from"] not in ids or e["to"] not in ids:
            bad.append(f"{tag}: unknown node")
            continue
        if owns(e["from"], e["to"]) or owns(e["to"], e["from"]):
            bad.append(f"{tag}: an arrow between a box and its own part (nesting already says it owns it)")
            continue
        proofs = e.get("proof") or []
        if not proofs:
            bad.append(f"{tag}: no proof")
        for p in proofs:
            if "observed" in p:
                continue                      # seen at run time (trace_objects.py): the run is the evidence
            f = root / p["file"]
            try:
                text = f.read_text(errors="replace")
            except OSError:
                bad.append(f"{tag}: {p['file']} not found")
                continue
            if not re.search(p["pattern"], text, re.M):
                bad.append(f"{tag}: no match for {p['pattern']!r} in {p['file']}")
    return bad


# --- build ----------------------------------------------------------------------

def build(spec, keep, stamp):
    old = {n["id"]: n for n in (keep or {}).get("nodes", [])}
    nodes, new = [], []
    for n in spec["nodes"]:
        m = {"id": n["id"], "name": n["name"], "kind": n.get("kind", "object"), "origin": n.get("origin", "library"),
             "members": n.get("members", []), "note": n.get("note", ""), "parent": n.get("parent"),
             "collapsed": n.get("collapsed", False), "x": 0, "y": 0}
        if n["id"] in old:
            for k in ("x", "y", "w", "h", "collapsed"):
                if k in old[n["id"]]:
                    m[k] = old[n["id"]][k]
        else:
            new.append(n["id"])
        nodes.append(m)
    edges = [{k: e[k] for k in ("from", "to", "kind", "label") if k in e} | {"id": f"e{i + 1}"}
             for i, e in enumerate(spec["edges"])]
    name = spec["name"] + (f" ({stamp})" if stamp else "")
    d = {"name": name, "nodes": nodes, "edges": edges}
    byid = {n["id"]: n for n in nodes}
    kids = {}
    for n in nodes:
        if n.get("parent") in byid:
            kids.setdefault(n["parent"], []).append(n["id"])

    def lay(i):
        """Stack i's new children in a column under its header, each child's
        own children laid out first, so a tree moves as one."""
        y = byid[i]["y"] + HEAD + CPAD
        for k in kids.get(i, []):
            lay(k)
            if k in old:
                continue
            b = bounds(d)[k]
            move_tree(d, k, byid[i]["x"] + CPAD - b[0], y - b[1])
            y += bounds(d)[k][3] + CPAD
        for k in kids.get(i, []):
            if k in old:
                y = max(y, bounds(d)[k][1] + bounds(d)[k][3] + CPAD)

    for n in nodes:
        if not n.get("parent") or n["parent"] not in byid:
            lay(n["id"])
    placed = {i for i in old if i in byid and not byid[i].get("parent")}
    for i in new:
        if byid[i].get("parent") in byid:
            continue
        peers = {}
        for e in edges:
            if i in (e["from"], e["to"]):
                o = e["to"] if e["from"] == i else e["from"]
                while byid.get(o, {}).get("parent") in byid:
                    o = byid[o]["parent"]
                if o in placed:
                    peers[o] = peers.get(o, 0) + 1
        if peers:
            place_near(d, i, max(peers, key=peers.get))
        elif placed:
            place_near(d, i, sorted(placed)[0])
        placed.add(i)
    return d


# --- simplify -------------------------------------------------------------------

def merge(edges, key, prefix):
    merged = {}
    for e in edges:
        a, b = key(e["from"]), key(e["to"])
        if a == b:
            continue
        v = merged.setdefault((a, b), {"kinds": [], "labels": []})
        v["kinds"].append(e["kind"])
        for part in (x.strip() for x in (e.get("label") or "").split(" · ")):
            if part and part not in v["labels"]:
                v["labels"].append(part)
    out = []
    for i, ((a, b), v) in enumerate(merged.items()):
        lab = " · ".join(v["labels"][:3]) + (f"  (+{len(v['labels']) - 3})" if len(v["labels"]) > 3 else "")
        out.append({"id": f"{prefix}{i + 1}", "from": a, "to": b,
                    "kind": next(k for k in KIND_RANK if k in v["kinds"]), "label": lab})
    return out


def collapse(d):
    return {"name": d["name"] + " — collapsed", "nodes": d["nodes"], "edges": merge(d["edges"], lambda i: i, "c")}


def simplify(d):
    N = {n["id"]: n for n in d["nodes"]}

    def top(i):
        while N[i].get("parent"):
            i = N[i]["parent"]
        return i

    def inside(i):
        return [n for n in d["nodes"] if top(n["id"]) == i and n["id"] != i]

    out = {"name": d["name"] + " — simplified", "nodes": [], "edges": []}
    for n in d["nodes"]:
        if n.get("parent"):
            continue
        s = {k: v for k, v in n.items() if k not in ("w", "h")}
        kids = inside(n["id"])
        if kids:
            s["collapsed"] = True
            s["note"] = f"{len(kids)} inside: " + ", ".join(k["name"] for k in kids if k.get("parent") == n["id"])
        out["nodes"].append(s)
    out["edges"] = merge(d["edges"], top, "s")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "build", "collapse", "simplify"])
    ap.add_argument("src")
    ap.add_argument("--root", default=".")
    ap.add_argument("--keep")
    ap.add_argument("--stamp")
    ap.add_argument("--out")
    a = ap.parse_args()
    doc = json.load(open(a.src))
    if a.cmd in ("collapse", "simplify"):
        fn = collapse if a.cmd == "collapse" else simplify
        json.dump(fn(doc.get("data", doc)), open(a.out, "w"), indent=1)
        return 0
    bad = check(doc, pathlib.Path(a.root))
    for b in bad:
        print("UNPROVED", b, file=sys.stderr)
    if bad:
        return 1
    print(f"{len(doc['edges'])} arrows proved", file=sys.stderr)
    if a.cmd == "build":
        keep = json.load(open(a.keep)) if a.keep else None
        d = build(doc, keep.get("data", keep) if keep else None, a.stamp)
        json.dump(d, open(a.out, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
