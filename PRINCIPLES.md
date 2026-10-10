# Objectsboard principles

## Intention

The objectsboard exists to eliminate accidental complexity and to make
essential complexity explicit. Its means are information hiding, the
Information Expert pattern, trust boundaries (Secure by Design),
encapsulation, abstraction and separation of concerns. The board shows the
code as it is; the user decides each change.

1. **Complexity** is what a reader has to hold in mind to change the code
   safely, and the number of interactions that can produce a fault.
2. **Accidental complexity** comes from how the code is built; design can
   remove it.
3. **Essential complexity** comes from the problem; it cannot be removed,
   only stated and given one owner that handles it in the open (Brooks,
   "No Silver Bullet", 1986).

Each principle below says what the board shows of it today. The rest is
the user's reading of the code.

## The means

1. **Encapsulation places an invariant; it does not enforce it.** One
   owner per part and per datum gives each invariant one place to be
   checked. The invariant still has to be written down and checked: a
   test, a state-machine document, a model check.
   1. Example: an `Order` that owns its lines can keep "the total is the
      sum of the lines" true, but only if that rule is stated and tested
      inside `Order`.
   2. Board today: the owners and every access that goes around them (red
      and amber arrows); with the State table, the invariant each owner
      states, and an empty one as a finding.
2. **Information hiding** (Parnas, "On the criteria to be used in
   decomposing systems into modules", 1972). Each owner hides a design
   decision likely to change, behind an interface that does not change
   with it. What callers have to reason about (failure, idempotency,
   identity) is not hidden: it belongs in the interface. A boundary is
   judged against a list of likely changes.
   1. Example: a payment client may hide its HTTP library; it may not hide
      that it retries a timed-out charge, or the caller charges twice.
   2. Board today: private names reached from outside (red) and parts
      reached around their owner (amber). Not what an interface hides of
      its semantics, nor the list of likely changes.
3. **Abstraction.** An interface sized to what its callers use.
   1. Board today: in the one-object review, public members nobody uses
      outside and private ones reached from outside (METHOD.md, "Method",
      step 5).
4. **Separation of concerns.** One concept per object.
   1. Board today: with a concept map, a concept spread over several
      owners, and an owner carrying several concepts.
5. **Information Expert.** Logic about some data lives with that data.
   1. Example: the check that a discount lies between 0 and 100 belongs to
      `Discount`, not to the checkout that applies it after the order is
      saved.
   2. Board today: who calls whom, and, in the one-object review, outside
      reads of an object's members. Value classes (dataclasses,
      NamedTuples, Enums) are not boxes, so logic kept away from them is
      not drawn.
6. **Trust boundary** (Secure by Design). Data or identity written by
   something outside the code is untrusted input until checked, whoever
   owns it inside.
   1. Example: a cache other services can write to, read back with a
      deserializer that can run code.
   2. Board today: a resource that no code in the repo writes marks the
      boundary; the board does not mark it untrusted.

## Where complexity comes from

7. **Coupling has a strength, not only an existence** (connascence).
   Weakest to strongest: name, type, meaning, position, algorithm (static,
   visible in the source); then execution order, timing, value, identity
   (dynamic, visible only at run time). The stronger the coupling, the
   closer together the parts should live.
   1. Example: two modules that both know status `3` means "shipped" share
      a meaning; a caller that must call `open()` before `read()` shares an
      order.
   2. Board today: an arrow says a dependency exists. Copied constants and
      formats, and a lifecycle restated by a caller, are found in the
      one-object review; the other strengths are not shown.
8. **Decision scope matches data scope.** The identity a decision is made
   under (instance, session, user, tenant, service) is the identity the
   data is kept under; otherwise one decider changes data that others
   still depend on. Either the decision moves to the data's scope, or the
   data records every decider.
   1. Example: a cart kept per user and emptied by whichever of the user's
      sessions checks out, while another session is still filling it.
   2. Board today: boxes are classes, not instances; the State table holds
      the keys.
9. **Combinatorial interactions grow with states × actors × interleavings,
   not with the number of arrows.** An arrow is a channel: one box can
   call another. An interleaving is one ordering in time of the steps
   concurrent actors take over those channels: n actors of k atomic steps
   each give (nk)! / (k!)^n orderings, 20 for 2 × 3 and 184 756 for
   2 × 10, with no arrow added. Actors are threads, async tasks (they
   interleave at each `await`), processes, instances, sessions, timers,
   signal handlers, retries and other services. Fewer actors per mutable
   datum, or fewer steps per non-atomic operation, mean fewer orderings;
   removing an arrow helps only when it removes an actor from shared
   state.
   1. Example: a counter incremented by two worker threads: one arrow from
      each, and one of the orderings loses an increment.
   2. Board today: arrows show where orderings can matter (a datum two or
      more actors reach), not which actors reach it; the State table names
      them.
10. **Measure before claiming a reduction.** A change reduces complexity
    when a measure of fault exposure goes down. Arrow counts and crossings
    measure the shape of the coupling, not the exposure.
    1. Example: a refactor that clears every red and amber arrow while two
       threads still write one counter.
    2. Board today: red and amber counts, crossings, arrows over a box;
       with the State table, the actors that write each datum.
