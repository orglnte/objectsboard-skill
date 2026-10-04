---
name: objectsboard
description: Concepts, separation of concerns, encapsulation and abstraction — translate a codebase's concepts into its actual objects and review its architecture for patterns, antipatterns and dark concepts. Pick one object, extract its whole interface and every outside use, find what goes around it through files, draw the current state on a live Objects Board (a claude.ai artifact) and let the user drag it into the target encapsulation before any code changes. Use when the user asks about concepts, separation of concerns, encapsulation, abstraction, who owns a file or a piece of state, an architectural review, or a change would move responsibility between classes or modules.
---

# Objectsboard

Concepts say what the software is for; objects are the code that delivers
them. This skill checks, one object at a time, that each concept's state and
files are owned by the objects meant to own them, shows the gap on a board,
and lets the user draw the target encapsulation.

**Python only for now:** the extractor parses Python source. The method and
the board work for any language, but the interface and its uses would have
to be gathered by hand.

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

## Requirements

1. Python 3.9+ and its standard library; nothing to install.
2. `tools/trace_uses.py` runs the project's test suite in its own process,
   so run it with the project's own interpreter, from the project root, e.g.
   `.venv/bin/python <skill>/tools/trace_uses.py --target ... -- tests -q`:
   that interpreter needs pytest and the project's dependencies. Caller
   names are fully qualified on Python 3.11+.

## The whole codebase: the objects representation (Python)

When the user wants to see everything, draw the objects representation from
`tools/trace_objects.py --concepts <concept map> --spec ...` (the test suite's
run plus the source):

1. **Boxes are the classes that encapsulate a concept:** the classes the
   concept map's Objects column names.
2. **One arrow per pair of boxes that call each other**, labelled with the
   members called.
3. **A module not encapsulated by a class is an external box.**
4. **What folds where:**
   1. a subclass of a concept class folds into its base's box (listed as a
      member);
   2. a helper class folds into the concept class of its file;
   3. a module's functions fold into the concept class of their file when
      that class is their only caller at run time;
   4. a value class (a dataclass, NamedTuple, Enum, or a class with no public
      method) goes inside the one box whose code constructs it;
   5. another module goes inside a concept class only when that class is its
      only caller both statically (only its file imports the module) and at
      run time.
5. **Never:** one box around everything, folder boxes, or the module import
   graph as the whole-codebase board.
6. **Moves:** an external module whose only concept-class user is one class
   does not belong outside it. `trace_objects.py` lists these moves; carry
   each out as a refactor (its other users then reach it through the class),
   one move per commit with the suites green, and redraw the board.

## Input

A concept map: one row per concept with a short description and the objects
it should rest on. Write one (`docs/CONCEPTS-<repo>.md`: concept | description
| aliases | objects | concerns), or produce it with a concept-design skill such
as [concept-skills](https://github.com/ontology-of-everything/concept-skills).
Without a map, start from the object the user's question is about.

## Steps

1. Pick ONE object: the one the question is about, or the largest class or
   module on its path. In Python everything is an object: a module, a class
   or a function can be the target (`path/mod.py`, `path/mod.py:Name`). Never
   a package from an import graph alone.
2. `tools/extract_interface.py --target`: its whole interface and every outside use
   (add each factory, stand-in, parameter name and attribute or property the
   object travels under: `--factory`, `--stand-in`, `--param-name`,
   `--attr`).
3. `tools/trace_uses.py --target`: the run-time cross-check and the call
   graph into the object, from running the test suite. It lists the uses the
   static scan missed and the ones no test runs. It needs tests with good
   coverage of the code that uses the object.
4. Read every caller; write one sentence per call site on why it calls.
5. `tools/file_contracts.py`: everything that reads or writes the object's
   files without going through it.
6. Report the findings as checkable facts with file and line; stop and let
   the user decide.
7. Publish `tools/objects-board.html` as an artifact with `capabilities: {db:
   {}}`. Write the diagram as a spec where every arrow carries its proof (a
   file and a pattern), run `tools/board.py build` (it refuses an unproved
   arrow and keeps the user's positions), seed the result, and
   let the user duplicate and drag it into the target encapsulation. No
   arrows from a container to its own parts: nesting is the ownership. Offer
   both arrow styles and let the user pick: **detailed**, one arrow per call
   or file access, labelled with it; **collapsed** (`board.py collapse`),
   one arrow per pair of boxes with the labels merged, to see who depends on
   whom. Read their diagram back before proposing code.
8. Refactor toward the target one owner at a time, suites green per commit,
   and redraw the board after each step.

What each tool cannot see is listed in [METHOD.md](METHOD.md#what-the-tools-cannot-see):
say so in the findings. The method, the tools, the views and the pitfalls
are there too. Read it before the first run.
