import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
import board  # noqa: E402

APP = {
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
    subprocess.run([sys.executable, str(TOOLS / "extract_interface.py"), "--class", "app/store.py:Store",
                    "--scan", "app=app", *extra, "--out", str(out)], cwd=app, check=True, capture_output=True)
    return json.loads(out.read_text())


def test_without_attr_a_property_hides_every_use(app):
    assert extract(app)["uses"] == []


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
