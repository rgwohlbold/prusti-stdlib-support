# unsupported: enum types in indirect predicate encoder

**Affected cases:** unknown (any function whose type graph includes a lifetime-carrying generic enum)

## Effort estimate

| Fix | Effort |
|---|---|
| Partial: suppress panic, return no indirect predicates | **1 / 5** |
| Full: per-variant discriminant-conditional indirect predicates | **3 / 5** |

The partial fix is a one-character change: replace `todo!("{:?}", ty)` with `()`. This aligns `EnumLike` with the existing (also incomplete) treatment of `Param`, `Opaque`, and `ArrayLike`, which silently return no indirect predicates. It removes the crash at the cost of potentially missing indirect predicates for enum types that contain mutable references — the same soundness gap already accepted for the other three variants (and acknowledged in the comment on lines 62–69 of `indirect.rs`).

The full fix generates discriminant-conditional indirect predicates using `snap_to_discr_snap` and per-variant `discr` values already present in `TyPureEnumData` and `TyPureVariantData`. The pattern is a direct extension of the `StructLike` arm, with an implication guard added per variant.

---

## Origin of the error

### Where it crashes

`prusti-encoder/src/encoders/ty/indirect.rs:146`, inside `IndirectPredicatesEnc::do_encode_full`:

```rust
TySpecifics::EnumLike(..) => todo!("{:?}", ty),
```

### Call chain

```
fn_wand.rs:129 (or loop.rs:128)
  → require_dep::<IndirectPredicatesEnc>(...)
    → IndirectPredicatesEnc::do_encode_full   (indirect.rs:49)
      → match combined.specifics              (indirect.rs:55)
        → EnumLike(..) => todo!()             (indirect.rs:146)  ← panics
```

`IndirectPredicatesEnc` is invoked whenever the encoder must produce indirect predicates for a type relative to a given lifetime — i.e. the Viper predicates for heap cells reachable through a mutable borrow with that lifetime. The two call sites are:

- `fn_wand.rs`: encoding the magic wand framing for function arguments / return types.
- `loop.rs`: encoding loop-invariant wand frames for loop-carried borrows.

### When it fires

The early-exit guard at line 60 prevents the panic for enum types with no generic arguments:

```rust
_ if ty.args.args().is_empty() => (),
```

The crash fires only when all three of the following hold:

1. The type is an enum (`TySpecifics::EnumLike`).
2. The type has non-empty generic arguments (e.g. `Option<&'a mut T>`, `Result<&'a T, E>`).
3. The queried lifetime appears in those arguments, so a `LifetimeProjection` for this type is actually constructed and passed to the encoder.

Concretely, any function that takes or returns a generic enum containing a reference — including `Option<&'a mut T>` or `Result<T, &'a E>` — will trigger this when Prusti builds the wand for the borrowed resource.

### What the full fix requires

For a struct, the encoder iterates over fields and recursively collects indirect predicates for any field whose type contains the queried lifetime:

```rust
TySpecifics::StructLike(data) => {
    for (field_ty, accessor) in data.fields {
        let field_ty = field_ty.decompose_context(ty.ty.params, ty.args);
        if let Some(new_projection) =
            LifetimeProjection::new(field_ty, task_key.region(()), None, ())
        {
            let field_indirect =
                deps.require_dep::<IndirectPredicatesEnc>(new_projection)?;
            predicate_applications.extend(
                field_indirect.predicate_applications.into_iter().map(project),
            );
        }
    }
}
```

For an enum, the same logic applies per variant, but each collected indirect predicate must be guarded by a discriminant check. The required components are already present in the combined type:

- `data.data.1` is `TyPureEnumData<'vir>`, which has `snap_to_discr_snap` to extract the discriminant from a self snap.
- Each `variant.data.1` is `TyPureVariantData<'vir>`, which has `discr: vir::ExprCSnap<'vir>` — the constant discriminant for that variant.
- Each field in `variant.inner.fields` provides the same `(field_ty, accessor)` pair as in the `StructLike` arm.

The generated expression for a field `f` in variant `v` with inner indirect predicate `inner_expr` is:

```
(snap_to_discr_snap(self_snap) == variant.discr) ==> inner_expr(field_accessor.read(self_snap))
```

This implication-per-variant pattern is already used in `enumlike.rs:107` for the impure predicate encoder:

```rust
(([snap_disc]) == ([variant.1.discr])) ==>
    ([variant_pred](ref_self, [..[builder.params.ty_exprs()]], [..]))
```

### Why the partial fix is incomplete

Returning empty `predicate_applications` for an `EnumLike` type loses the indirect predicates for any mutable-reference-carrying variant fields. This is the same class of soundness gap already acknowledged for `Param`, `Opaque`, and `ArrayLike` in the existing TODO comment:

```rust
// TODO: it's not valid to have nothing for these. We should fix
// this by using an opaque predicate to represent potential
// indirect stuff. For example:
// fn foo<'a, T: Trait<'a>>(x: T) -> &'a mut i32 { x.get() }
// Here, `T` could be instantiated as `&'a mut i32` in which
// case we would want a wand with `i32(result) --* opaque_behind_a(x)`.
// This is why we should return `opaque_behind_a(x)` here.
```

For enums, a concrete failing example would be:

```rust
fn unwrap_ref<'a>(x: Option<&'a mut i32>) -> &'a mut i32 {
    x.unwrap()
}
```

Here, `Option<&'a mut i32>` queried for lifetime `'a` should produce the indirect predicate `i32(result_deref)`, but the partial fix silently returns nothing, so the wand framing is incomplete.
