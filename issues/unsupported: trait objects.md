# unsupported: trait objects

**Affected cases:** 173 across 33 source files

## Effort estimate

| Fix | Effort |
|---|---|
| Partial: opaque snapshot, no crash | **1 / 5** |
| Full: meaningful verification of trait objects | **4 / 5** |

The partial fix is a one-liner: add a `ty::TyKind::Dynamic` arm to `ty_name` that returns a stable string (e.g. derived from the principal trait's name). This stops the crash and unblocks all 173 cases, but trait-object code is treated as a black box. Actually supporting trait objects in verification — assigning them a meaningful Viper snapshot type, tracking the concrete type through the vtable, and axiomatising trait method calls — is a substantial research and engineering effort.

---

## Origin of the error

### Where it crashes

`prusti-encoder/src/encoders/ty/rust_ty.rs`, in the `ty_name` method, at the catch-all `unimplemented!` arm:

```rust
other => unimplemented!("ty_name for {:?}", other),
```

The method has explicit arms for primitives, ADTs, closures, `FnPtr`, `Array`, `Slice`, etc., but no arm for `ty::TyKind::Dynamic` (trait objects).

### Call chain

```
ty_name                      ← panics here (rust_ty.rs:363)
TyData::from_ty              (rust_ty.rs:297)
RustTyDecomposition::from_ty (rust_ty.rs:69)
GArgsTyEnc::do_encode_full   (generics/args_ty.rs:52)
```

`ty_name` is called as the very first step of `TyData::from_ty`, which is itself called whenever the encoder processes any type — including type arguments. So the crash fires as soon as any type that contains `dyn Trait` as a component (a field type, a Box inner type, a generic argument, etc.) is encoded.

### Trait objects seen in practice

All observed panic messages follow the pattern `not implemented: ty_name for dyn [...] + '<lifetime>`. The trait combinations encountered include:

| Trait object | Typical context |
|---|---|
| `dyn Any + '{erased}` | `Any::downcast_ref`, `Box<dyn Any>` |
| `dyn Any + Send + '{erased}` | `Box<dyn Any + Send>` (thread panics) |
| `dyn Any + Send + Sync + '{erased}` | `Arc<dyn Any + Send + Sync>` |
| `dyn Error + '{erased}` | `Box<dyn Error>` error chains |
| `dyn Error + Send + Sync + 'static` | `Box<dyn Error + Send + Sync>` |
| `dyn Debug + '{erased}` | Debug formatting machinery |
| `dyn fmt::Write + 'a` | `write!` macro internals |
| `dyn Foo`, `dyn MyTrait` | User-defined trait objects in doctests |

### Why this is hard to fix properly

Trait objects are fundamentally opaque: the encoder does not know at the encoding site which concrete type is behind the `dyn Trait`. A proper encoding would require:

1. An abstract Viper snapshot domain for each distinct `dyn Trait` type (e.g. `s_DynAny`).
2. Some way to track the concrete type through the vtable (likely an abstract tag field in the snapshot).
3. Axioms or abstract contracts for each trait method, so that calls through the trait object can be reasoned about without knowing the concrete type.

A simpler partial fix — assigning `dyn Trait` an opaque snapshot that prevents the crash but produces no useful verification — would take far less effort and would unblock all 173 cases at the cost of treating trait-object code as a black box.
