---
name: objectsboard
description: Separation of concerns, encapsulation and abstraction — draw a codebase's objects as the code has them, optionally overlay its concepts, and review its architecture for patterns, antipatterns and dark concepts, interactively through an Objects Board. Pick one object, extract its whole interface and every outside use, find what goes around it through files, draw the current state on a live Objects Board (a claude.ai artifact), and refactor round by round: the user decides each next step on the board, the code changes, the board is redrawn from the code. Use when the user asks about concepts, separation of concerns, encapsulation, abstraction, who owns a file or a piece of state, an architectural review, or a change would move responsibility between classes or modules.
---

# Objectsboard

This skill draws a codebase's objects as the code has them, shows what
goes around each owner, and supports the design round by round: the user
picks the next change, the code changes, the board is redrawn from the code.
Encapsulation, abstraction, separation of concerns and ownership are
design, and the user makes the design.

A concept map is optional. Concepts say what the software is for; laid over
the objects they inform the design, but a concept does not translate into a
class by itself. Without a map the board shows the objects, the data and
the arrows; with one it also labels the boxes and flags concept mismatches.

**Python only for now:** the extractor parses Python source. The method and
the board work for any language, but the interface and its uses would have
to be gathered by hand.

It covers:

1. **Encapsulation:** an object's state and files reached only through it.
2. **Abstraction:** an interface sized to what its callers use; public
   members nobody uses outside, and private ones reached from outside, are
   findings.
3. **Separation of concerns:** with a concept map, one concept per object;
   a concept spread over several objects, or an object carrying several
   concepts, is a finding.
4. **Architectural review:** patterns and antipatterns found in the code,
   and, with a concept map, dark concepts (a concept whose effect surprises
   its user, or that acts silently) and colliding names.

## Requirements

1. Python 3.9+ and its standard library; nothing to install.
2. `tools/trace_uses.py` runs the project's test suite in its own process,
   so run it with the project's own interpreter, from the project root, e.g.
   `.venv/bin/python <skill>/tools/trace_uses.py --target ... -- tests -q`:
   that interpreter needs pytest and the project's dependencies. Caller
   names are fully qualified on Python 3.11+.
3. Optional: Graphviz (`dot` on the PATH) for `board.py build --layout`;
   without it the layout starts from a grid and crosses more arrows.

## The whole codebase: the objects representation (Python)

When the user wants to see everything, draw the objects representation of
the code **as it is**: `tools/trace_objects.py --spec ...`, with
`--data <Resources table>` or `--concepts <concept map>` when there is one.
Boxes are the classes with behaviour of their own (a public
method; not a value class such as a dataclass, NamedTuple or Enum, and not
an exception), one arrow per pair of boxes that call each other, modules no
class holds as external boxes, and the **resources (data)**: the files,
folders and external systems the code keeps data in or drives, named with
the code patterns that reach them in a Resources table (Resource | Kind |
Owner | Reached by), in its own file (`--data`) or in the concept map.
Without one, the board has no data boxes. Each box whose code reaches a resource gets an
arrow to it.

**Concepts do not decide the board.** When there is a concept map,
concepts say what the software is for; encapsulation, abstraction, separation of concerns and ownership are
design, and the person makes the design. A concept does not translate into
a class by itself. On the board the concept map is an **overlay**: each box
is labelled with the concepts whose Objects cell names it, and mismatches
are flagged (a concept spread over several owners, a box carrying several
concepts; a concept naming nothing in the code is listed). They inform the
design; they never move a box. The Resources table's Owner column is
documentation, not an input.

**Placement comes from the code's structure, never from usage or concepts**
(the first rule that matches wins):

1. The file holds a box class: its functions and helper classes belong to
   it (the class named after the file, or after the package for an
   `__init__.py`); another box class in the file nests inside it.
2. A box class created and kept by exactly one other nests in it
   (`self.x = Other(...)`, or `self.x = make(...)` where the project
   function `make` returns `Other(...)`); a subclass folds into its base.
3. The package names its class: any other module, and a box class no one
   class keeps, belongs to the class named after its folder (`cell/` ->
   Cell), or the nearest enclosing folder's, never the root's. A package
   with no class holds its modules in its own `__init__` module, when that
   has code of its own.
4. Everything else is external.

**Data's owner is its only writer in the code** (a write anywhere in the
function that reaches it, or through the name the path is kept in). Several
writers: no single owner, each write flagged as shared. A helper that
returns a data path (`return self.root / "out"`) passes the reach to its
callers: a caller's write through it counts as the caller's, and a path the
owner's own public helper hands out is reached through the owner.

**Processes:** a module the code starts as its own Python process (an argv
with `-m pkg.mod`, or an interpreter and the file itself) is a process box,
with a "starts it" arrow from the code that builds the command line.

**The criterion:** one owner per part and per datum; everything else goes
through the owner (information hiding, the aggregate root, the Law of
Demeter; see METHOD.md). What the owner makes public is its interface: its
public members, the parts it hands out (`self.door = Door()`, `def
window(self)`, a method named after the part's class), the objects its
public methods return, and the names its module or package re-exports from
a part (`from ._impl import go`).

**Arrows:** all legitimate calls and accesses between two owners are one
blue arrow between the owners themselves, whichever of their parts make or
receive them. The others keep their own arrow, from the exact part that
makes it, coloured by the table in METHOD.md (Arrow colours):

1. **Red, private access:** a name on the path starts with an underscore
   and the caller is outside its Python container (its module, its class
   or a subclass, the package of a private module).
2. **Amber, owner bypass:** a public path into a part its owner does not
   hand out; **amber, shared:** data with several writers, or another
   owner's data reached directly. Each amber arrow is a decision for the
   user: route it through the owner, have the owner hand it out, or make
   the part private.

`trace_objects.py --worklist FILE` lists them, red first, the most reached
part first, each with its call sites (file:line). A flagged box is a part
called directly from outside its owner, or a module its owner never uses
while exactly one other class does (a doubt). Concept findings, with a
concept map, are flagged in place. Never draw one box around everything,
folder boxes, or the module import graph as the whole-codebase board.

**The design is iterative, not one step to a target.** The board is always
the current code. Each round the person picks the next change, informed by
the worklist (and the concept findings, with a map); the code changes in one commit,
suites green; the board is traced and redrawn from the code; the next round
starts from there.

## Input

The code and its test suite. Optional:

1. A Resources table (Resource | Kind | Owner | Reached by): the data the
   code keeps in files, folders and external systems, and the code patterns
   that reach each one (`--data`).
2. A concept map: one row per concept with a short description and the
   objects it should rest on (`docs/CONCEPTS-<repo>.md`: concept |
   description | aliases | objects | concerns), its Resources table
   included, written by hand or produced with a concept-design skill such
   as [concept-skills](https://github.com/ontology-of-everything/concept-skills)
   (`--concepts`).

Without either, start from the object the user's question is about.

## Steps

0. Ask once whether the repo has a concept map or a Resources table (look
   for `docs/CONCEPTS-*.md` first). If not, offer three ways to start: a
   Resources table only, so the board has data findings; a concept map,
   written by hand or with a concept-design skill, for the concept overlay;
   or neither. Never block on it.
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
   {}, downloads: true}` (or open it anywhere and Import the `board.py
   build` JSON: away from claude.ai it keeps diagrams in the browser). A board is per repo: one artifact per repo, its `<title>` and
   heading `<repo> Objects Board` (e.g. `shop Objects Board`), every diagram
   of that repo inside it; another repo gets its own board. Write the diagram as a spec where every arrow carries its proof (a
   file and a pattern), run `tools/board.py build` (it refuses an unproved
   arrow and keeps the user's positions; a first draw or a redraw uses
   `--layout`, laid out for the fewest arrows over a box, then crossings),
   seed the result, and
   let the user duplicate it and drag the next change into it. No
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
