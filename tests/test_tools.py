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
    "lib/util.py": '''
def fmt(x):
    return str(x)
''',
    "lib/other.py": '''
from lib import util


def line():
    return util.fmt(2)
''',
    "lib/use.py": '''
from lib.shapes import Square
from lib import util


class Report:
    def show(self):
        return util.fmt(Square().area().value)


def top():
    return util.fmt(1)
''',
}
MAP = "| Concept | Description | Aliases | Objects | Concerns |\n|---|---|---|---|---|\n| Shape | a shape | - | `Shape` | geometry |\n| Report | output | - | `Report` | output |\n"


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
        edges = [{"from": "lib/use.py:Report", "to": "lib/shapes.py:Shape", "members": ["area"]},
                 {"from": "lib/use.py:Report", "to": "lib/util.py", "members": ["fmt"]},
                 {"from": "lib/other.py", "to": "lib/util.py", "members": ["fmt"]},
                 {"from": "lib/shapes.py:Shape", "to": "lib/shapes.py", "members": ["_square"]}]
        s = trace_objects.spec_of(objs, edges, "lib", "MAP.md")
    finally:
        os.chdir(cwd)
    N = {n["id"]: n for n in s["nodes"]}
    assert N["lib/shapes.py:Shape"]["kind"] == "object" and "lib/shapes.py:Square" not in N
    assert "lib/shapes.py" not in N                          # its helper folded into Shape
    assert N["lib/util.py"]["kind"] == "external"            # two files use it: not encapsulated
    assert N["lib/other.py"]["kind"] == "external"           # functions no class encapsulates
    assert ("lib/use.py:Report", "lib/shapes.py:Shape") in {(e["from"], e["to"]) for e in s["edges"]}
    assert not [n for n in s["nodes"] if n["kind"] == "folder"]
    assert [m["into"] for m in s["moves"] if m["module"] == "lib/util.py"] == ["lib/use.py:Report"]


def test_build_nudges_a_kept_box_a_grown_neighbour_now_covers():
    spec = {"name": "g", "nodes": [{"id": "big", "name": "Big"}, {"id": "p1", "name": "Part one", "parent": "big"},
                                   {"id": "p2", "name": "Part two", "parent": "big"}, {"id": "near", "name": "Near"}],
            "edges": []}
    keep = {"nodes": [{"id": "big", "x": 0, "y": 0}, {"id": "near", "x": 0, "y": 60}]}
    d = board.build(spec, keep, None)
    assert board.overlaps(d, "near") == [] and board.overlaps(d, "big") == []
    pos = {n["id"]: (n["x"], n["y"]) for n in d["nodes"]}
    assert pos["big"] == (0, 0)
