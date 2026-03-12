# verification error could not be backtranslated

**Affected cases:** 37 across 37 source files

## Effort estimate

| Fix | Effort | Effect |
|---|---|---|
| Add missing `insufficient.permission` handler | **1 / 5** | crash → reported verification error |
| Investigate and fix root cause of false-positive permission failures | **3–4 / 5** | crash → passes (or correct failure) |

The crash is a one-liner to suppress: add the missing handler so the error is reported as a verification failure instead of panicking. Understanding *why* Prusti generates permission preconditions that fail for provably-correct programs (e.g., `12 / 2 == 6`) is a separate and harder problem.

---

## Origin of the error

### Where it crashes

`prusti-server/src/lib.rs:228`, inside `handle_result`:

```rust
prusti_encoder::backtranslate_error(
    &error.full_id,         // "application.precondition:insufficient.permission"
    offending_pos_id, ...,
)
.expect("verification error could not be backtranslated")   // panics
```

`backtranslate_error` calls `vcx.backtranslate(error_kind, ...)` which walks the span tree looking for a registered handler whose `error_kind` matches. If no handler is found it prints `no handler found for error kind: …` and returns `None`. The `expect` then panics.

### The Viper error taxonomy

Viper distinguishes two reasons a precondition can fail on a pure function application:

- `application.precondition:assertion.false` — a boolean assertion in the precondition is false (e.g., `requires divisor != 0`)
- `application.precondition:insufficient.permission` — an accessibility predicate in the precondition is not satisfied (e.g., `requires acc(x.field)`)

Prusti registers a handler for `application.precondition:assertion.false` in two places:

```rust
// mir_impure.rs:1656 — pure function calls in impure context
vcx.handle_error("application.precondition:assertion.false", move |reason_span_opt| { ... });

// mir_pure.rs:897 — pure function calls in pure context
vcx.handle_error("application.precondition:assertion.false", move |reason_span_opt| { ... });
```

There is no corresponding handler for `application.precondition:insufficient.permission` anywhere in the codebase.

### What triggers the permission error

Viper pure functions can have permission-based preconditions such as `requires acc(pred(x))`. When such a function is applied in an impure context (`call_impure`), Viper checks that the caller holds those permissions. If not, it reports `application.precondition:insufficient.permission` rather than `assertion.false`.

The affected programs span a wide range — simple arithmetic (`12 / 2`, `12 % 10`), atomic operations, iterators, raw pointers, `Option`, `String`, `try` desugaring. The diversity suggests this is a systematic gap in permission grant coverage, not an isolated case: at some call sites the encoding applies a pure function without first establishing the required permissions in the Viper heap.

### The immediate fix

Add the missing handler alongside the existing one at each call site in `mir_impure.rs` and `mir_pure.rs`:

```rust
vcx.handle_error("application.precondition:assertion.false", move |reason_span_opt| {
    // ... existing handler ...
});
vcx.handle_error("application.precondition:insufficient.permission", move |reason_span_opt| {
    let mut error = PrustiError::verification(
        "precondition might not hold (permission)",
        span.into(),
    );
    if let Some(reason_span) = reason_span_opt {
        error.add_note_mut("the failing precondition is here", Some(reason_span.into()));
    }
    Some(vec![error])
});
```

This converts the crash into a reported (possibly false positive) verification error.

### The root cause

For programs like `let mut x: u32 = 12; x /= 2; assert_eq!(x, 6)` there is no genuine permission violation — the code is trivially correct. The permission error is a false positive, implying that the Prusti encoding of the surrounding method call (likely `DivAssign::div_assign` or the arithmetic operations it expands to) does not correctly re-establish the permissions needed for the subsequent pure function applications. Fixing this requires tracing how permissions flow through the relevant call sites and ensuring they are inhaled/exhaled correctly.
