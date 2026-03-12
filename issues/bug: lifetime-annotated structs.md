# bug: lifetime-annotated structs

**Affected cases:** 126 across 18 source files

## Effort estimate

**1 / 5**

The fix is a one-line change that mirrors a pattern already used correctly one branch up in the same match arm.

---

## Origin of the error

### Where it crashes

`prusti-encoder/src/encoders/ty/indirect.rs:133`, inside `IndirectPredicatesEnc::do_encode_full`, in the `TySpecifics::StructLike` arm:

```rust
TySpecifics::StructLike(data) => {
    for (field_ty, accessor) in data.fields {
        let field_ty = field_ty.decompose_context(ty.ty.params, ty.args);
        let new_projection =
            LifetimeProjection::new(field_ty, task_key.region(()), None, ())
                .unwrap();   // <-- panics here
        ...
    }
}
```

### Why it panics

`LifetimeProjection::new` returns `Option<Self>`, yielding `None` when the queried region does not appear in the base type's region list:

```rust
pub fn new(...) -> Option<Self> {
    let region_idx = base
        .regions(ctxt)
        .into_iter_enumerated()
        .find(|(_, r)| *r == region)?   // returns None if region not found
        .0;
    ...
}
```

When a struct has a lifetime parameter (e.g. `struct Peekable<I: Iterator>` used in a context with lifetime `'a`), not every field necessarily mentions that lifetime. When iterating over fields to build indirect predicates, the code calls `LifetimeProjection::new` with the struct's queried region for each field. If a field's type doesn't contain that region, `new` returns `None` and `.unwrap()` panics.

### Call chain

```
encode_predicates_for_function_shape_node   (fn_wand.rs:129)
  → IndirectPredicatesEnc::do_encode_full   (indirect.rs:49)
    → LifetimeProjection::new(...).unwrap() (indirect.rs:133)  ← panics
```

`encode_predicates_for_function_shape_node` is called when encoding the wand predicates for the arguments or return type of a function that involves a struct with lifetime parameters.

### The fix

The `MutRef` branch immediately above in the same function already handles this correctly by using `if let Some`:

```rust
if let Some(new_projection) =
    LifetimeProjection::new(inner_ty, task_key.region(()), None, ())
{
    ...
}
```

The `StructLike` branch should do the same — if a field's type doesn't contain the queried lifetime, there are no indirect predicates to generate for it, so it should simply be skipped:

```rust
if let Some(new_projection) =
    LifetimeProjection::new(field_ty, task_key.region(()), None, ())
{
    let field_indirect =
        deps.require_dep::<IndirectPredicatesEnc>(new_projection)?;
    predicate_applications.extend(...);
}
```
