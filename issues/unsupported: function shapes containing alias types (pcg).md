# unsupported: function shapes containing alias types (pcg)

**Affected cases:** 121 across 32 source files

## Effort estimate

**3 / 5**

The rejection is a deliberate guard, not an accidental crash. Fixing it requires using the caller's substitutions to instantiate and normalise the signature before checking for aliases, then ensuring the subsequent outlives computation works correctly on the normalised types. Many call sites would be resolved by this, but fully generic call sites (where the caller is also parameterised) would still have aliases and need a separate strategy.

---

## Origin of the error

### Where it is raised

`pcg/src/borrow_pcg/edge/abstraction/function.rs`, inside `FunctionDataShapeDataSource::new`:

```rust
let sig = data.identity_fn_sig(tcx);
...
if sig.has_aliases() {
    return Err(MakeFunctionShapeError::ContainsAliasType);
}
```

`identity_fn_sig` instantiates the function's signature with the identity substitution (i.e. keeping all generic parameters as abstract). `has_aliases()` returns `true` whenever the signature contains an unresolved alias type — most commonly an associated type such as `Self::Target`, `Self::Item`, or `Self::Output`. The wand encoder propagates this as `Unsupported("function shape: ContainsAliasType")` and the enclosing method encoding fails.

### What alias types are

In Rust's type system, an alias type is any type that is not yet fully normalised:

- **Associated types**: `<I as Iterator>::Item`, `<T as Deref>::Target`, `<T as Index<usize>>::Output`
- **`impl Trait`** before opaque-type lowering
- **Unevaluated const generic expressions** (e.g. `[u8; Self::BITS / 8]`)

When `identity_fn_sig` is used, all of these remain unevaluated because the function is inspected without any concrete substitution for its type parameters.

### Affected functions

The error surfaces on a wide range of standard library trait methods whose return types are associated types:

| Function | Alias type in signature |
|---|---|
| `Deref::deref` | `&Self::Target` |
| `DerefMut::deref_mut` | `&mut Self::Target` |
| `Index::index` / `IndexMut::index_mut` | `&Self::Output` / `&mut Self::Output` |
| `Iterator::next` | `Option<Self::Item>` |
| `FnMut::call_mut` | `Self::Output` |
| `Try::branch` | `ControlFlow<Self::Residual, Self::Output>` |
| `str::parse` | `Result<F, <F as FromStr>::Err>` |
| `ptr::metadata` | `<T as Pointee>::Metadata` |
| `Cow::into_owned` | `<B as ToOwned>::Owned` |
| `Option::as_deref_mut` | `Option<&mut <T as Deref>::Target>` |
| `slice::get` | `Option<&<I as SliceIndex<[T]>>::Output>` |
| `i32::to_ne_bytes` | `[u8; N]` (const generic alias) |

These are pervasive operations — dereferencing, indexing, iterating, and error propagation with `?` all go through functions in this list.

### Why this is hard

The `caller_substs` parameter is already threaded through to `FunctionDataShapeDataSource::new`, but the current code ignores it when constructing the signature, using `identity_fn_sig` instead. The fix would be to instantiate the signature with `caller_substs` (when present), normalise the resulting types, and only then check for remaining aliases. The tricky parts are:

1. Normalisation requires an inference context and a typing environment.
2. Even after substitution, a caller that is itself generic may still leave alias types unresolved.
3. The `OutlivesEnvironment` constructed below the alias check also needs to be built from the normalised types.

A partial fix targeting concrete call sites (where `caller_substs` is fully monomorphic) would already resolve the majority of the 121 cases, since most failing snippets call these methods on concrete types.
