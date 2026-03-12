# unsupported: unsizing of other types than refs to arrays

**Affected cases:** 93 across 31 source files

## Effort estimate

| Fix | Effort | Cases resolved |
|---|---|---|
| `Box`, `Rc`, `Arc` only | **3 / 5** | 89 / 93 |
| All including `*mut`/`*const` | **3–4 / 5** | 93 / 93 |
| Silently skip `*mut`/`*const` (emit unsupported error, no crash) | **1 / 5** | +4 (no crash) |

The encoding for `&[T; N]` → `&[T]` is already complete and correct. Extending it to `Box`, `Rc`, and `Arc` (89 of 93 cases) requires threading the same array-to-slice coercion through each wrapper's internal encoding — well-understood work with several distinct cases. Raw pointer unsizing (`*mut`/`*const`) accounts for only 4 cases. Silently skipping them (returning an unsupported error rather than crashing) is near-zero additional work. Properly encoding them requires a separate dispatch path for how raw pointer snapshots represent pointee types and fat-pointer metadata, which is somewhat different from the smart pointer cases; hence the marginal increase to 3–4 if both are done together.

---

## Origin of the error

### Where it crashes

`prusti-encoder/src/encoders/mir_builtin.rs`, inside `handle_unsize`, at the `expect_array()` call on the encoded source type:

```rust
let src_ty_inner = src_ty.peel_refs();
...
let src_array_pure = deps.require_dep::<TyUsePureEnc>(ty_task)?.expect_array(); // panics
...
let dst_array_pure = deps.require_dep::<TyUsePureEnc>(ty_task)?.expect_array(); // panics
```

`peel_refs()` only strips `&T` and `&mut T`. For any other container (`Box<[T; N]>`, `Arc<[T; N]>`, `*mut [T; N]`, etc.), the inner type is not exposed and the encoded snapshot is the container's own type (`s_Box`, `s_Arc`, `s_RawPtr_mutable`, etc.) rather than an array. `expect_array()` then panics because it receives a struct-like snapshot instead.

### Rust's Unsize coercions

In Rust, the `Unsize` pointer coercion widens `[T; N]` to `[T]` (a fixed-size array to a dynamically-sized slice) through any of these outer pointer/container types:

| Source | Destination | Status |
|---|---|---|
| `&[T; N]` | `&[T]` | ✅ works |
| `&mut [T; N]` | `&mut [T]` | ✅ works |
| `Box<[T; N]>` | `Box<[T]>` | ❌ crashes (`s_Box`) |
| `Rc<[T; N]>` | `Rc<[T]>` | ❌ crashes (`s_Rc`) |
| `Arc<[T; N]>` | `Arc<[T]>` | ❌ crashes (`s_Arc`) |
| `*const [T; N]` | `*const [T]` | ❌ crashes (`s_RawPtr_immutable`) |
| `*mut [T; N]` | `*mut [T]` | ❌ crashes (`s_RawPtr_mutable`) |

All five failing kinds were observed in the test suite.

### Why `peel_refs` is insufficient

The current code uses `src_ty.peel_refs()` to obtain the inner `[T; N]` type. This works for references because `peel_refs` strips `&` and `&mut` one layer at a time. It does nothing for `Box`, `Rc`, `Arc`, or raw pointers — these are nominal types and the array is their type *argument*, not something exposed by peeling. For `Arc<[u32; 3], Global>`, the snapshot type `s_Arc` has `s_Array_type(s_UInt_u32_type(), s_UInt_usize_cons(3))` as a type argument, confirming the array is reachable — just one level deeper.

### How Rust performs these coercions

At the machine level, `Box<[T; N]>` is a **thin pointer** — a single address, because the array size is known at compile time. `Box<[T]>` is a **fat pointer** — `(*mut T, usize)`, a data address plus a runtime length. The coercion keeps the same data pointer and attaches the compile-time length `N` as metadata. No allocation or copy occurs.

Two traits govern this:

- **`Unsize<U>`** — a compiler lang item, not implementable by the user. The compiler auto-implements `[T; N]: Unsize<[T]>` and `T: Unsize<dyn Trait>`.
- **`CoerceUnsized<P<U>>`** — implementable on nightly for custom wrapper types. `Box<T>` declares:
  ```rust
  impl<T: Unsize<U>, U: ?Sized> CoerceUnsized<Box<U>> for Box<T> {}
  ```
  This tells the compiler: when `T` can be unsized to `U`, a `Box<T>` can be coerced to `Box<U>`. The compiler finds the pointer field inside `Box` and widens it from a thin pointer to a fat pointer. `Rc` and `Arc` do the same through their internal `NonNull<T>` pointer.

To do this for a custom type (nightly only), you need one field that itself supports the coercion (a raw pointer or another `CoerceUnsized` type), and declare:
```rust
impl<T: Unsize<U> + ?Sized, U: ?Sized> CoerceUnsized<MyBox<U>> for MyBox<T> {}
```

### What the fix requires

The MIR `Cast(Unsize, src, dst_ty)` is the same regardless of the outer wrapper. The problem is entirely in `handle_unsize`:

```rust
let src_ty_inner = src_ty.peel_refs();  // only strips & and &mut
// For Box<[T; N]>, src_ty_inner is still Box<[T; N]>

let src_array_pure = deps.require_dep::<TyUsePureEnc>(ty_task)?.expect_array();
// Panics: Box encodes as s_Box (StructLike), not ArrayLike
```

The fix is a dispatch step added after `peel_refs()`:

1. **If the peeled type is still a smart pointer** (`Box`, `Rc`, `Arc`): extract its first type argument (`[T; N]`) and use that for the `expect_array()` calls. Access the array data through the wrapper's `deref` snapshot field rather than directly through the reference accessors.
2. **If the peeled type is a raw pointer** (`*mut [T; N]`, `*const [T; N]`): extract the pointee type argument, then match how raw pointer snapshots represent their pointee and fat-pointer metadata.

The bulk of the existing logic — building the `forall` quantifier over element positions, relating source and destination elements, generating `unsize`/`undo_unsize` methods — is already correct and reusable. It only needs to be reached with the right inner type. The new work is: for each outer container kind, how to unwrap into the inner array snapshot, and how to reconstruct the widened wrapper snapshot on the way out.
