#!/usr/bin/env python3
"""Seed, check and simplify an Objects Board diagram.

    board.py check    SPEC [--root DIR]
    board.py build    SPEC [--root DIR] [--keep CURRENT.json] [--stamp REV] --out DOC.json
    board.py build    SPEC --layout [--strategy NAME] [--root DIR] [--stamp REV] --out DOC.json
    board.py strategies
    board.py compare  SPEC [--root DIR] [--stamp REV] [--collapse] --out-dir DIR
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
--layout  ignore any kept positions and lay the diagram out from scratch.
        Each box's parts are laid out first: the parts no arrow leaves the
        box from in a grid in the middle, the parts arrows leave from around
        it on the side facing those arrows (the layout runs twice: the first
        pass shows where they go), same colours together (red, amber, blue,
        purple, grey, data), each part stretched to its row's height and its
        column's width so they line up. The top level is one graph of
        fixed-size blocks, by Graphviz `dot` when it is installed (both
        directions, with and without packing), a grid, and rings; of those no
        larger than COMPACT times the smallest and no longer than ASPECT to
        one, the one with the fewest arrows over a box, then crossing arrows,
        is kept; then top-level blocks swap places wherever that lowers them
        (not in rings, where a swap would take a box out of its ring), and
        each box's inside is mirrored where that does. Rings: the blocks of
        most links in the centre, each k-core shell one ring further out on
        the side facing its neighbours, and a block of one or two links last,
        in the nearest free spot beside them. Deterministic: the same spec gives
        the same layout. Each trial recounts the clutter, so the swaps are
        skipped on a level of more than SWAP_LIMIT boxes, and the whole-board
        pass (mirror and swap) on a board of more than REFINE_LIMIT arrows:
        a big codebase keeps the Graphviz (or grid) layout as it is.
strategies  the placing strategies --strategy takes: what each does, and
        what it needs. The default, `best`, tries them all and keeps the one
        with the least clutter. A strategy that cannot run stops with an
        error, it never falls back to another.
compare  build the board under every strategy, write DIR/<strategy>.json
        (each diagram named after its strategy; --collapse first merges the
        arrows) and print one row per strategy: the clutter, the board's
        size, and for the boxes of one or two links the gap to their nearest
        partner (median and worst) and how far out they sit (0 the centre,
        1 a corner).
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
KIND_RANK = ["writes", "spawns", "calls", "reads", "uses"]
SWAP_LIMIT = 40                               # siblings: pairs to try grow with the square
REFINE_LIMIT = 500                            # arrows: one whole-board clutter count grows with the square
COMPACT = 1.5                                 # a top-level layout at most this times the smallest one
ASPECT = 3                                    # ... and no longer than this to one
SHAPE = 1.6                                   # a ring turns to the short sides of a block wider than this to one
WAYS = {"layered": [("TB", False), ("LR", False), ("TB", True), ("LR", True)], "grid": ["grid"],
        "rings": ["rings"], "stress": ["neato"], "force": ["sfdp"]}
WAYS["best"] = [w for ways in WAYS.values() for w in ways]
STRATEGIES = {                                # name: (what it does, what it needs)
    "best": ("every strategy below that can run; the fewest arrows over a box, then crossings", "nothing"),
    "rings": ("concentric rings by k-core: the most linked boxes in the centre, boxes of one or two links "
              "beside their partners", "nothing"),
    "layered": ("rows in call order, callers before callees (Sugiyama)", "Graphviz dot"),
    "stress": ("boxes as far apart as the arrows between them say (stress majorisation)", "Graphviz neato"),
    "force": ("arrows as springs, boxes pushing apart (force-directed)", "Graphviz sfdp"),
    "grid": ("rows in connection order, most connected first", "nothing"),
}


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
    parent = {n["id"]: n.get("parent") for n in d["nodes"]}

    def inside(j):                            # j sits inside i's box
        p = parent.get(j)
        while p:
            if p == i:
                return True
            p = parent.get(p)
        return False

    return [j for j, b in B.items() if j != i and j not in anc
            and a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]
            and not inside(j)]


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


def build(spec, keep, stamp, fresh=False, strategy="best"):
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
        if n.get("unobserved"):
            m["unobserved"] = True
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
        return layout(d, strategy)
    # inner boxes first (they make their owner's size), then the top level
    for i in sorted(byid, key=lambda i: (-depth(i), bounds(d)[i][2] * bounds(d)[i][3])):
        if overlaps(d, i):
            nudge(d, i)
    return d


# --- layout: fewest arrows over a box, then fewest crossings ---------------------------------------------

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


def clutter(d):
    """What a layout minimises, in order: arrows over a box, then pairs of
    arrows that cross."""
    x, over = crossings(d)
    return over, x


def _graphviz(engine, text, ids, size):
    """{id: (x, y)} top-left positions from a Graphviz engine, or None."""
    exe = shutil.which(engine)
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "-Tjson"], input=text, capture_output=True, text=True, timeout=120)
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


def _quote(s):
    return '"' + s.replace('"', '\\"') + '"'


def _nodes(ids, size):
    return [f"{_quote(i)} [width={size[i][0] / 72:.3f}, height={size[i][1] / 72:.3f}];" for i in ids]


def _dot(ids, size, edges, rankdir, pack=False):
    lines = [f"digraph g {{ rankdir={rankdir}; nodesep=0.6; ranksep=0.8; ordering=out;"
             + (' pack=true; packmode="array_u";' if pack else ""),
             "node [shape=box, fixedsize=true, label=\"\"];"] + _nodes(ids, size)
    lines += [f"{_quote(a)} -> {_quote(b)};" for a, b in edges]
    return _graphviz("dot", "\n".join(lines + ["}"]), ids, size)


def _clear(ids, size, pos):
    R = {i: (*pos[i], *size[i]) for i in ids}
    return not any(R[a][0] < R[b][0] + R[b][2] and R[b][0] < R[a][0] + R[a][2]
                   and R[a][1] < R[b][1] + R[b][3] and R[b][1] < R[a][1] + R[a][3]
                   for k, a in enumerate(ids) for b in ids[k + 1:])


def _spring(ids, size, edges, engine):
    """neato or sfdp; None if the build of Graphviz cannot remove the overlaps."""
    lines = ['graph g { overlap=prism; sep="+30"; node [shape=box, fixedsize=true, label=""];'] + _nodes(ids, size)
    lines += [f"{_quote(a)} -- {_quote(b)};" for a, b in edges]
    pos = _graphviz(engine, "\n".join(lines + ["}"]), ids, size)
    return pos if pos and _clear(ids, size, pos) else None


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
    return sum(1 for s in segs for j, r in B.items() if j not in (s[0], s[1]) and _hits(s[2], s[3], r)), x


def _swap(ids, size, pos, edges):
    """Swap two siblings wherever that lowers the count, until none does."""
    better = True
    while better:
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
    return pos


def _shells(ids, nb):
    """The k-core shells of ids, centre first: peel the boxes with at most k
    neighbours left, then k + 1, each k one shell."""
    left, shells, k = set(ids), [], 0
    while left:
        shell, peel = [], True
        while peel:
            peel = [i for i in sorted(left) if len(nb[i] & left) <= k]
            shell += peel
            left -= set(peel)
        if shell:
            shells.insert(0, shell)
        k += 1
    return shells


def _extent(pos, size):
    return (min(p[0] for p in pos.values()), min(p[1] for p in pos.values()),
            max(pos[i][0] + size[i][0] for i in pos), max(pos[i][1] + size[i][1] for i in pos))


def _middle(ends):
    return sum(e[0] for e in ends) / len(ends), sum(e[1] for e in ends) / len(ends)


def _around(pos, size, shell, nb, gap):
    """One ring round the block in pos: each box on the side facing its
    neighbours, as close to them along that side as the others leave room."""
    x0, y0, x1, y1 = _extent(pos, size)
    cx, cy, w, h = (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0
    mid = lambda i: (pos[i][0] + size[i][0] / 2, pos[i][1] + size[i][1] / 2)
    want = {}
    for i in shell:
        ends = [mid(j) for j in sorted(nb[i]) if j in pos]
        if ends:
            want[i] = _middle(ends)
    for i in shell:
        if i not in want:
            ends = [want[j] for j in sorted(nb[i]) if j in want]
            want[i] = _middle(ends) if ends else (cx, y0)
    sides = {"left": [], "right": [], "top": [], "bottom": []}
    for i in shell:
        dx, dy = (want[i][0] - cx) / w, (want[i][1] - cy) / h
        if w > SHAPE * h or (abs(dy) > abs(dx) and h <= w):
            sides["bottom" if dy > 0 else "top"].append(i)
        else:
            sides["right" if dx > 0 else "left"].append(i)
    for sd in ("left", "right"):
        col = sorted(sides[sd], key=lambda i: want[i][1])
        y = y0
        for i in col:
            y = max(y, want[i][1] - size[i][1] / 2)
            pos[i] = (x0 - gap - size[i][0] if sd == "left" else x1 + gap, y)
            y += size[i][1] + gap
    x0, y0, x1, y1 = _extent(pos, size)
    for sd in ("top", "bottom"):
        todo = sorted(sides[sd], key=lambda i: want[i][0])
        edge = y0 if sd == "top" else y1
        while todo:                           # rows no wider than the block, wrapped outwards
            row, used = [], 0
            while todo and (not row or used + size[todo[0]][0] + gap <= x1 - x0):
                used += size[todo[0]][0] + gap
                row.append(todo.pop(0))
            tall = max(size[i][1] for i in row)
            top = edge - gap - tall if sd == "top" else edge + gap
            x, slack = x0, max(0, x1 - x0 - used + gap)
            for i in row:
                shift = min(slack, max(0, want[i][0] - size[i][0] / 2 - x))
                x += shift
                slack -= shift
                pos[i] = (x, top + (tall - size[i][1] if sd == "top" else 0))
                x += size[i][0] + gap
            edge = top if sd == "top" else top + tall


FAN = (0, 30, -30, 60, -60, 90, -90, 120, -120, 150, -150, 180)


def _beside(pos, size, i, nb, gap):
    """i in the nearest free spot round the middle of its neighbours, looking
    away from the block's centre first, so it stays by them and on the outside."""
    x0, y0, x1, y1 = _extent(pos, size)
    mid = lambda j: (pos[j][0] + size[j][0] / 2, pos[j][1] + size[j][1] / 2)
    tx, ty = _middle([mid(j) for j in sorted(nb[i]) if j in pos])
    out = math.atan2(ty - (y0 + y1) / 2, tx - (x0 + x1) / 2)
    w, h = size[i]
    far = math.hypot(x1 - x0, y1 - y0) + math.hypot(w, h)
    r = 0
    while r <= far:
        r += gap
        for deg in FAN:
            a = out + math.radians(deg)
            x, y = tx + r * math.cos(a) - w / 2, ty + r * math.sin(a) - h / 2
            if not any(x - gap / 2 < p[0] + size[j][0] and p[0] - gap / 2 < x + w
                       and y - gap / 2 < p[1] + size[j][1] and p[1] - gap / 2 < y + h for j, p in pos.items()):
                pos[i] = (x, y)
                return


def _rings(ids, size, edges, gap=60):
    """Concentric rings: the boxes of most links in the centre, each k-core
    shell one ring further out, on the side facing its neighbours; a box of
    one or two links last, beside them. {id: (x, y)}, or None when no box has
    more than two links, so there is no centre."""
    nb = {i: set() for i in ids}
    for a, b in edges:
        nb[a].add(b)
        nb[b].add(a)
    small = sorted((i for i in ids if 0 < len(nb[i]) <= 2), key=lambda i: (-size[i][0] * size[i][1], i))
    rest = {i: nb[i] - set(small) for i in ids if i not in small}
    if not any(nb[i] for i in rest):
        return None
    centre, *shells = _shells(rest, rest)
    inner = [e for e in edges if e[0] in centre and e[1] in centre]
    pos = _swap(centre, size, _grid(centre, size, inner, gap), inner)
    for shell in shells:
        _around(pos, size, shell, rest, gap)
    while small:
        i = next((i for i in small if nb[i] & set(pos)), None)
        if i:
            _beside(pos, size, i, nb, gap)
        else:                                 # small boxes linked only to each other start a spot of their own
            i = small[0]
            _around(pos, size, [i], nb, gap)
        small.remove(i)
    return pos


def colour(n):
    """The order a box's colour sorts in among its siblings: red, amber,
    blue (a class), purple (a process), grey (code with no class, or never
    seen at run time), data."""
    f = n.get("flag") or ""
    if f.startswith("private:") or "— private:" in f:
        return 0
    if f.startswith(("owner bypass", "shared", "doubt")) or "— owner bypass" in f or "— shared" in f:
        return 1
    if n.get("unobserved") or n["kind"] in ("module", "unowned"):
        return 4
    return {"object": 2, "process": 3}.get(n["kind"], 5)


SIDES = ("top", "right", "bottom", "left")


def _ring(ids, size, outer, key, prefer, gap=30):
    """Inside a box: the parts no arrow leaves the box from in a core, in
    rows; the parts arrows leave from around it, each on the side facing
    its arrows when `prefer` says which (else in turn), so the arrows start
    at the border. Same colours sit together. {id: (x, y)}."""
    cell = {}                                 # (w, h) each part is stretched to, so rows and columns line up

    def grid(items, cols):
        colw = [max(size[i][0] for i in items[c::cols]) for c in range(cols)]
        rowh = [max(size[i][1] for i in items[r:r + cols]) for r in range(0, len(items), cols)]
        pos = {}
        for k, i in enumerate(items):
            r, c = divmod(k, cols)
            pos[i] = (sum(colw[:c]) + gap * c, sum(rowh[:r]) + gap * r)
            cell[i] = (colw[c], rowh[r])
        return pos, sum(colw) + gap * (cols - 1), sum(rowh) + gap * (len(rowh) - 1)

    def best_grid(items):
        if not items:
            return {}, 0, 0
        def cost(cols):
            saved = dict(cell)
            _, w, h = grid(items, cols)
            cell.clear(), cell.update(saved)
            return w * h * (1 + abs(math.log(w / h / 1.6)))
        return grid(items, min(range(1, len(items) + 1), key=cost))

    inner = sorted((i for i in ids if i not in outer), key=key)
    core, cw, ch = best_grid(inner)
    total = sum(size[i][0] * size[i][1] for i in ids)
    cap = max(cw, ch, math.sqrt(total))       # the most one side holds before the next is used
    load = dict.fromkeys(SIDES, 0)
    side = {}
    span = lambda i, sd: size[i][0] if sd in ("top", "bottom") else size[i][1]
    order = sorted((i for i in ids if i in outer), key=key)
    for k, i in enumerate(order):
        want = prefer.get(i)
        tries = ([want] + [sd for sd in SIDES if sd != want]) if want else list(SIDES)
        sd = next((t for t in tries if load[t] + span(i, t) <= cap), min(SIDES, key=lambda t: load[t]))
        side[i] = sd
        load[sd] += span(i, sd) + gap
    rows = {sd: [i for i in order if side[i] == sd] for sd in SIDES}
    tw = sum(size[i][0] + gap for i in rows["top"]) - gap if rows["top"] else 0
    bw = sum(size[i][0] + gap for i in rows["bottom"]) - gap if rows["bottom"] else 0
    lh = sum(size[i][1] + gap for i in rows["left"]) - gap if rows["left"] else 0
    rh = sum(size[i][1] + gap for i in rows["right"]) - gap if rows["right"] else 0
    T = max((size[i][1] for i in rows["top"]), default=-gap) + gap
    for sd in ("top", "bottom"):
        h = max((size[i][1] for i in rows[sd]), default=0)
        for i in rows[sd]:
            cell[i] = (size[i][0], h)
    for sd in ("left", "right"):
        w = max((size[i][0] for i in rows[sd]), default=0)
        for i in rows[sd]:
            cell[i] = (w, size[i][1])
    L = max((cell[i][0] for i in rows["left"]), default=-gap) + gap
    W, H = max(cw, tw, bw), max(ch, lh, rh)
    pos = {i: (L + (W - cw) / 2 + x, T + (H - ch) / 2 + y) for i, (x, y) in core.items()}
    x = L + (W - tw) / 2
    for i in rows["top"]:
        pos[i] = (x, T - gap - cell[i][1]); x += size[i][0] + gap
    x = L + (W - bw) / 2
    for i in rows["bottom"]:
        pos[i] = (x, T + H + gap); x += size[i][0] + gap
    y = T + (H - lh) / 2
    for i in rows["left"]:
        pos[i] = (L - gap - cell[i][0], y); y += size[i][1] + gap
    y = T + (H - rh) / 2
    for i in rows["right"]:
        pos[i] = (L + W + gap, y); y += size[i][1] + gap
    return pos, cell


def layout(d, strategy="best"):
    """Lay d out from scratch under a strategy of WAYS; returns d. Twice: the
    first pass shows where each part's arrows go, the second puts each
    part that has arrows leaving its box on the side facing them. ValueError
    when the strategy cannot run on this diagram."""
    chosen = {"ways": WAYS[strategy]}
    _layout(d, {}, chosen)
    N = {n["id"]: n for n in d["nodes"]}
    B = bounds(d)
    chain = {i: {i} | ancestors(d, i) for i in N}
    far = {}                                  # part -> the far ends of the arrows that leave its box
    for e in d["edges"]:
        if e["from"] not in N or e["to"] not in N:
            continue
        for a, b in ((e["from"], e["to"]), (e["to"], e["from"])):
            for i in chain[a] - chain[b]:
                p = N[i].get("parent")
                if p in N and p not in chain[b]:
                    far.setdefault(i, []).append(b)
    prefer = {}
    for i, ends in far.items():
        p = N[i]["parent"]
        cx, cy = B[p][0] + B[p][2] / 2, B[p][1] + B[p][3] / 2
        dx = sum(B[j][0] + B[j][2] / 2 for j in ends) / len(ends) - cx
        dy = sum(B[j][1] + B[j][3] / 2 for j in ends) / len(ends) - cy
        prefer[i] = (("right" if dx > 0 else "left") if abs(dx) * B[p][3] >= abs(dy) * B[p][2] else
                     ("bottom" if dy > 0 else "top"))
    return _layout(d, prefer, chosen)


def _layout(d, prefer, chosen):
    """One layout pass; `chosen` keeps the top level's way (Graphviz
    direction and packing, or the grid) so a second pass makes only that one."""
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
        if parent is not None:
            outer = {under(e[k], level) for e in d["edges"] for k, o in (("from", "to"), ("to", "from"))
                     if under(e[k], level) and not under(e[o], level)}
            pos, cell = _ring(ids, size, outer, lambda i: (colour(N[i]), N[i]["name"]), prefer)
            for i, wh in cell.items():
                if not kids.get(i):           # a box holding others is sized by them
                    stretch[i] = wh
                    size[i] = wh
            x0 = min((pos[i][0] for i in ids), default=0)
            y0 = min((pos[i][1] for i in ids), default=0)
            rel[parent] = {i: (pos[i][0] - x0, pos[i][1] - y0) for i in ids}
            return (max((pos[i][0] - x0 + size[i][0] for i in ids), default=0),
                    max((pos[i][1] - y0 + size[i][1] for i in ids), default=0))
        pairs = {(under(e["from"], level), under(e["to"], level)) for e in d["edges"]}
        edges = sorted((a, b) for a, b in pairs if a and b and a != b)
        group = {i: i for i in ids}           # packing changes nothing on a graph in one piece
        root = lambda i: i if group[i] == i else root(group[i])
        for a, b in edges:
            group[root(a)] = root(b)
        pieces = len({root(i) for i in ids})
        ways = [w for w in chosen["ways"]
                if (isinstance(w, str) or not w[1] or pieces > 1) and (chosen.get("way") in (None, w))]
        make = {"grid": lambda: _grid(ids, size, edges), "rings": lambda: _rings(ids, size, edges),
                "neato": lambda: _spring(ids, size, edges, "neato"), "sfdp": lambda: _spring(ids, size, edges, "sfdp")}
        made = {w: make[w]() if isinstance(w, str) else _dot(ids, size, edges, *w) for w in ways}
        made = {w: p for w, p in made.items() if p}
        if not made:
            raise ValueError("Graphviz is missing or failed" if chosen["ways"] != WAYS["rings"]
                             else "no box has more than two links, so rings have no centre")
        cands = list(made.values())

        def box(p):
            w = max(p[i][0] + size[i][0] for i in ids) - min(p[i][0] for i in ids)
            h = max(p[i][1] + size[i][1] for i in ids) - min(p[i][1] for i in ids)
            return w * h, max(w / h, h / w)
        least = min(box(p)[0] for p in cands)
        fit = [p for p in cands if box(p)[0] <= COMPACT * least and box(p)[1] <= ASPECT] or [min(cands, key=lambda p: box(p)[0])]
        pos = min(fit, key=lambda p: _level_score(ids, size, p, edges))
        chosen["way"] = next(w for w, p in made.items() if p is pos)
        if chosen["way"] != "rings" and len(ids) <= SWAP_LIMIT:   # a swap would take a box out of its ring
            pos = _swap(ids, size, pos, edges)
        x0 = min((pos[i][0] for i in ids), default=0)
        y0 = min((pos[i][1] for i in ids), default=0)
        rel[parent] = {i: (pos[i][0] - x0, pos[i][1] - y0) for i in ids}
        return (max((pos[i][0] - x0 + size[i][0] for i in ids), default=0),
                max((pos[i][1] - y0 + size[i][1] for i in ids), default=0))

    rel, stretch = {}, {}
    place(None)

    def put(parent, ox, oy):
        for i, (x, y) in rel.get(parent, {}).items():
            n = N[i]
            n.pop("w", None), n.pop("h", None)
            if i in stretch:
                n["w"], n["h"] = map(math.ceil, stretch[i])
            n["x"], n["y"] = round(ox + x), round(oy + y)
            if kids.get(i):
                put(i, n["x"] + CPAD, n["y"] + own(n)[1] + CPAD)

    put(None, 0, 0)
    if len(d["edges"]) <= REFINE_LIMIT:
        refine(d, kids, swap=chosen["way"] != "rings")
    return d


def refine(d, kids, swap=True):
    """On the whole board, where arrows leaving a box count too: mirror each
    box's inside, and swap two sibling boxes, whenever that lowers the
    clutter (arrows over a box first, then crossings). Fixed order, so
    deterministic."""
    score = clutter(d)

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
                s2 = clutter(d)
                if s2 < score:
                    score, better = s2, True
                else:
                    for i, (dx, dy) in moves.items():
                        move_tree(d, i, -dx, -dy)
            for k, a in enumerate(ids if swap and parent is None and len(ids) <= SWAP_LIMIT else []):
                for b in ids[k + 1:]:
                    B = bounds(d)
                    da = (B[b][0] - B[a][0], B[b][1] - B[a][1])
                    move_tree(d, a, *da)
                    move_tree(d, b, -da[0], -da[1])
                    s2 = clutter(d) if siblings_clear((a, b)) else score
                    if s2 < score:
                        score, better = s2, True
                    else:
                        move_tree(d, a, -da[0], -da[1])
                        move_tree(d, b, *da)


def placing(d):
    """How a layout reads: {over, cross, width, height, gap_median, gap_worst,
    out_median}. gap: from a top-level box of one or two links to its nearest
    partner; out: how far from the board's centre it sits, 1 at a corner.
    The last three are None on a board with no such box."""
    N = {n["id"]: n for n in d["nodes"]}
    B = bounds(d)

    def top(i):
        while N[i].get("parent") in N:
            i = N[i]["parent"]
        return i
    tops = sorted(i for i in N if N[i].get("parent") not in N)
    nb = {i: set() for i in tops}
    for e in d["edges"]:
        if e["from"] in N and e["to"] in N and top(e["from"]) != top(e["to"]):
            nb[top(e["from"])].add(top(e["to"]))
            nb[top(e["to"])].add(top(e["from"]))

    def gap(a, b):
        return math.hypot(max(b[0] - a[0] - a[2], a[0] - b[0] - b[2], 0), max(b[1] - a[1] - a[3], a[1] - b[1] - b[3], 0))
    x0, y0 = min(B[i][0] for i in tops), min(B[i][1] for i in tops)
    x1, y1 = max(B[i][0] + B[i][2] for i in tops), max(B[i][1] + B[i][3] for i in tops)
    over, cross = clutter(d)
    small = [i for i in tops if 0 < len(nb[i]) <= 2]
    gaps = sorted(min(gap(B[i], B[j]) for j in nb[i]) for i in small)
    half = math.hypot(x1 - x0, y1 - y0) / 2
    outs = sorted(math.hypot(B[i][0] + B[i][2] / 2 - (x0 + x1) / 2, B[i][1] + B[i][3] / 2 - (y0 + y1) / 2) / half
                  for i in small)
    return {"over": over, "cross": cross, "width": x1 - x0, "height": y1 - y0,
            "gap_median": gaps[len(gaps) // 2] if gaps else None, "gap_worst": gaps[-1] if gaps else None,
            "out_median": outs[len(outs) // 2] if outs else None}


def compare(spec, stamp, out_dir, collapsed=False):
    """Build the board under every strategy into out_dir; {strategy: placing(d)
    or the reason it could not run}."""
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = {}
    for name in STRATEGIES:
        if name == "best":
            continue
        try:
            d = build(spec, None, stamp, fresh=True, strategy=name)
        except ValueError as e:
            rows[name] = str(e)
            continue
        d = collapse(d) if collapsed else d
        d["name"] += f" — {name}"
        json.dump(d, open(out / f"{name}.json", "w"), indent=1)
        rows[name] = placing(d)
    return rows


def render_compare(rows):
    fmt = "{:<9} {:>5} {:>7} {:>13} {:>10} {:>10} {:>5}"
    lines = [fmt.format("strategy", "over", "crosses", "board", "gap median", "gap worst", "out")]
    for name, r in rows.items():
        if isinstance(r, str):
            lines.append(f"{name:<9} cannot run: {r}")
            continue
        none = lambda v, f: "-" if v is None else f.format(v)
        lines.append(fmt.format(name, r["over"], r["cross"], f"{r['width']:.0f} x {r['height']:.0f}",
                                none(r["gap_median"], "{:.0f}"), none(r["gap_worst"], "{:.0f}"),
                                none(r["out_median"], "{:.2f}")))
    return "\n".join(lines)


# --- simplify -------------------------------------------------------------------

def merge(edges, key, prefix):
    merged = {}
    for e in edges:
        a, b = key(e["from"]), key(e["to"])
        if a == b:
            continue
        v = merged.setdefault((a, b), {"kinds": [], "labels": [], "flags": []})
        v["kinds"].append(e["kind"])
        if e.get("flag") and e["flag"] not in v["flags"]:
            v["flags"].append(e["flag"])
        for part in (x.strip() for x in (e.get("label") or "").split(" · ")):
            if part and part not in v["labels"]:
                v["labels"].append(part)
    out = []
    for i, ((a, b), v) in enumerate(merged.items()):
        lab = " · ".join(v["labels"][:3]) + (f"  (+{len(v['labels']) - 3})" if len(v["labels"]) > 3 else "")
        out.append({"id": f"{prefix}{i + 1}", "from": a, "to": b,
                    "kind": next(k for k in KIND_RANK if k in v["kinds"]), "label": lab})
        if v["flags"]:                        # a merged pair keeps every finding, private access first
            out[-1]["flag"] = " — ".join(sorted(v["flags"], key=lambda f: not f.startswith("private:")))
    return out


def collapse(d):
    return {"name": d["name"] + " — collapsed", "nodes": d["nodes"], "edges": merge(d["edges"], lambda i: i, "c")}


def simplify(d):
    N = {n["id"]: n for n in d["nodes"]}

    def top(i):                               # a group is no owner
        while N[i].get("parent") and N[N[i]["parent"]].get("kind") not in ("unowned", "resources"):
            i = N[i]["parent"]
        return i

    def inside(i):
        return [n for n in d["nodes"] if top(n["id"]) == i and n["id"] != i]

    out = {"name": d["name"] + " — simplified", "nodes": [], "edges": []}
    for n in d["nodes"]:
        if top(n["id"]) != n["id"]:
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
    ap.add_argument("cmd", choices=["check", "build", "strategies", "compare", "crossings", "collapse", "simplify"])
    ap.add_argument("src", nargs="?")
    ap.add_argument("--root", default=".")
    ap.add_argument("--keep")
    ap.add_argument("--stamp")
    ap.add_argument("--out")
    ap.add_argument("--layout", action="store_true")
    ap.add_argument("--strategy", choices=list(STRATEGIES), default="best")
    ap.add_argument("--collapse", action="store_true")
    ap.add_argument("--out-dir")
    a = ap.parse_args()
    if a.cmd == "strategies":
        for name, (what, needs) in STRATEGIES.items():
            print(f"{name:<8} {what} (needs: {needs})")
        return 0
    if not a.src:
        ap.error(f"{a.cmd} needs a file")
    if a.strategy != "best" and not (a.cmd == "build" and a.layout):
        ap.error("--strategy goes with build --layout")
    if a.cmd == "compare" and not a.out_dir:
        ap.error("compare needs --out-dir")
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
    if a.cmd == "compare":
        print(render_compare(compare(doc, a.stamp, a.out_dir, a.collapse)))
    if a.cmd == "build":
        keep = json.load(open(a.keep)) if a.keep and not a.layout else None
        try:
            d = build(doc, keep.get("data", keep) if keep else None, a.stamp, fresh=a.layout, strategy=a.strategy)
        except ValueError as e:
            print(f"{a.strategy} cannot run: {e}", file=sys.stderr)
            return 1
        json.dump(d, open(a.out, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
