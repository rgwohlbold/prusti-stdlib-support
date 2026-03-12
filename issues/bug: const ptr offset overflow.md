# bug: const ptr offset overflow

**Affected cases:** 93 across 31 source files

## Effort estimate

**2 / 5**

The bug is a one-line fix in a well-understood location, but threading a `TypingEnv` into the function (or finding an equivalent way to compute the type's layout) adds a small amount of plumbing.

---

## Origin of the error

### Where it crashes

`prusti-encoder/src/encoders/const.rs`, inside `ConstEnc::encode_scalar`, in the `Scalar::Ptr` / `GlobalAlloc::Memory` arm:

```rust
let size = if inner_ty.is_any_ptr() {
    vcx.tcx().data_layout().pointer_size()
} else {
    mem.0.size()   // <-- uses the entire allocation size
};

let bytes = mem
    .0
    .read_scalar(
        &vcx.tcx(),
        AllocRange { start: offset, size },  // panics when size > 16
        inner_ty.is_any_ptr(),
    )
    .unwrap();
```

The panic originates inside `rustc_middle::mir::interpret::read_target_uint`, which stores the raw bytes into a `[u8; 16]` buffer (a `u128`):

```
range end index 43 out of range for slice of length 16
```

### What `read_target_uint` requires

`read_target_uint` reads an integer value from a byte slice and returns a `u128`. Because `u128` is 16 bytes wide, it can only represent values up to 16 bytes in size. Passing it `size = mem.0.size()` works as long as the allocation is ≤ 16 bytes, but fails for any larger allocation.

### The concrete trigger

In `any_doctest_21.rs`, the constant being encoded is a pointer to the string `"core::option::Option<alloc::string::String>"` — exactly 43 bytes. The allocation holding this string is 43 bytes. `mem.0.size()` returns 43, so `read_target_uint` tries to read 43 bytes into a 16-byte buffer and panics.

In `arith_doctest_25.rs` (and many `fmt_doctest_*.rs` files), the same issue arises with format string internals. The `println!` macro lowers to `fmt::Arguments`, which contains `*const str` pointers (stored as `Scalar::Ptr`) pointing into static string allocations. Those allocations are arbitrarily large.

### Why `mem.0.size()` is wrong

When a `Scalar::Ptr` constant holds a pointer into a `GlobalAlloc::Memory` allocation, the encoder wants to read the value *at the pointer* — i.e., `sizeof(*inner_ty)` bytes starting at `offset`. Instead it reads the entire allocation, regardless of what the pointer actually points to. The correct size is determined by the type `inner_ty`, not by the allocation that happens to contain it.

### The fix

Replace `mem.0.size()` with the size of `inner_ty` as reported by the type layout:

```rust
let size = if inner_ty.is_any_ptr() {
    vcx.tcx().data_layout().pointer_size()
} else {
    let typing_env = ty::TypingEnv::fully_monomorphized();
    vcx.tcx()
        .layout_of(typing_env.as_query_input(inner_ty))
        .unwrap()
        .size
};
```

Since constants are always fully evaluated and their types are concrete, `TypingEnv::fully_monomorphized()` is the appropriate environment. The only wrinkle is that `inner_ty` may be a dynamically-sized type (e.g., `str`), for which layout is not computable; those cases would need a separate guard or are already handled before reaching this branch.
