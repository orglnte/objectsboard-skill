---
name: objectsboard
description: Concepts, separation of concerns, encapsulation and abstraction — translate a codebase's concepts into its actual objects and review its architecture for patterns, antipatterns and dark concepts. Pick one object, extract its whole interface and every outside use, find what goes around it through files, draw the current state on a live Objects Board (a claude.ai artifact) and let the user drag it into the target encapsulation before any code changes. Use when the user asks about concepts, separation of concerns, encapsulation, abstraction, who owns a file or a piece of state, an architectural review, or a change would move responsibility between classes or modules.
---

# Objectsboard

Concepts say what the software is for; objects are the code that delivers
them. This skill checks, one object at a time, that each concept's state and
files are owned by the objects meant to own them, shows the gap on a board,
and lets the user draw the target encapsulation.

It covers:

1. **Concepts:** what each unit of function is for, its aliases and the
   objects it rests on.
2. **Separation of concerns:** one concept per object; a concept spread over
   several objects, or an object carrying several concepts, is a finding.
3. **Encapsulation:** an object's state and files reached only through it.
4. **Abstraction:** an interface sized to what its callers use; public
   members nobody uses outside, and private ones reached from outside, are
   findings.
5. **Architectural review:** patterns and antipatterns found in the code,
   and dark concepts (a concept whose effect surprises its user, or that
   acts silently) and colliding names.

## Input

A concept map: one row per concept with a short description and the objects
it should rest on. Write one (`docs/CONCEPTS-<repo>.md`: concept | description
| aliases | objects | concerns), or produce it with a concept-design skill such
as [concept-skills](https://github.com/ontology-of-everything/concept-skills).
Without a map, start from the object the user's question is about.

## Steps

1. Pick ONE object: the one the question is about, or the largest class on
   its path. Never a package from an import graph alone.
2. `tools/extract_interface.py`: its whole interface and every outside use
   (add each factory, stand-in and parameter name the object travels under).
3. Read every caller; write one sentence per call site on why it calls.
4. `tools/file_contracts.py`: everything that reads or writes the object's
   files without going through it.
5. Report the findings as checkable facts with file and line; stop and let
   the user decide.
6. Publish `tools/objects-board.html` as an artifact with `capabilities: {db:
   {}}`, seed the current state (every arrow proved against the code), and
   let the user duplicate and drag it into the target encapsulation. Read
   their diagram back before proposing code.
7. Refactor toward the target one owner at a time, suites green per commit,
   and redraw the board after each step.

The method, the tools, the views and the pitfalls are in
[METHOD.md](METHOD.md). Read it before the first run.
