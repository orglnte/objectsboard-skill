# objectsboard

A Claude Code skill that translates a codebase's concepts into its actual
objects. It takes a concept map (what the software is for), reviews one
object at a time (its whole interface, every outside caller, everything that
reaches its files without it), draws the current state on a live board and
lets you drag it into the target before the code changes.

It sits after concept design: write the concept map yourself, or produce it
with a concept-design skill such as
[concept-skills](https://github.com/ontology-of-everything/concept-skills),
then use objectsboard to make the objects match it.

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
3. `tools/extract_interface.py`: a class's whole interface and every outside
   use, as JSON (Python codebases).
4. `tools/file_contracts.py`: every mention of given file names, to find
   what goes around an object through its files.
5. `tools/build_audit.py` + `tools/interface-audit.template.html`: the audit
   page (the class as pseudo code, each caller and why it calls).
6. `tools/objects-board.html`: the board, published as a claude.ai artifact
   with a shared database (`capabilities: {db: {}}`).
