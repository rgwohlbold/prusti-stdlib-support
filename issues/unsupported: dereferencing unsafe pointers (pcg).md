# unsupported: dereferencing unsafe pointers (pcg)

**Affected cases:** 41 across 41 source files

## Effort estimate

| Fix | Effort |
|---|---|
| Propagate `Unsupported` error gracefully (no crash) | **1 / 5** |
| Actually support raw pointer derefs in the PCG | **4–5 / 5** |

The crash is a one-line fix: change `.unwrap().unwrap()` to match the error. Supporting raw pointer derefs in the PCG is a fundamentally hard problem because raw pointers have no region/lifetime structure, which is what the PCG's permission model is built on.

---

## Origin of the error

### Where it crashes

`prusti-encoder/src/encoders/mir_impure.rs:1289`:

```rust
let cfpcs = self.fpcs_analysis.get_all_for_bb(block).unwrap().unwrap();
```

`get_all_for_bb` returns `Result<Option<PcgBasicBlock>, PcgError>`. The outer `.unwrap()` panics when the `Result` is `Err(PcgError { kind: Unsupported(DerefUnsafePtr), ... })`.

### The deliberate guard in the PCG

The PCG visitor rejects any MIR place whose projection chain contains a `Deref` through a raw pointer:

```rust
// pcg/src/pcg/visitor/mod.rs
fn visit_place_fallable(&mut self, place: Place<'tcx>, ...) -> Result<(), PcgError<'tcx>> {
    if place.contains_unsafe_deref(self.ctxt) {
        return Err(PcgError::unsupported(PcgUnsupportedError::DerefUnsafePtr));
    }
    Ok(())
}
```

`contains_unsafe_deref` walks the projection chain and returns `true` when any step is `Deref` on a raw pointer type (`*const T` / `*mut T`):

```rust
// pcg/src/utils/place/mod.rs
pub(crate) fn contains_unsafe_deref(&self, ctxt: ...) -> bool {
    for (p, proj) in self.iter_projections(ctxt.ctxt()) {
        if p.is_raw_ptr(ctxt) && matches!(proj, PlaceElem::Deref) {
            return true;
        }
    }
    false
}
```

The same guard fires in `borrow_pcg_expansion.rs:525` and `borrow_pcg/graph/join.rs:233` during the permission graph computation.

### Affected programs

Any function body that contains a raw pointer dereference in a MIR place hits this path. In the test suite this arises from:

- **Manual allocation** (`std::alloc::alloc`): `*(ptr as *mut u16) = 42`
- **Raw pointer arithmetic** and slice access through raw pointers
- **`NonNull` and raw pointer APIs** in `ptr`, `non_null`, `mut_ptr`
- **Atomic operations** and their internal pointer handling
- **`Pin`, `Box`, `String`** internals that reach unsafe deref through their implementations

### The immediate fix

The PCG already handles this error gracefully in its own visualization code (`pcg/src/lib.rs:514`):

```rust
let Ok(Some(pcg_block)) = analysis_results.get_all_for_bb(block) else {
    continue;
};
```

The encoder should do the same — match on the error and emit an `Unsupported` diagnostic instead of panicking:

```rust
// mir_impure.rs:1289
let cfpcs = match self.fpcs_analysis.get_all_for_bb(block) {
    Ok(Some(cfpcs)) => cfpcs,
    Ok(None) => return,
    Err(e) if e.is_unsupported() => {
        // emit unsupported error and skip
        return;
    }
    Err(e) => panic!("{e:?}"),
};
```

### Why full support is hard

Raw pointers have no lifetime or region information. The PCG's permission model is built on regions: each reference carries a region that the PCG tracks to determine when permissions are created, lent, and returned. A `*mut T` has no region — it can be arbitrarily aliased, retained across arbitrary scopes, and dereferenced at any point. Incorporating this into a region-based borrow graph would require either:

1. Treating raw pointer derefs as fully opaque (losing precision but not crashing), or
2. Extending the PCG with an alias analysis for raw pointers, which is a substantial research-level undertaking.
