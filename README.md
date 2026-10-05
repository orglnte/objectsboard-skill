# objectsboard

A Claude Code skill for separation of concerns, encapsulation and
abstraction: it draws a codebase's objects as the code has them, lays its
concepts over them, and reviews the architecture for patterns, antipatterns
and dark concepts, interactively, through an Objects Board.

It (optionally) sits after concept design: write the concept map yourself,
or produce it with a concept-design skill such as
[concept-skills](https://github.com/ontology-of-everything/concept-skills),
then use objectsboard to make the objects match it.

Python only for now: the extractor parses Python source. The method and the
board apply to any language.

What each tool cannot see is listed in `METHOD.md`, "What the tools cannot
see".

## Requirements

1. Python 3.9+ and its standard library; nothing to install.
2. `tools/trace_uses.py` imports the project and runs its tests, so run it
   with the project's own interpreter from the project root (for example
   `.venv/bin/python ~/code/objectsboard-skill/tools/trace_uses.py ...`);
   that interpreter needs pytest and the project's dependencies.

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
   codebase as the code has it: its classes with behaviour, one arrow per
   pair that call each other, modules no class holds as external boxes,
   the concepts as an overlay; from the test suite's run and the source. `--worklist` ranks the bypasses
   into the refactoring order, with their call sites.
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
   with a shared database (`capabilities: {db: {}}`).
10. `tests/`: `python3 -m pytest tests -q`.
