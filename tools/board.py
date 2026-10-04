#!/usr/bin/env python3
"""Seed, check and simplify an Objects Board diagram.

    board.py check    SPEC [--root DIR]
    board.py build    SPEC [--root DIR] [--keep CURRENT.json] [--stamp REV] --out DOC.json
    board.py build    SPEC --layout [--root DIR] [--stamp REV] --out DOC.json
    board.py crossings DOC
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
        and size unless it now overlaps another (a neighbour grew), then it
        moves to the nearest free spot, smallest boxes first; a new node goes
        next to the node it shares most arrows with, without overlapping.
        --stamp appends "(REV)" to the name.
        DOC.json holds {name, nodes, edges}, ready for `ArtifactData update`
        pinned to the version that was read.
--layout  ignore any kept positions and lay the diagram out from scratch,
        for the fewest crossing arrows: each box's parts are laid out first,
        then each level as one graph of fixed-size blocks, by Graphviz `dot`
        when it is installed (both directions tried, the better kept), else
        in a grid in connection order; then sibling blocks swap places
        wherever that lowers the count. Deterministic: the same spec gives
        the same layout.
crossings  the layout's clutter, as the page draws it (straight arrows
        between box centres, clipped at the borders): pairs of arrows that
        cross, and arrows that pass over a box that is neither end nor holds
        one.
collapse  the same boxes, one arrow per pair of boxes: every arrow between
        the same two boxes becomes one, labels merged (three, then "(+n)"),
        the strongest kind kept. The detailed diagram has one arrow per call
        or file access; the collapsed one shows who depends on whom.
simplify  one box per top-level owner, one arrow per pair: labels merged
        (three, then "(+n)"), the strongest kind kept (writes > spawns >
        calls > reads), arrows between a box and its own parts dropped.
"""
import argparse, json, math, pathlib, re, shutil, subprocess, sys

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

def nudge(d, i, step=30, rings=40):
    """Move i (with what it holds) to the nearest spot where it overlaps
    nothing, searching rings of `step` px around where it is."""
    x0, y0 = bounds(d)[i][:2]
    for k in range(1, rings):
        for dx, dy in ((k, 0), (0, k), (-k, 0), (0, -k), (k, k), (-k, k), (k, -k), (-k, -k)):
            cur = bounds(d)[i]
            move_tree(d, i, x0 + dx * step - cur[0], y0 + dy * step - cur[1])
            if not overlaps(d, i):
                return True
    cur = bounds(d)[i]
    move_tree(d, i, x0 - cur[0], y0 - cur[1])
    return False


def build(spec, keep, stamp, fresh=False):
    old = {n["id"]: n for n in (keep or {}).get("nodes", [])}
    old = {i: n for i, n in old.items()
           if n.get("parent") == next((m.get("parent") for m in spec["nodes"] if m["id"] == i), None)}
    nodes, new = [], []
    for n in spec["nodes"]:
        m = {"id": n["id"], "name": n["name"], "kind": n.get("kind", "object"), "origin": n.get("origin", "library"),
             "members": n.get("members", []), "note": n.get("note", ""), "parent": n.get("parent"),
             "collapsed": n.get("collapsed", False), "x": 0, "y": 0}
        if n.get("flag"):
            m["flag"] = n["flag"]
        if n["id"] in old and old[n["id"]].get("parent") == n.get("parent"):
            for k in ("x", "y", "w", "h", "collapsed"):
                if k in old[n["id"]]:
                    m[k] = old[n["id"]][k]
        else:
            new.append(n["id"])
        nodes.append(m)
    edges = [{k: e[k] for k in ("from", "to", "kind", "label", "flag") if k in e} | {"id": f"e{i + 1}"}
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
        for k in kids.get(i, []):            # new parts go below the ones already placed
            if k in old:
                y = max(y, bounds(d)[k][1] + bounds(d)[k][3] + CPAD)
        for k in kids.get(i, []):
            if k in old:
                continue
            b = bounds(d)[k]
            move_tree(d, k, byid[i]["x"] + CPAD - b[0], y - b[1])
            y += bounds(d)[k][3] + CPAD

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
    # a kept box that now overlaps (a neighbour grew) moves to the nearest free
    # spot; the largest boxes stay, the smaller ones move
    def depth(i):
        k, p = 0, byid[i].get("parent")
        while p in byid:
            k, p = k + 1, byid[p].get("parent")
        return k

    if fresh:
        return layout(d)
    # inner boxes first (they make their owner's size), then the top level
    for i in sorted(byid, key=lambda i: (-depth(i), bounds(d)[i][2] * bounds(d)[i][3])):
        if overlaps(d, i):
            nudge(d, i)
    return d


# --- layout: fewest crossing arrows ---------------------------------------------

def _clip(b, tx, ty):
    cx, cy = b[0] + b[2] / 2, b[1] + b[3] / 2
    dx, dy = tx - cx, ty - cy
    if not dx and not dy:
        return cx, cy
    s = min((b[2] / 2) / abs(dx or 1e-9), (b[3] / 2) / abs(dy or 1e-9))
    return cx + dx * s, cy + dy * s


def _segments(d, B):
    out = []
    for e in d["edges"]:
        if e["from"] not in B or e["to"] not in B or e["from"] == e["to"]:
            continue
        a, b = B[e["from"]], B[e["to"]]
        p1 = _clip(a, b[0] + b[2] / 2, b[1] + b[3] / 2)
        p2 = _clip(b, a[0] + a[2] / 2, a[1] + a[3] / 2)
        out.append((e["from"], e["to"], p1, p2))
    return out


def _cross(p1, p2, p3, p4):
    def o(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return (v > 1e-9) - (v < -1e-9)
    return o(p1, p2, p3) * o(p1, p2, p4) < 0 and o(p3, p4, p1) * o(p3, p4, p2) < 0


def _hits(p1, p2, r):
    """Whether the segment p1-p2 passes through the rectangle r (x, y, w, h)."""
    t0, t1 = 0.0, 1.0
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    for p, q in ((-dx, p1[0] - r[0]), (dx, r[0] + r[2] - p1[0]), (-dy, p1[1] - r[1]), (dy, r[1] + r[3] - p1[1])):
        if p == 0:
            if q < 0:
                return False
        else:
            t = q / p
            if p < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
            if t0 > t1:
                return False
    return t1 - t0 > 1e-6


def crossings(d):
    """(arrow pairs that cross, arrows over a box that is neither end nor
    holds one): the layout's clutter as the page draws it."""
    B = bounds(d)
    N = {n["id"]: n for n in d["nodes"]}
    up = {}
    for i in N:
        a, p = {i}, N[i].get("parent")
        while p in N:
            a.add(p)
            p = N[p].get("parent")
        up[i] = a
    segs = _segments(d, B)
    x = sum(1 for i, s in enumerate(segs) for t in segs[i + 1:]
            if not ({s[0], s[1]} & {t[0], t[1]}) and _cross(s[2], s[3], t[2], t[3]))
    over = sum(1 for s in segs for j, r in B.items()
               if j not in up[s[0]] | up[s[1]] and _hits(s[2], s[3], r))
    return x, over


def _dot(ids, size, edges, rankdir):
    """{id: (x, y)} top-left positions from Graphviz dot, or None."""
    exe = shutil.which("dot")
    if not exe:
        return None
    q = lambda s: '"' + s.replace('"', '\\"') + '"'
    lines = [f"digraph g {{ rankdir={rankdir}; nodesep=0.6; ranksep=0.8; ordering=out;",
             "node [shape=box, fixedsize=true, label=\"\"];"]
    for i in ids:
        w, h = size[i]
        lines.append(f"{q(i)} [width={w / 72:.3f}, height={h / 72:.3f}];")
    for a, b in edges:
        lines.append(f"{q(a)} -> {q(b)};")
    lines.append("}")
    try:
        r = subprocess.run([exe, "-Tjson"], input="\n".join(lines), capture_output=True, text=True, timeout=120)
        g = json.loads(r.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    top = float(g["bb"].split(",")[3])
    pos = {}
    for o in g.get("objects", []):
        if o.get("name") in size and "pos" in o:
            cx, cy = map(float, o["pos"].split(","))
            w, h = size[o["name"]]
            pos[o["name"]] = (cx - w / 2, (top - cy) - h / 2)
    return pos if len(pos) == len(ids) else None


def _grid(ids, size, edges, gap=60):
    """Rows in connection order (most connected first, then its neighbours)."""
    deg = {i: 0 for i in ids}
    nb = {i: [] for i in ids}
    for a, b in edges:
        deg[a] += 1
        deg[b] += 1
        nb[a].append(b)
        nb[b].append(a)
    order, seen = [], set()
    for s in sorted(ids, key=lambda i: (-deg[i], i)):
        stack = [s]
        while stack:
            i = stack.pop(0)
            if i in seen:
                continue
            seen.add(i)
            order.append(i)
            stack += sorted((j for j in nb[i] if j not in seen), key=lambda j: (-deg[j], j))
    per = max(1, math.ceil(math.sqrt(len(order))))
    pos, y = {}, 0
    for r in range(0, len(order), per):
        row, x = order[r:r + per], 0
        for i in row:
            pos[i] = (x, y)
            x += size[i][0] + gap
        y += max(size[i][1] for i in row) + gap
    return pos


def _level_score(ids, size, pos, edges):
    B = {i: (pos[i][0], pos[i][1], *size[i]) for i in ids}
    d = {"edges": [{"from": a, "to": b} for a, b in edges]}
    segs = _segments(d, B)
    x = sum(1 for k, s in enumerate(segs) for t in segs[k + 1:]
            if not ({s[0], s[1]} & {t[0], t[1]}) and _cross(s[2], s[3], t[2], t[3]))
    return x + sum(1 for s in segs for j, r in B.items() if j not in (s[0], s[1]) and _hits(s[2], s[3], r))


def layout(d):
    """Lay d out from scratch for the fewest crossings; returns d."""
    N = {n["id"]: n for n in d["nodes"]}
    kids = {}
    for n in d["nodes"]:
        kids.setdefault(n.get("parent") if n.get("parent") in N else None, []).append(n["id"])
    for v in kids.values():
        v.sort()

    def under(i, level):
        """The member of `level` that is i or holds i, or None."""
        while i is not None and i not in level:
            i = N[i].get("parent") if i in N else None
        return i

    size = {}

    def place(parent):
        """Lay out parent's children (each first), at positions relative to
        the parent's content origin; returns the block's (w, h)."""
        ids = kids.get(parent, [])
        for i in ids:
            if kids.get(i):
                cw, ch = place(i)
                ow, oh = own(N[i])
                size[i] = (max(ow, cw + 2 * CPAD), oh + ch + 2 * CPAD)
            else:
                size[i] = own(N[i])
        level = set(ids)
        pairs = {(under(e["from"], level), under(e["to"], level)) for e in d["edges"]}
        edges = sorted((a, b) for a, b in pairs if a and b and a != b)
        cands = [p for p in (_dot(ids, size, edges, r) for r in ("TB", "LR")) if p] or [_grid(ids, size, edges)]
        pos = min(cands, key=lambda p: _level_score(ids, size, p, edges))
        better = True
        while better:                         # swap two siblings when it lowers the count
            better = False
            score = _level_score(ids, size, pos, edges)
            for k, a in enumerate(ids):
                for b in ids[k + 1:]:
                    trial = dict(pos)
                    trial[a], trial[b] = pos[b], pos[a]
                    R = {i: (trial[i][0], trial[i][1], *size[i]) for i in ids}
                    if any(R[i][0] < R[j][0] + R[j][2] and R[j][0] < R[i][0] + R[i][2]
                           and R[i][1] < R[j][1] + R[j][3] and R[j][1] < R[i][1] + R[i][3]
                           for i in (a, b) for j in ids if j != i):
                        continue
                    s2 = _level_score(ids, size, trial, edges)
                    if s2 < score:
                        pos, score, better = trial, s2, True
        x0 = min((pos[i][0] for i in ids), default=0)
        y0 = min((pos[i][1] for i in ids), default=0)
        rel[parent] = {i: (pos[i][0] - x0, pos[i][1] - y0) for i in ids}
        return (max((pos[i][0] - x0 + size[i][0] for i in ids), default=0),
                max((pos[i][1] - y0 + size[i][1] for i in ids), default=0))

    rel = {}
    place(None)

    def put(parent, ox, oy):
        for i, (x, y) in rel.get(parent, {}).items():
            n = N[i]
            n.pop("w", None), n.pop("h", None)
            n["x"], n["y"] = round(ox + x), round(oy + y)
            if kids.get(i):
                put(i, n["x"] + CPAD, n["y"] + own(n)[1] + CPAD)

    put(None, 0, 0)
    refine(d, kids)
    return d


def refine(d, kids):
    """On the whole board, where arrows leaving a box count too: mirror each
    box's inside, and swap two sibling boxes, whenever that lowers the
    crossings plus the arrows over a box. Fixed order, so deterministic."""
    score = sum(crossings(d))

    def siblings_clear(ids):
        return not any(overlaps(d, i) for i in ids)

    better = True
    while better:
        better = False
        for parent in sorted(kids, key=lambda k: (k is not None, k or "")):
            ids = kids[parent]
            if len(ids) < 2:
                continue
            B = bounds(d)
            x0 = min(B[i][0] for i in ids)
            x1 = max(B[i][0] + B[i][2] for i in ids)
            y0 = min(B[i][1] for i in ids)
            y1 = max(B[i][1] + B[i][3] for i in ids)
            for axis in ("x", "y"):
                moves = {i: ((x0 + x1 - 2 * B[i][0] - B[i][2]) if axis == "x" else 0,
                             (y0 + y1 - 2 * B[i][1] - B[i][3]) if axis == "y" else 0) for i in ids}
                for i, (dx, dy) in moves.items():
                    move_tree(d, i, dx, dy)
                s2 = sum(crossings(d))
                if s2 < score:
                    score, better = s2, True
                else:
                    for i, (dx, dy) in moves.items():
                        move_tree(d, i, -dx, -dy)
            for k, a in enumerate(ids):
                for b in ids[k + 1:]:
                    B = bounds(d)
                    da = (B[b][0] - B[a][0], B[b][1] - B[a][1])
                    move_tree(d, a, *da)
                    move_tree(d, b, -da[0], -da[1])
                    s2 = sum(crossings(d)) if siblings_clear((a, b)) else score
                    if s2 < score:
                        score, better = s2, True
                    else:
                        move_tree(d, a, -da[0], -da[1])
                        move_tree(d, b, *da)


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
    ap.add_argument("cmd", choices=["check", "build", "crossings", "collapse", "simplify"])
    ap.add_argument("src")
    ap.add_argument("--root", default=".")
    ap.add_argument("--keep")
    ap.add_argument("--stamp")
    ap.add_argument("--out")
    ap.add_argument("--layout", action="store_true")
    a = ap.parse_args()
    doc = json.load(open(a.src))
    if a.cmd == "crossings":
        x, over = crossings(doc.get("data", doc))
        print(f"{x} crossing pairs, {over} arrows over a box")
        return 0
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
        keep = json.load(open(a.keep)) if a.keep and not a.layout else None
        d = build(doc, keep.get("data", keep) if keep else None, a.stamp, fresh=a.layout)
        json.dump(d, open(a.out, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
