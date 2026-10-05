# Objectsboard method

For the whole codebase, the objects as the code has them, with what goes
around each owner coloured, and a concept map laid over them when there is
one; for one object, what it really offers, who reaches it and why, and what
goes around it; then a board where the user decides the next change, one
owner at a time. The tools are in `tools/`.

## Concepts and objects

A concept map is optional; without one, skip this section.

1. The concept map (concept | description | aliases | objects | concerns)
   says what the software is for. It does not decide encapsulation,
   abstraction, separation of concerns or ownership: those are design, made
   by the user. A concept does not become a class by itself.
2. The board draws the code as it is; the concepts are an overlay on it
   (which boxes carry which concept, a concept spread over several owners,
   a box carrying several) that informs the design and moves nothing.
3. The design is iterative: each round the user picks the next change from
   the worklist and the concept findings, the code changes in one commit,
   the board is redrawn from the code.
4. Concepts whose effect surprises their user (dark concepts) and names that
   collide (one name, two meanings; two names, one meaning) are listed next
   to the map, in a TODO file: they are the first candidates to fix.

## The criterion: one owner per part and per datum

Every part (a module, a helper, a nested class) and every datum (a file, a
folder, an external system's state) has exactly one owner, and everything
else reaches it only through that owner. A class with its parts and its
data is one unit; the class is its root.

On the board, all legitimate traffic between two owners is one arrow between
the owners themselves; each red or amber arrow is its own, from the exact
part, since each is one fix.

### Arrow colours

The terms:

1. **Owner:** a box, a class with behaviour of its own, placed by the
   code's structure (its file, composition, its package's folder).
2. **The owner's interface:** its public attributes, methods and
   properties; the parts it hands out (a public attribute that holds one,
   a public method or property that returns one or is named after it); the
   objects its public methods return (`shop.sales()` returning `Sale`s); the
   names its own module or its package's `__init__.py` imports from a part
   (`from ._stock import count`: what is in the owner's namespace is
   public); and a function the caller was handed (a callback its code does
   not name).
3. **Public path:** no name from the caller's import or reference to the
   member starts with an underscore (dunders such as `__init__` are public).
4. **Python container of a private name:** the module that defines it, the
   class (and its subclasses) for a method or a `self._x`, the package for
   a private module (`pkg/_impl.py`).

| Colour | When | Example |
|---|---|---|
| Red, *private access* | a name on the path starts with an underscore and the caller is outside its Python container | `shop._audit()`, `from shop._stock import count` from outside `shop/` |
| Amber, *owner bypass* | the path is public, but it reaches a part its owner does not hand out | `shop.ledger.reset()` when Shop does not hand out its ledger |
| Amber, *shared* | a datum written by more than one owner, or another owner's data reached directly | two classes writing the same `out/` folder |
| Blue | the caller goes through the owner's interface, or uses a part the owner handed out | `shop.sales()` then `sale.total()`; `shop.count()` re-exported by Shop's package |

Red is the code breaking its own visibility rules. Amber is a design
decision left open: route the call through the owner, have the owner hand
the part out (blue), or hide the part behind an underscore (red until its
callers move). The code decides what is public; the board shows where its
structure and its visibility disagree.

A private name is matched by name: an attribute counts only when exactly
one project class or module defines it, and a private method seen at run
time only when the caller's code names it (a private method handed over as
a callback is not red). Constant reads are not traced, so a module imported
only for a constant shows as a flagged box, not as an arrow. A pair of boxes
the run never saw call each other, where one names a class or function of
the other in its source, gets a dotted "uses" arrow (static only).

It is three established rules seen together:

1. **Information hiding:** an owner hides its design decisions (its data's
   layout, its helpers) behind its interface, so they can change without
   breaking callers. An arrow into a part or a resource depends on what
   should be hidden.
2. **The aggregate root** (Domain-Driven Design): a cluster of objects is
   one unit, outside code holds a reference to the root only, and every
   change goes through it. An amber arrow is an aggregate-boundary violation.
3. **The Law of Demeter:** code talks to its immediate collaborators, not
   through them to their internals. Red and amber arrows are its violations
   at module level.

What this skill adds: placement comes from the code's structure, never
from usage or concepts, so the boundary is declared, not inferred from who
happens to call what; usage only marks the crossings. Where the structure
and the usage name different owners (an owner that never uses its module),
the box is a doubt for the user to settle.

## Why one object at a time

Seeing is the whole codebase at once: the board and its worklist show every
owner and every arrow around one. Deciding and changing go one owner at a
time. An import graph shows modules and imports only; a refactor planned
from it reshuffles packages without fixing anything a user or a test can
see. Object design has more than one level (an object, what it owns, what it
exposes, who calls it, which files stand in for it), and a review that starts
from a single owner sees all of them. Each move is one commit with the suites
green, and the board is redrawn before the next owner is decided.

## Method

1. **Pick one object.** The one the question is about, or the largest class
   on the path the question touches. Not a package, not the whole system.
2. **Extract its whole interface and every outside use.**
   `extract_interface.py --target mod.py[:Name] --scan tag=dir ... --out data.json`.
   It lists every member (methods, properties, constants, `self.x`
   attributes), grouped by the class's own `# --- name ---` sections, with
   reads inside the class, by production code outside, and by tests.
   1. Add `--factory f` for every function that returns an instance.
   2. Add `--stand-in X` for any class that imitates a slice of the object.
   3. Then look for where the object passes itself out (`f(self)`): the
      receiving function's parameter must be covered by `--param-name`.
3. **Read every caller and write why it calls.** One sentence per call site,
   taken from the code, not guessed from the name. Group callers by how they
   reach the object:
   1. **process:** they start a process that builds the object;
   2. **object:** they construct it and call it;
   3. **stand-in:** they build a fake that holds part of its data;
   4. **constant:** they only read a class constant.
4. **Find what goes around the object through files.**
   `file_contracts.py --name <file> ... --root tag=dir`. A member reported
   "unused outside" can hide a concept used everywhere through its file,
   with writers, readers and parsers of its format outside the object.
   Classify each mention as a
   read or a write from its context before drawing it.
5. **State the findings as checkable facts**, each with its file and line.
   The shapes that recur:
   1. public members nothing outside uses;
   2. private members reached from outside;
   3. construction finished from outside (the constructor takes too little);
   4. constants or formats copied instead of imported (exit codes, markers);
   5. the object's lifecycle restated by a caller (prepare, setup, verify,
      teardown called by hand);
   6. a stand-in class, which means callers depend on a slice of data, not on
      the object;
   7. tests reaching private members;
   8. an expensive constructor that pushes callers to re-read its files.
6. **Publish the audit page.** Write `annotations.json` (title, repo heads,
   callers with their reasons, facts, routes, who constructs it), then
   `build_audit.py data.json annotations.json out.html`, and publish it as an
   artifact. The page opens with the class as pseudo code, public then
   non-public, each line naming who reaches it from outside.
7. **Draw the current state in the Objects Board, then let the user draw the
   target.** Seed `objects-board.html`'s store with the object, everything it
   owns, every component that calls it or touches its files, and one arrow
   per verified call or file access. The user duplicates the diagram and
   drags it into the target design; read their diagram back before
   proposing code.
8. **Refactor toward the target, one owner at a time.** Each step a commit
   with the suites green; redraw the board after each step, so it never
   shows a state the code has left.

## The tools

1. **`extract_interface.py`**: the interface and the outside uses as JSON,
   for a module, a class or a function (`--target`)
   (`cls, file, span, classdoc, init_sig, sections, members, uses`).
   Static analysis of Python source: an instance reached through a name it
   does not track is missed (follow factories with `--factory` and
   attributes or properties with `--attr`), so say so on the page.
2. **`file_contracts.py`**: every quoted mention of the given file names,
   with two lines of context.
3. **`trace_objects.py`**: the objects representation of a whole codebase
   (view 6) from the test suite's run and the source: `--data` names a
   Resources table (the data and the code patterns that reach it);
   `--concepts`, optional, names a concept map laid over the boxes, whose
   own Resources table serves when `--data` is not given; `--spec`
   writes the board spec; `--exclude DIR` (repeatable, or `exclude` in
   `.objectsboard.json`) leaves a folder out of every scan. An arrow the caller's file does not name (a
   subclass, a callback, an injected function) is marked "[run time only]"
   with the observation as proof. A pair the run never saw, where the
   source names a class or function of the other box (followed through
   imports and re-exports; an unused import is no use), is a "uses" arrow,
   static only. Every box is drawn; one no call between project objects
   reached at run time is marked UNOBSERVED. With `--group-unowned`,
   top-level modules no class owns go in the unowned group (box kind
   `unowned`, titled with the deepest folder they share): drawn together,
   never an owner; findings, the worklist and the simplified
   view keep each member as a box of its own. With `--group-resources`,
   the resources go in one box of kind `resources`, the same way: drawn
   together, never an owner, each resource keeping its own owner or
   writers. `--worklist FILE` writes the refactoring
   worklist: private access (red) first, then owner bypasses and shared
   data (amber); one entry per part, the most reached first, with every
   arrow into it and its call sites (file:line, read from the code's
   imports, names and attributes).
4. **`trace_uses.py`**: runs the test suite (pytest, in process, under the
   project's own interpreter) with a profiler and records every call into the target's
   functions and the caller outside it: the call graph into the target.
   `--diff data.json` lists, per file, the members seen only at run time
   (missed by the static scan) and those no test runs.
5. **`board.py`**: `check` refuses any arrow whose proof (a file and a
   regex) does not match; `build` lays the diagram out, keeping the
   positions of every box the board already has and placing new ones next
   to their main neighbour without overlap, and stamps the revision;
   `build --layout` ignores kept positions and lays the diagram out from
   scratch (each box's parts first: the parts no arrow leaves the box from
   in a grid in the middle, the parts arrows leave from on the border
   facing those arrows, same colours together, every part stretched so
   rows and columns line up; the top level by Graphviz `dot` when
   installed, and a grid, the compact candidate with the fewest arrows
   over a box, then crossings, kept; then top-level boxes swapped and
   insides mirrored while that lowers them; deterministic); `crossings`
   counts the arrows that cross and the arrows over a box;
   `collapse` merges the arrows per pair of boxes; `simplify` makes the
   simplified view. `check` also refuses an arrow
   between a box and a box nested in it.
6. **`build_audit.py` + `interface-audit.template.html`**: the audit page.
   Sections: the pseudo-code class; summary counts; findings; ways in; one
   card per caller (route, members used, why, finding); the full member
   table with filters (only used outside, only public unused outside, count
   tests). Clicking a member shows its docstring and every outside site.
7. **`objects-board.html`**: a diagram editor, published as an artifact with
   `capabilities: {db: {}, downloads: true}`. One board per repo, named
   `<repo> Objects Board` in its `<title>` and heading; the repo's diagrams
   live in it. Away from claude.ai it runs alone, keeping diagrams in the
   browser; Import loads a diagram from JSON (a `board.py build` output; one
   of the same name is replaced) and Export saves the current one.
   1. **Boxes** have a name, a kind (object, module, process, file, folder,
      external), an origin (library, or app: code built on
      it), members (`+` public,
      `-` private, or plain text) and a note. Double-click a box to hide or
      show its members.
   2. **Ownership is nesting.** Drop a box inside another to make it owned;
      dragging a box detaches it, so dropping it on empty canvas frees it.
      The inspector's "Owned by" does the same without dragging.
   3. **Arrows** are typed (calls, reads, writes, spawns) and labelled with
      the call or the file use. "Connect", then click the source and the
      target. Select an arrow to change its kind or label, reverse it or
      delete it.
   4. **Reading a dense diagram:** the kind chips hide one kind of arrow;
      "All arrows" switches to showing only the selected box's arrows;
      "Labels" toggles arrow labels; a selected box highlights its arrows.
   5. **Several diagrams per board** (New, Duplicate), stored as documents
      in the collection `diagrams`:
      `{name, nodes: [{id, name, kind, origin, x, y, members: [{vis, name}],
      note, parent, collapsed}], edges: [{id, from, to, kind, label}]}`.
      Seed with `ArtifactData set` after publishing; read the user's version
      with `ArtifactData get`. A node's `w`/`h`, when present, is a minimum
      size: the box still grows to fit its members and children.
   6. **Editing:** Undo/Redo buttons, Ctrl/⌘ Z and Ctrl/⌘ Shift Z or Y; the
      history resets when another diagram is picked or a remote change
      arrives. Every box has a resize handle at its bottom-right corner
      (always shown on containers and dashed boxes, on hover otherwise).

## What the tools cannot see

Say which of these apply when reporting findings: an absence the tools
cannot see is not evidence that nothing is there.

1. **Static scan (`extract_interface.py`), class targets:** an instance
   reached through a name it does not follow (a function returning one that
   is not given as `--factory`, a parameter under another name, an attribute
   or property not given as `--attr`); loop and unpacking variables;
   module-level variables; instances in lists, dicts, callbacks or
   `**kwargs`; `getattr` with a computed name; uses through a subclass.
2. **Static scan, module and function targets:** `importlib` or
   `__import__` with a computed name; a module-level `__getattr__`; names
   re-exported through another module (target the re-exporting module too).
3. **Run-time traces (`trace_uses.py`, `trace_objects.py`):** code the tests never run, so the
   call graph is only as complete as the suite's coverage of the code that
   uses the target (`trace_objects.py` falls back to the source for a pair
   the run missed: a "uses" arrow, and an UNOBSERVED box); reads of plain
   attributes, constants and data objects (no function runs); calls inside another process (the tracer draws a
   module started as its own process as a process box, from its command
   line, but not the calls it makes). A data-path helper is followed by its
   name only when one function in the project has that name; a generic name
   (`path`, `get`, `open`, …) is never followed.
4. **File scan (`file_contracts.py`):** a file name built at run time (only
   quoted names are matched).
5. **Everything:** Python only. Other languages need the interface and its
   uses gathered by hand; the method and the board still apply.

## Keeping the diagrams true

1. **Write against the version read.** Pin every write with `if_version`;
   on a mismatch re-read and redo the change on the newer version, never
   overwrite it. If writes keep failing, ask the operator to pause. The page
   ignores a remote snapshot that arrives while the operator is dragging,
   saving or has unsaved changes.
2. **Prove every arrow against the code before writing it.** Each arrow
   carries its evidence, a file and a pattern that must match at HEAD;
   `board.py check` fails on a missing match. Stamp the
   diagram's name with the commit it was checked at, and check again after
   every change that moves a call.
3. **Run the extractor with every way the object is reached:** each
   factory and each parameter name the object is passed under. Diff the
   per-file member sets against the previous run; a difference with no code
   change behind it comes from the configuration, and a same-named member of
   another object is a false hit.
4. **Only current-state diagrams.** Delete a diagram once its state is
   gone; label a target design as a target, never mix it into a
   current-state diagram.
5. **Place a new box next to the box it connects to most**, without
   overlaps; keep the user's position for every box that survives. When the
   structure changed (boxes moved to another owner), redraw from scratch
   (`board.py build --layout`): kept positions then put boxes over their
   new owners' titles. The fewest cannot be guaranteed (finding them is
   NP-hard); `crossings` gives the number to compare layouts by.
6. **No arrows between an object and its own parts.** Nesting already
   states ownership; the arrows that matter cross the boundary.

## Views

1. **Full:** the object and everything it owns, every component that calls
   it or touches its files, one arrow per verified call or file access.
2. **Composed object box:** the public interface inside the object in three
   boxes by how many production files outside its package use each member
   (≥3, 1–2, none), each member suffixed `·N`.
3. **Detailed or collapsed arrows.** Any of these views can carry one
   arrow per call or file access (detailed: what exactly crosses the
   boundary) or one arrow per pair of boxes with the labels merged
   (collapsed, `board.py collapse`: who depends on whom). Offer both; a
   whole-codebase board starts collapsed, a single object's board detailed.
4. **Simplified:** one box per top-level owner, one arrow per pair with
   the labels merged (three, then `(+n)`). An arrow into the object's files
   that does not start at the object is a bypass.
5. **Interface:** the object's box lists its constructor and its 10–20 most
   used public members with their file counts, and a note saying so; each
   caller's box lists exactly the members it uses; each arrow reads
   `calls N members`. Callers nested by owner show which owner needs which
   slice.
6. **Whole codebase (the objects representation):** the code as it is.
   Classes with behaviour of their own as boxes, one arrow per pair,
   modules no class holds as module boxes of their own, and the resources (data)
   named in a Resources table (`--data`, or the concept map's), with an arrow from every box whose
   code reaches one directly (a bypass unless it is the data's only writer;
   several writers are flagged as shared). Placement from the code's
   structure only: the file's class, composition (a class kept as `self.x`,
   built directly or by a project factory function) and subclassing, then
   the package's class (named after its folder; the root folder's only
   with `--root-owns`, when the user says it owns its package), or a
   classless package's own `__init__` module; neither
   usage nor the concept map moves a box. With a concept map, the concepts
   are an overlay: labels, and flags for a concept spread over several
   owners or a box carrying several. Usage colours the arrows (see Arrow colours): red for
   private access, amber for an owner bypass or shared data; amber also
   marks the doubts (an owner that never uses its module).
   No box around everything, no folder boxes, never the module import
   graph.

## Pitfalls

1. **A page must not write on load.** The publish preview opens it; a page
   writes only after the user changes something.
2. **A container that grows around its children can never lose one.**
   Detach a box from its owner when its drag starts.
3. **The process is not the object's boundary.** An object that exists
   only inside one process pushes every other process to re-read its files.
   Ask whether the object should also exist where its data is used.
4. **Answer the question asked.** "How is it instantiated?" wants one line,
   not the constructor's walkthrough.
5. **Don't propose a structure the user did not ask for.** After a finding,
   stop and let the user decide.
