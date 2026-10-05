# objectsboard

A live board of Python objects for interactive architecture refactoring
with coding agents. This Claude Code skill draws a codebase's objects as the
code has them, colours every call or data access that goes around an owner
(red: private access; amber: owner bypass or shared data), and supports the
refactoring one owner at a time: you decide each move on the board, the
agent changes the code, the board is redrawn from it.

It (optionally) integrates with concept design: write the concept map
yourself, or produce it with a concept-design skill such as
[concept-skills](https://github.com/ontology-of-everything/concept-skills),
then use objectsboard to make the objects match it.

Python only for now: the tools parse Python source and trace the test suite.
The method and the board apply to any language.

What each tool cannot see is listed in `METHOD.md`, "What the tools cannot
see".

## Requirements

1. Python 3.9+ and its standard library; nothing to install.
2. `tools/trace_uses.py` imports the project and runs its tests, so run it
   with the project's own interpreter from the project root (for example
   `.venv/bin/python ~/code/objectsboard-skill/tools/trace_uses.py ...`);
   that interpreter needs pytest and the project's dependencies; so does
   `tools/trace_objects.py`.
3. Optional: Graphviz (`dot`) for `tools/board.py build --layout`.

## Install

```sh
git clone https://github.com/orglnte/objectsboard-skill ~/code/objectsboard-skill
ln -s ~/code/objectsboard-skill ~/.claude/skills/objectsboard
```

Then `/objectsboard` in Claude Code.

## Contents

1. `SKILL.md`: when to use it and the steps.
2. `METHOD.md`: the method, the tools, keeping the diagrams true, the views,
   the pitfalls.
3. `tools/extract_interface.py`: an object's whole interface and every
   outside use, as JSON, for a module, a class or a function; for classes it
   follows factories, parameters, stand-ins and attributes or properties that
   hold an instance.
4. `tools/trace_objects.py`: the objects representation of a whole
   codebase as the code has it, from the test suite's run and the source:
   its classes with behaviour, one arrow per pair of owners, red and amber
   arrows for what goes around an owner, the data from a Resources table
   (`--data`) and, optionally, a concept map laid over the boxes
   (`--concepts`). `--worklist` lists the red and amber arrows as the
   refactoring order, with their call sites.
5. `tools/trace_uses.py`: the run-time cross-check and the call graph into
   the object, recorded while the test suite runs (needs good test coverage;
   Python 3.11+).
6. `tools/file_contracts.py`: every mention of given file names, to find
   what goes around an object through its files.
7. `tools/build_audit.py` + `tools/interface-audit.template.html`: the audit
   page (the class as pseudo code, each caller and why it calls).
8. `tools/board.py`: checks every arrow's proof against the code, lays a
   diagram out keeping the board's existing positions or, with `--layout`,
   from scratch for the fewest arrows over a box, then crossings, refuses an arrow into
   a box's own parts, collapses arrows to one per pair of boxes, and makes the
   simplified view.
9. `tools/objects-board.html`: the board, published as a claude.ai artifact
   with a shared database (`capabilities: {db: {}, downloads: true}`). It
   also runs on its own, from any static host or a local file: diagrams
   then stay in that browser, and Import loads a `board.py build` JSON
   (Export saves one).
10. `tests/`: `python3 -m pytest tests -q`.

## License and citation

Apache-2.0: see [LICENSE](LICENSE). Redistributions carry [NOTICE](NOTICE).
To cite or credit the skill and its method, use
[CITATION.cff](CITATION.cff) (GitHub's "Cite this repository").
