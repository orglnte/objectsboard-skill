import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
import board  # noqa: E402

APP = {
    "app/__init__.py": "",
    "app/report.py": '''
from app import store
from .store import Store as S


def show(path):
    return S(path).get(store.Store.LIMIT)
''',
    "tests/test_app.py": '''
from app.service import Service, run
from app.store import Store
from app.report import show


def test_run():
    assert run(Service(Store("p"))) == 3


def test_show():
    assert show("p") == 3
''',
    "app/store.py": '''
class Store:
    LIMIT = 3

    def __init__(self, path):
        self.path = path

    # --- reading ---
    def get(self, key):
        return key

    def unused(self):
        return None
''',
    "app/service.py": '''
class Service:
    def __init__(self, store):
        self._store = store

    @property
    def store(self):
        return self._store


def run(svc):
    s = svc.store
    s.get("a")
    return svc.store.LIMIT
''',
}


@pytest.fixture
def app(tmp_path):
    for rel, text in APP.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return tmp_path


def extract(app, *extra):
    out = app / "data.json"
    subprocess.run([sys.executable, str(TOOLS / "extract_interface.py"), "--target", "app/store.py:Store",
                    "--scan", "app=app", *extra, "--out", str(out)], cwd=app, check=True, capture_output=True)
    return json.loads(out.read_text())


def test_without_attr_a_property_hides_every_use(app):
    assert not [u for u in extract(app)["uses"] if u["file"].endswith("service.py")]


def test_attr_follows_the_property_and_a_name_bound_from_it(app):
    used = {u["member"] for u in extract(app, "--attr", "store")["uses"]}
    assert used == {"get", "LIMIT"}


def test_members_carry_their_section(app):
    members = {m["name"]: m["section"] for m in extract(app)["members"]}
    assert members["get"] == "reading" and members["LIMIT"] == "(top)"


SPEC = {"name": "store", "nodes": [{"id": "store", "name": "Store", "kind": "object"},
                                   {"id": "svc", "name": "service", "kind": "module"}],
        "edges": [{"from": "svc", "to": "store", "kind": "calls", "label": "get",
                   "proof": [{"file": "app/service.py", "pattern": r"s\.get\("}]}]}


def test_check_passes_on_a_matching_proof(app):
    assert board.check(SPEC, app) == []


def test_check_refuses_a_missing_match_and_a_missing_proof(app):
    spec = json.loads(json.dumps(SPEC))
    spec["edges"][0]["proof"][0]["pattern"] = r"s\.put\("
    spec["edges"].append({"from": "store", "to": "svc", "kind": "reads", "label": "x"})
    bad = board.check(spec, app)
    assert len(bad) == 2 and "no match" in bad[0] and "no proof" in bad[1]


def test_build_keeps_the_users_positions_and_places_new_boxes_clear():
    keep = {"nodes": [{"id": "store", "x": 500, "y": 300}]}
    d = board.build(SPEC, keep, "abc1234")
    pos = {n["id"]: (n["x"], n["y"]) for n in d["nodes"]}
    assert pos["store"] == (500, 300)
    assert board.overlaps(d, "svc") == []
    assert d["name"] == "store (abc1234)"


def test_simplify_merges_per_pair_and_drops_arrows_into_own_parts():
    d = {"name": "x", "nodes": [{"id": "a", "name": "A", "x": 0, "y": 0},
                                {"id": "a1", "name": "A1", "parent": "a", "x": 10, "y": 40},
                                {"id": "b", "name": "B", "x": 400, "y": 0}],
         "edges": [{"from": "b", "to": "a", "kind": "reads", "label": "one"},
                   {"from": "b", "to": "a1", "kind": "writes", "label": "two"},
                   {"from": "a1", "to": "a", "kind": "calls", "label": "inner"}]}
    s = board.simplify(d)
    assert [(e["from"], e["to"], e["kind"], e["label"]) for e in s["edges"]] == [("b", "a", "writes", "one · two")]
    assert {n["id"] for n in s["nodes"]} == {"a", "b"}


def test_module_target_lists_importers_through_alias_from_import_and_relative_import(app):
    out = app / "mod.json"
    subprocess.run([sys.executable, str(TOOLS / "extract_interface.py"), "--target", "app/store.py",
                    "--scan", "app=app", "--out", str(out)], cwd=app, check=True, capture_output=True)
    d = json.loads(out.read_text())
    assert d["module"] == "app.store" and {m["name"] for m in d["members"]} == {"Store"}
    assert {(Path(u["file"]).name, u["via"]) for u in d["uses"]} >= {("report.py", "module.Store"), ("report.py", "name")}


def test_trace_records_calls_from_outside_and_not_from_the_class_itself(app):
    out = app / "trace.json"
    subprocess.run([sys.executable, str(TOOLS / "trace_uses.py"), "--target", "app/store.py:Store",
                    "--out", str(out), "--", "tests", "-q", "-p", "no:cacheprovider"],
                   cwd=app, check=True, capture_output=True)
    d = json.loads(out.read_text())
    assert d["pytest_exit"] == 0
    prod = {(Path(u["file"]).name, u["member"]) for u in d["uses"] if not u["test"]}
    assert ("service.py", "get") in prod and ("report.py", "get") in prod
    assert ("report.py", "__init__") in prod
    assert all(u["file"] != "app/store.py" for u in d["uses"])


def test_check_refuses_an_arrow_between_a_container_and_its_own_part(app):
    spec = json.loads(json.dumps(SPEC))
    spec["nodes"].append({"id": "part", "name": "Store.reader", "kind": "object", "parent": "store"})
    spec["edges"].append({"from": "store", "to": "part", "kind": "calls", "label": "read",
                          "proof": [{"file": "app/store.py", "pattern": "def get"}]})
    bad = board.check(spec, app)
    assert len(bad) == 1 and "own part" in bad[0]


def test_collapse_keeps_the_boxes_and_merges_arrows_per_pair():
    d = {"name": "x", "nodes": [{"id": "a", "name": "A", "x": 0, "y": 0}, {"id": "b", "name": "B", "x": 400, "y": 0}],
         "edges": [{"from": "a", "to": "b", "kind": "calls", "label": "get"},
                   {"from": "a", "to": "b", "kind": "reads", "label": "config.json"},
                   {"from": "b", "to": "a", "kind": "calls", "label": "notify"}]}
    c = board.collapse(d)
    assert c["nodes"] == d["nodes"]
    assert [(e["from"], e["to"], e["kind"], e["label"]) for e in c["edges"]] == [
        ("a", "b", "calls", "get · config.json"), ("b", "a", "calls", "notify")]


def test_trace_objects_records_calls_between_objects_not_within_one(app):
    out, spec = app / "objects.json", app / "spec.json"
    subprocess.run([sys.executable, str(TOOLS / "trace_objects.py"), "--root", "app", "--out", str(out),
                    "--spec", str(spec), "--", "tests", "-q", "-p", "no:cacheprovider"],
                   cwd=app, check=True, capture_output=True)
    d = json.loads(out.read_text())
    assert d["pytest_exit"] == 0
    pairs = {(e["from"], e["to"]) for e in d["edges"]}
    assert ("app/service.py", "app/store.py:Store") in pairs
    assert ("app/report.py", "app/store.py:Store") in pairs
    assert all(a != b for a, b in pairs)
    s = json.loads(spec.read_text())
    assert not [n for n in s["nodes"] if n["kind"] == "folder" and n.get("parent")]
    assert board.check(s, app) == []


def test_build_stacks_new_nested_boxes_inside_their_parent_without_overlap():
    spec = {"name": "n", "nodes": [{"id": "o", "name": "Owner"}, {"id": "a", "name": "PartA", "parent": "o"},
                                   {"id": "b", "name": "PartB", "parent": "o"}, {"id": "u", "name": "User"}],
            "edges": [{"from": "u", "to": "o", "kind": "calls", "label": "run"}]}
    d = board.build(spec, None, None)
    assert board.overlaps(d, "a") == [] and board.overlaps(d, "b") == [] and board.overlaps(d, "u") == []
    B = board.bounds(d)
    assert B["o"][0] <= B["a"][0] and B["a"][1] + B["a"][3] <= B["o"][1] + B["o"][3]


REPR = {
    "lib/__init__.py": "",
    "lib/shapes.py": '''
from dataclasses import dataclass


@dataclass
class Area:
    value: float


class Shape:
    def area(self):
        return Area(self._size())

    def _size(self):
        return _square(2)


class Square(Shape):
    pass


def _square(x):
    return x * x
''',
    "lib/report/__init__.py": "",
    "lib/report/report.py": '''
from pathlib import Path
from lib.report import fmt


class Report:
    def show(self, shape):
        (Path(".") / "out").mkdir(exist_ok=True)
        return fmt.fmt(shape.area().value)
''',
    "lib/report/fmt.py": '''
def fmt(x):
    return str(x)
''',
    "lib/util.py": '''
from pathlib import Path
from lib.report import fmt


def line():
    (Path(".") / "out").exists()
    return fmt.fmt(2)
''',
}
MAP = ("| Concept | Description | Aliases | Objects | Concerns |\n|---|---|---|---|---|\n"
       "| Shape | a shape | - | `Shape` | geometry |\n| Report | output | - | `Report` | output |\n\n"
       "| Resource | Kind | Owner | Reached by |\n|---|---|---|---|\n"
       "| `out/` | folder | `Report` | `/ \"out\"` |\n")


def test_objects_representation_rules(tmp_path):
    import trace_objects
    for rel, text in REPR.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (tmp_path / "MAP.md").write_text(MAP)
    cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        fns, objs = trace_objects.objects("lib")
        edges = [{"from": "lib/report/report.py:Report", "to": "lib/shapes.py:Shape", "members": ["area"]},
                 {"from": "lib/report/report.py:Report", "to": "lib/report/fmt.py", "members": ["fmt"]},
                 {"from": "lib/util.py", "to": "lib/report/fmt.py", "members": ["fmt"]},
                 {"from": "lib/shapes.py:Shape", "to": "lib/shapes.py", "members": ["_square"]}]
        s = trace_objects.spec_of(objs, edges, "lib", "MAP.md")
    finally:
        os.chdir(cwd)
    N = {n["id"]: n for n in s["nodes"]}
    assert "lib/shapes.py:Square" not in N and "lib/shapes.py" not in N   # subclass and helpers fold into Shape
    assert N["lib/report/fmt.py"]["parent"] == "lib/report/report.py:Report"   # report/ names Report
    assert N["lib/util.py"]["kind"] == "external"                              # the root folder owns nothing
    assert [(m["into"], m["other_users"]) for m in s["moves"] if m["module"] == "lib/report/fmt.py"] == [
        ("lib/report/report.py:Report", ["lib/util.py"])]                       # util goes around Report
    assert [n["kind"] for n in s["nodes"] if n["id"] == "resource:out/"] == ["folder"]
    res = [e for e in s["edges"] if e["to"] == "resource:out/"]
    assert sorted((e["from"], e["kind"], bool(e.get("flag"))) for e in res) == [
        ("lib/report/report.py:Report", "writes", False), ("lib/util.py", "reads", True)]   # its only writer owns it
    assert "lib/util.py" in N["lib/report/fmt.py"]["flag"] or "util.py" in N["lib/report/fmt.py"]["flag"]
    flagged = [(e["from"], e["to"]) for e in s["edges"] if e.get("flag")]
    assert ("lib/util.py", "lib/report/fmt.py") in flagged                   # the arrow around Report


def test_build_nudges_a_kept_box_a_grown_neighbour_now_covers():
    spec = {"name": "g", "nodes": [{"id": "big", "name": "Big"}, {"id": "p1", "name": "Part one", "parent": "big"},
                                   {"id": "p2", "name": "Part two", "parent": "big"}, {"id": "near", "name": "Near"}],
            "edges": []}
    keep = {"nodes": [{"id": "big", "x": 0, "y": 0}, {"id": "near", "x": 0, "y": 60}]}
    d = board.build(spec, keep, None)
    assert board.overlaps(d, "near") == [] and board.overlaps(d, "big") == []
    pos = {n["id"]: (n["x"], n["y"]) for n in d["nodes"]}
    assert pos["big"] == (0, 0)


def test_legitimate_calls_between_two_owners_are_one_arrow(tmp_path):
    import trace_objects
    for rel, text in REPR.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (tmp_path / "MAP.md").write_text(MAP)
    (tmp_path / "lib/report/fmt.py").write_text(REPR["lib/report/fmt.py"] + "\n\ndef width(s):\n    return len(s.area().value)\n")
    cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        fns, objs = trace_objects.objects("lib")
        edges = [{"from": "lib/report/report.py:Report", "to": "lib/shapes.py:Shape", "members": ["area"]},
                 {"from": "lib/report/fmt.py", "to": "lib/shapes.py:Shape", "members": ["area"]}]
        s = trace_objects.spec_of(objs, edges, "lib", "MAP.md")
    finally:
        os.chdir(cwd)
    into_shape = [(e["from"], e["to"]) for e in s["edges"] if e["to"] == "lib/shapes.py:Shape"]
    assert into_shape == [("lib/report/report.py:Report", "lib/shapes.py:Shape")]   # Report and its part: one arrow


def test_worklist_ranks_the_most_bypassed_part_first_with_its_call_sites(tmp_path):
    import trace_objects
    files = dict(REPR, **{"lib/cli.py": "from lib.report import fmt\n\n\ndef main():\n    print(fmt.fmt(1))\n"})
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (tmp_path / "MAP.md").write_text(MAP)
    cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        fns, objs = trace_objects.objects("lib")
        edges = [{"from": "lib/report/report.py:Report", "to": "lib/report/fmt.py", "members": ["fmt"]},
                 {"from": "lib/util.py", "to": "lib/report/fmt.py", "members": ["fmt"]},
                 {"from": "lib/cli.py", "to": "lib/report/fmt.py", "members": ["fmt"]}]
        w = trace_objects.worklist(trace_objects.spec_of(objs, edges, "lib", "MAP.md"))
    finally:
        os.chdir(cwd)
    assert [(g["part"], g["owner"], len(g["arrows"])) for g in w] == [
        ("fmt.py", "Report", 2), ("out/", "Report", 1)]                     # two arrows around Report first
    by = {a["from"]: a["sites"] for a in w[0]["arrows"]}
    assert by == {"cli.py": ["lib/cli.py:1", "lib/cli.py:5"], "util.py": ["lib/util.py:3", "lib/util.py:8"]}
    assert w[1]["arrows"][0]["sites"] == ["lib/util.py:7"]                  # the data reached directly


FACTORY = {
    "lib/__init__.py": "",
    "lib/engine.py": '''
class Engine:
    def go(self):
        return 1


class FastEngine(Engine):
    pass
''',
    "lib/pick.py": '''
from lib.engine import FastEngine


def pick(fast):
    return FastEngine() if fast else None
''',
    "lib/runner.py": '''
from lib.pick import pick


class Runner:
    def __init__(self):
        self.engine = pick(True)

    def run(self):
        return self.engine.go()
''',
}
FACTORY_MAP = ("| Concept | Description | Aliases | Objects | Concerns |\n|---|---|---|---|---|\n"
               "| Runner | runs | - | `Runner` | running |\n| Engine | works | - | `Engine` | work |\n")


def test_a_class_kept_through_a_factory_function_nests_in_its_keeper(tmp_path):
    import trace_objects
    for rel, text in FACTORY.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (tmp_path / "MAP.md").write_text(FACTORY_MAP)
    cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        fns, objs = trace_objects.objects("lib")
        assert trace_objects.owners("lib", objs) == {"lib/engine.py:FastEngine": "lib/runner.py:Runner"}
        edges = [{"from": "lib/runner.py:Runner", "to": "lib/engine.py:Engine", "members": ["go"]}]
        s = trace_objects.spec_of(objs, edges, "lib", "MAP.md")
    finally:
        os.chdir(cwd)
    N = {n["id"]: n for n in s["nodes"]}
    assert N["lib/engine.py:Engine"]["parent"] == "lib/runner.py:Runner"   # the subclass folds into its base


def test_a_kept_helper_class_does_not_make_its_files_class_owned(tmp_path):
    import trace_objects
    files = dict(FACTORY, **{"lib/engine.py": FACTORY["lib/engine.py"] + "\n\nclass Note:\n    pass\n",
                             "lib/runner.py": FACTORY["lib/runner.py"].replace(
                                 "self.engine = pick(True)", "self.note = Note()").replace(
                                 "from lib.pick import pick", "from lib.engine import Note")})
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (tmp_path / "MAP.md").write_text(FACTORY_MAP)
    cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        fns, objs = trace_objects.objects("lib")
        edges = [{"from": "lib/runner.py:Runner", "to": "lib/engine.py:Engine", "members": ["go"]}]
        s = trace_objects.spec_of(objs, edges, "lib", "MAP.md")
    finally:
        os.chdir(cwd)
    parents = {n["id"]: n.get("parent") for n in s["nodes"]}
    assert "lib/engine.py:Engine" in parents and parents["lib/engine.py:Engine"] is None


def _box(i, x, y, parent=None):
    return {"id": i, "name": i, "kind": "object", "members": [], "note": "", "parent": parent, "x": x, "y": y}


def test_crossings_counts_crossing_arrows_and_arrows_over_a_box():
    d = {"nodes": [_box("a", 0, 0), _box("b", 600, 400), _box("c", 600, 0), _box("d", 0, 400), _box("m", 300, 200)],
         "edges": [{"from": "a", "to": "b"}, {"from": "c", "to": "d"}]}
    assert board.crossings(d) == (1, 2)                 # the X, and both arrows over the box in the middle


def _square_spec():
    nodes = [_box(i, 0, 0) for i in ("a", "b", "c", "d", "e")] + [_box("p1", 0, 0, "e"), _box("p2", 0, 0, "e")]
    edges = [("a", "c"), ("b", "d"), ("a", "d"), ("b", "c"), ("p1", "a"), ("p2", "d"), ("c", "e")]
    return {"name": "sq", "nodes": nodes,
            "edges": [{"from": f, "to": t, "kind": "calls", "label": "", "proof": [{"observed": "run"}]} for f, t in edges]}


@pytest.mark.parametrize("dot", [True, False])
def test_layout_is_deterministic_clear_of_overlaps_and_keeps_parts_inside(monkeypatch, dot):
    if not dot:
        monkeypatch.setattr(board.shutil, "which", lambda _: None)
    elif not board.shutil.which("dot"):
        pytest.skip("Graphviz not installed")
    d1 = board.build(_square_spec(), None, None, fresh=True)
    d2 = board.build(_square_spec(), None, None, fresh=True)
    assert d1 == d2
    assert all(board.overlaps(d1, n["id"]) == [] for n in d1["nodes"])
    B = board.bounds(d1)
    for p in ("p1", "p2"):
        assert B["e"][0] < B[p][0] and B[p][0] + B[p][2] < B["e"][0] + B["e"][2]
        assert B["e"][1] < B[p][1] and B[p][1] + B[p][3] < B["e"][1] + B["e"][3]
    stacked = board.build(_square_spec(), None, None)   # the default placement, for comparison
    assert board.clutter(d1) <= board.clutter(stacked)


OVERLAY_MAP = ("| Concept | Description | Aliases | Objects | Concerns |\n|---|---|---|---|---|\n"
               "| Geometry | shapes | - | `Shape`, `Report` | - |\n| Output | output | - | `Report` | - |\n"
               "| Ghost | nothing | - | `Nowhere` | - |\n\n"
               "| Resource | Kind | Owner | Reached by |\n|---|---|---|---|\n"
               "| `out/` | folder | `Shape` | `/ \"out\"` |\n")


def test_boxes_and_data_owners_come_from_the_code_and_concepts_only_overlay(tmp_path):
    import trace_objects
    files = dict(REPR, **{"lib/errors.py": "class BadShape(Exception):\n    def why(self):\n        return 1\n",
                          "lib/util.py": REPR["lib/util.py"].replace(".exists()", ".mkdir(exist_ok=True)")})
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (tmp_path / "MAP.md").write_text(OVERLAY_MAP)
    cwd = Path.cwd()
    edges = [{"from": "lib/report/report.py:Report", "to": "lib/shapes.py:Shape", "members": ["area"]},
             {"from": "lib/util.py", "to": "lib/errors.py:BadShape", "members": ["why"]}]
    try:
        os.chdir(tmp_path)
        fns, objs = trace_objects.objects("lib")
        bare = trace_objects.spec_of(objs, edges, "lib")
        s = trace_objects.spec_of(objs, edges, "lib", "MAP.md")
    finally:
        os.chdir(cwd)
    N = {n["id"]: n for n in s["nodes"]}
    assert "lib/errors.py:BadShape" not in N and "lib/shapes.py:Area" not in N     # an exception, a value class: no box
    assert {n["id"]: n["parent"] for n in bare["nodes"]} == {n["id"]: n["parent"] for n in s["nodes"]
                                                           if not n["id"].startswith("resource:")}   # the map moves nothing
    assert N["lib/report/report.py:Report"]["concepts"] == ["Geometry", "Output"]
    assert "concept: carries Geometry, Output" in N["lib/report/report.py:Report"]["flag"]
    assert "concept: Geometry is spread over" in N["lib/shapes.py:Shape"]["flag"]
    assert s["unmapped"] == ["Ghost"]
    writes = [e for e in s["edges"] if e["to"] == "resource:out/"]
    assert N["resource:out/"]["note"].startswith("no single owner: written by")   # the map's declared owner is ignored
    assert writes and all(e["flag"].startswith("shared: written by") for e in writes)


def test_a_class_no_one_keeps_nests_in_its_packages_class_and_a_write_counts_in_its_function(tmp_path):
    import trace_objects
    files = dict(REPR, **{"lib/report/table.py": "class Table:\n    def rows(self):\n        return []\n",
                          "lib/util.py": REPR["lib/util.py"].replace('    (Path(".") / "out").exists()\n',
                                                                     '    d = Path(".") / "out"\n    d.mkdir(exist_ok=True)\n')})
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (tmp_path / "MAP.md").write_text(MAP)
    cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        fns, objs = trace_objects.objects("lib")
        s = trace_objects.spec_of(objs, [{"from": "lib/util.py", "to": "lib/report/table.py:Table", "members": ["rows"]}],
                                  "lib", "MAP.md")
    finally:
        os.chdir(cwd)
    N = {n["id"]: n for n in s["nodes"]}
    assert N["lib/report/table.py:Table"]["parent"] == "lib/report/report.py:Report"   # report/ names Report
    assert N["resource:out/"]["note"].startswith("no single owner")    # util's write is two lines below the path
