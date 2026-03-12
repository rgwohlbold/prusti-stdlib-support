# unsupported: constant scalars that are not primitives

**Affected cases:** 413 (largest single failure category)

## Effort estimate

**3 / 5**

The approach is explicitly documented in a `TODO` comment in the source. The fix is conceptually straightforward: inspect the memory layout and reconstruct the composite value field-by-field. However, it requires careful integration with the Viper snapshot encoding infrastructure and must handle several distinct composite shapes (newtypes, structs, fieldless enums).

---

## Origin of the error

### Where it crashes

`prusti-encoder/src/encoders/const.rs`, inside `ConstEnc::encode_scalar`, at the call to `kind.expect_primitive()`:

```rust
Scalar::Int(int) => {
    // TODO: Aggregates like structs will also show up as scalars
    // This means that the scalar doesn't contain all the data
    // and that it doesn't match the type of ty, which will lead
    // to a panic. We would have to encode these by looking at
    // the memory layout and iterating over the individual fields
    let prim = kind.expect_primitive();   // <-- panics here
    ...
}
```

### Why it happens

In Rust's MIR, small composite types that fit in a single machine word are represented as `Scalar::Int` constants — the same representation used for primitive integers. This means a value of type `AsciiChar`, `Wrapping<u32>`, `Alignment`, or a single-element tuple may arrive at `encode_scalar` with `Scalar::Int` even though its Viper snapshot type (`s_AsciiChar`, `s_Wrapping`, etc.) is a struct or enum, not a primitive.

The encoder currently assumes that a `Scalar::Int` always corresponds to a primitive Rust type, so it calls `expect_primitive()` on the `TyData` and panics when it gets a struct-like or enum-like snapshot instead.

### Affected types

The 413 failing cases cover 25 distinct non-primitive snapshot types, including:

| Snapshot type | Rust type | Shape |
|---|---|---|
| `s_AsciiChar` | `core::ascii::Char` | fieldless enum backed by `u8` |
| `s_Alignment` | `core::ptr::Alignment` | newtype wrapping an enum |
| `s_Wrapping` | `core::num::Wrapping<T>` | newtype wrapper |
| `s_Saturating` | `core::num::Saturating<T>` | newtype wrapper |
| `s_RawPtr_mutable` | `*mut T` | raw pointer |
| `s_Result` | `Result<T, E>` | enum |
| `s_Array` | `[T; N]` | array |
| `s_2_Tuple` | `(A, B)` | tuple |

### The fix

The `TODO` comment outlines the required approach: when the type is not a primitive, use the Rust memory layout of the type to read the individual bytes/fields out of the scalar's bit pattern and then construct the corresponding Viper snapshot expression (e.g. call `s_AsciiChar_cons(...)` with the discriminant value). For newtypes this is one level of unwrapping; for enums it requires reading the discriminant and selecting the right variant constructor.
