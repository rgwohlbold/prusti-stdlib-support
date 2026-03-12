# unsupported: recursive struct types

**Affected cases:** 120 across 8 source files (`entry`, `iterator`, `map`, `option`, `range`, `rc`, `set`, `sync`)

## Effort estimate

**4 / 5**

The cycle is correctly detected by the task encoder, but the calling code unconditionally `.unwrap()`s the result. The fix cannot simply ignore the error — when a cycle is detected, the encoder needs something concrete to return in place of the recursive type expression. This requires either lazy/deferred evaluation of type constructors, a fixpoint encoding, or an opaque fallback for the recursive case.

---

## Origin of the error

### Where it crashes

`prusti-encoder/src/encoders/ty/generics/params.rs:277`, inside `GenericParams::ty_expr`:

```rust
let args = deps.require_dep::<GArgsTyEnc>(ty.args).unwrap();  // line 277 — panics
let ty_constructor = ...;
let args = deps.require_dep::<GArgsTyEnc>(ty.args).unwrap();  // line 279
ty_constructor(args.get_ty(), args.get_const())
```

`require_dep` calls `check_cycle` after every dependency resolution. When a cycle is detected, `check_cycle` returns `Err(EncodeFullError::AlreadyEncoded)` and the `.unwrap()` panics.

### The cycle

The encoding forms a loop:

```
GenericParams::ty_expr (params.rs:277)
  → require_dep::<GArgsTyEnc>
    → GArgsTyEnc::do_encode_full (args_ty.rs:53)
      → GenericParams::ty_expr (params.rs:279)
        → require_dep::<GArgsTyEnc>   ← same task key as above
          → check_cycle() → AlreadyEncoded
            → .unwrap() panics
```

`GArgsTyEnc` encodes the type arguments of a generic type, and `GenericParams::ty_expr` builds the Viper expression for each type parameter by calling back into `GArgsTyEnc` for any nested type arguments. For types whose generic arguments refer back to themselves — directly or through struct fields — this mutual recursion has no base case and the task encoder detects it as a cycle.

### How `AlreadyEncoded` arises

The task encoder tracks in-progress encodings in a cache. `check_cycle` checks whether the current encoder is already in a `Started`, `Encoded`, or `ErrorEncode` state for the same task key. If it is, it means encoding is being re-entered recursively, and `AlreadyEncoded` is returned:

```rust
pub fn check_cycle(&self) -> Result<(), EncodeFullError<'vir, E>> {
    if E::with_cache(move |cache| {
        matches!(cache.borrow().get(task_key),
            Some(TaskEncoderCacheState::Encoded { .. } | ...))
    }) {
        return Err(EncodeFullError::AlreadyEncoded);
    }
    Ok(())
}
```

The `TODO` comment in `indirect.rs` also notes this problem in the context of struct field encoding:

```rust
// TODO: invalid recursion here if the defined struct is
// recursive!
```

### Affected types

The affected source files involve collections and wrapper types with complex internal generic structure: `BTreeMap`/`BTreeSet` entries, `Range` types, `Rc`/`Arc`-wrapped types, and iterator adapters. These are not necessarily types that are recursive in the Rust sense (`struct Node { next: Box<Node> }`), but types where the Viper encoding of their generic parameter expressions re-enters an encoding that is already in progress.

### Why this is hard to fix

The encoding cannot simply skip the recursive case, because `ty_expr` must return a `vir::ExprTyVal<'vir>` — there is no `Option` or error return path. Fixing this requires one of:

1. **Lazy type constructors**: generate a placeholder expression and fill it in once the recursive encoding completes (requires support in the Viper IR for forward references).
2. **Fixpoint encoding**: identify recursive types at the start of encoding and generate a recursive Viper domain with a fixpoint definition.
3. **Opaque fallback**: treat the recursive field as an uninterpreted type, losing some precision but preventing the crash.
