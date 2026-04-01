use quote::quote;
use serde::Deserialize;
use std::collections::HashSet;
use std::fs;
use std::path::PathBuf;
use std::process::Command;
use syn::{AttrStyle, File as SynFile, Item, Stmt};

#[derive(Deserialize)]
struct RustdocOutput {
    doctests: Vec<Doctest>,
}

#[derive(Deserialize)]
struct Doctest {
    file: String,
    line: u64,
    doctest_attributes: DoctestAttributes,
    doctest_code: Option<DoctestCode>,
}

#[derive(Deserialize)]
struct DoctestAttributes {
    should_panic: bool,
    no_run: bool,
    compile_fail: bool,
    standalone_crate: bool,
    #[serde(deserialize_with = "deserialize_ignore")]
    ignore: bool,
    rust: bool,
}

/// Rustdoc serializes ignore as `"None"` or `{"Some": ["reason"]}`.
/// We just need to know whether it's ignored at all.
fn deserialize_ignore<'de, D: serde::Deserializer<'de>>(d: D) -> Result<bool, D::Error> {
    let v = serde_json::Value::deserialize(d)?;
    Ok(v != serde_json::Value::String("None".into()))
}

#[derive(Deserialize)]
struct DoctestCode {
    crate_level: String,
    code: String,
    wrapper: Option<Wrapper>,
}

#[derive(Deserialize)]
struct Wrapper {
    returns_result: bool,
}

/// Clean up crate-level attributes:
/// - Remove `#![deny(warnings)]` (causes errors on stable features)
/// - Remove `stmt_expr_attributes` from `#![feature(...)]` (Prusti injects it)
fn clean_crate_attrs(attrs: &mut Vec<syn::Attribute>) {
    attrs.retain_mut(|attr| {
        if !matches!(attr.style, AttrStyle::Inner(_)) {
            return true;
        }
        // Remove #![deny(warnings)]
        if attr.path().is_ident("deny") {
            let mut has_warnings = false;
            let _ = attr.parse_nested_meta(|meta| {
                if meta.path.is_ident("warnings") {
                    has_warnings = true;
                }
                Ok(())
            });
            if has_warnings {
                return false;
            }
        }
        // Remove stmt_expr_attributes from #![feature(...)]
        if attr.path().is_ident("feature") {
            let mut features: Vec<syn::Ident> = Vec::new();
            let _ = attr.parse_nested_meta(|meta| {
                if let Some(ident) = meta.path.get_ident() {
                    if ident != "stmt_expr_attributes" {
                        features.push(ident.clone());
                    }
                }
                Ok(())
            });
            if features.is_empty() {
                return false;
            }
            *attr = syn::parse_quote!(#![feature(#(#features),*)]);
        }
        true
    });
}

/// Parse a raw doctest snippet using syn, hoist attributes and items to global
/// scope, and leave execution statements wrapped in `fn main()`.
fn wrap_and_hoist_doctest(
    raw_doctest: &str,
    needs_wrapper: bool,
    returns_result: bool,
) -> Option<String> {
    let to_parse = if needs_wrapper {
        if returns_result {
            format!(
                "fn main() -> Result<(), Box<dyn std::error::Error>> {{\n{}\n}}",
                raw_doctest
            )
        } else {
            format!("fn main() {{\n{}\n}}", raw_doctest)
        }
    } else {
        raw_doctest.to_string()
    };
    let mut ast = syn::parse_str::<SynFile>(&to_parse).ok()?;

    if needs_wrapper {
        let mut hoisted_items: Vec<Item> = Vec::new();
        let mut main_stmts = Vec::new();

        if let Some(Item::Fn(mut dummy_func)) = ast.items.pop() {
            for attr in dummy_func.attrs.drain(..) {
                if matches!(attr.style, AttrStyle::Inner(_)) {
                    ast.attrs.push(attr);
                }
            }

            for stmt in dummy_func.block.stmts.drain(..) {
                match stmt {
                    Stmt::Item(item) => hoisted_items.push(item),
                    // Brace-delimited macro invocations (e.g. thread_local! { ... })
                    // are item-like and belong at file scope.
                    Stmt::Macro(ref m)
                        if matches!(m.mac.delimiter, syn::MacroDelimiter::Brace(_)) =>
                    {
                        hoisted_items.push(Item::Macro(syn::ItemMacro {
                            attrs: m.attrs.clone(),
                            ident: None,
                            mac: m.mac.clone(),
                            semi_token: m.semi_token,
                        }));
                    }
                    _ => main_stmts.push(stmt),
                }
            }

            if returns_result {
                dummy_func.sig.output = syn::parse_quote!(-> Result<(), impl core::fmt::Debug>);
            }
            dummy_func.block.stmts = main_stmts;
            hoisted_items.push(Item::Fn(dummy_func));
        }

        ast.items = hoisted_items;
    }

    clean_crate_attrs(&mut ast.attrs);

    let tokens = quote!(#ast);
    let parsed_file: SynFile = syn::parse2(tokens).ok()?;
    Some(prettyplease::unparse(&parsed_file))
}

/// Process a single doctest.
fn process_doctest(
    crate_level: &str,
    code: &str,
    has_wrapper: bool,
    returns_result: bool,
) -> Option<String> {
    let full = format!("{}{}", crate_level, code);
    wrap_and_hoist_doctest(&full, has_wrapper, returns_result)
}

/// Build the output filename: {library}_{path_part}_doctest_{line}.rs
fn build_filename(library: &str, file_path: &str, line: u64) -> String {
    let prefix = format!("{}/src/", library);
    let rel = file_path.strip_prefix(&prefix).unwrap_or(file_path);
    let rel = match rel.rfind('.') {
        Some(i) => &rel[..i],
        None => rel,
    };
    let path_part = rel.replace('/', "_");
    format!("{}_{}_doctest_{}.rs", library, path_part, line)
}

fn parse_args() -> (PathBuf, PathBuf, String) {
    let args: Vec<String> = std::env::args().collect();
    let get = |flag: &str| -> String {
        let pos = args.iter().position(|a| a == flag).unwrap_or_else(|| {
            eprintln!("Missing required argument: {flag}");
            std::process::exit(1)
        });
        args.get(pos + 1)
            .unwrap_or_else(|| {
                eprintln!("Missing value for {flag}");
                std::process::exit(1)
            })
            .clone()
    };
    (
        PathBuf::from(get("--src-dir")),
        PathBuf::from(get("--snippets-dir")),
        get("--library"),
    )
}

fn main() {
    let (src_dir, snippets_dir, library) = parse_args();

    if !src_dir.is_dir() {
        eprintln!("Error: Source directory {:?} does not exist.", src_dir);
        std::process::exit(1);
    }

    fs::create_dir_all(&snippets_dir).expect("Failed to create snippets directory");

    let output = Command::new("cargo")
        .args([
            "rustdoc",
            "--",
            "-Zunstable-options",
            "--output-format=doctest",
        ])
        .current_dir(&src_dir)
        .output()
        .expect("Failed to run cargo rustdoc");

    if !output.status.success() {
        eprintln!(
            "Error: cargo rustdoc failed:\n{}",
            String::from_utf8_lossy(&output.stderr)
        );
        std::process::exit(1);
    }

    let data: RustdocOutput =
        serde_json::from_slice(&output.stdout).expect("Failed to parse rustdoc JSON");

    let mut seen = HashSet::new();
    let mut n_written = 0u64;
    let mut n_skipped_filter = 0u64;
    let mut n_skipped_parse = 0u64;

    for dt in &data.doctests {
        let attrs = &dt.doctest_attributes;

        if attrs.should_panic
            || attrs.no_run
            || attrs.compile_fail
            || attrs.standalone_crate
            || attrs.ignore
            || !attrs.rust
            || dt.doctest_code.is_none()
            || dt.file.contains("..")
        {
            n_skipped_filter += 1;
            continue;
        }

        let key = (dt.file.as_str(), dt.line);
        if seen.contains(&key) {
            continue;
        }
        seen.insert(key);

        let dc = dt.doctest_code.as_ref().unwrap();
        let has_wrapper = dc.wrapper.is_some();
        let returns_result = dc.wrapper.as_ref().is_some_and(|w| w.returns_result);
        let crate_level = &dc.crate_level;

        let full_code = match process_doctest(&crate_level, &dc.code, has_wrapper, returns_result) {
            Some(code) => code,
            None => {
                n_skipped_parse += 1;
                eprintln!("  parse failure: {}:{}", dt.file, dt.line,);
                continue;
            }
        };

        let filename = build_filename(&library, &dt.file, dt.line);
        let out_path = snippets_dir.join(&filename);
        fs::write(&out_path, &full_code).expect("Failed to write snippet");
        n_written += 1;
    }

    eprintln!(
        "Done! Extracted {} snippets to {:?} (filtered {}, parse failures {})",
        n_written, snippets_dir, n_skipped_filter, n_skipped_parse
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hoist(code: &str) -> String {
        wrap_and_hoist_doctest(code, true, false).expect("parse failed")
    }

    #[test]
    fn simple_statements_wrapped_in_main() {
        let out = hoist(
            r#"
let x = 1;
assert_eq!(x, 1);
"#,
        );
        assert!(out.contains("fn main()"));
        assert!(out.contains("let x = 1;"));
    }

    #[test]
    fn inner_attrs_hoisted_to_crate_level() {
        let out = hoist(
            r#"
#![feature(test_feature)]
let x = 1;
"#,
        );
        let feat = out.find("#![feature(test_feature)]").unwrap();
        let main = out.find("fn main()").unwrap();
        assert!(feat < main);
    }

    #[test]
    fn use_and_struct_hoisted_outside_main() {
        let out = hoist(
            r#"
use std::collections::HashMap;

struct Foo {
    x: i32,
}

let f = Foo { x: 1 };
"#,
        );
        let main = out.find("fn main()").unwrap();
        assert!(out.find("use std::collections::HashMap;").unwrap() < main);
        assert!(out.find("struct Foo").unwrap() < main);
        assert!(out.contains("let f = Foo"));
    }

    #[test]
    fn fn_and_impl_hoisted() {
        let out = hoist(
            r#"
struct S;

impl S {
    fn go(&self) -> i32 { 42 }
}

fn helper() -> i32 { 1 }

let s = S;
assert_eq!(s.go(), 42);
"#,
        );
        let main = out.find("fn main()").unwrap();
        assert!(out.find("impl S").unwrap() < main);
        assert!(out.find("fn helper()").unwrap() < main);
        assert!(out.contains("s.go()"));
    }

    #[test]
    fn user_provided_main_not_double_wrapped() {
        let out = wrap_and_hoist_doctest(
            r#"
use std::fmt;

fn main() {
    println!("hello");
}
"#,
            false,
            false,
        )
        .unwrap();
        assert_eq!(out.matches("fn main()").count(), 1);
        assert!(out.contains("use std::fmt;"));
    }

    #[test]
    fn stmt_expr_attributes_removed_when_sole_feature() {
        let out = hoist(
            r#"
#![feature(stmt_expr_attributes)]
let x = 1;
"#,
        );
        assert!(!out.contains("stmt_expr_attributes"));
        assert!(!out.contains("#![feature"));
    }

    #[test]
    fn stmt_expr_attributes_removed_but_others_kept() {
        let out = hoist(
            r#"
#![feature(test_feature, stmt_expr_attributes)]
let x = 1;
"#,
        );
        assert!(!out.contains("stmt_expr_attributes"));
        assert!(out.contains("#![feature(test_feature)]"));
    }

    #[test]
    fn deny_warnings_removed() {
        let out = hoist(
            r#"
#![deny(warnings)]
let x = 1;
"#,
        );
        assert!(!out.contains("deny"));
        assert!(!out.contains("warnings"));
    }

    #[test]
    fn deny_warnings_removed_with_other_attrs() {
        let out = hoist(
            r#"
#![deny(warnings)]
#![feature(test_feature)]
let x = 1;
"#,
        );
        assert!(!out.contains("deny"));
        assert!(out.contains("#![feature(test_feature)]"));
    }

    #[test]
    fn mod_hoisted_outside_main() {
        let out = hoist(
            r#"
mod inner {
    pub fn f() -> i32 { 1 }
}

assert_eq!(inner::f(), 1);
"#,
        );
        assert!(out.find("mod inner").unwrap() < out.find("fn main()").unwrap());
    }

    #[test]
    fn extern_fn_hoisted() {
        let out = hoist(
            r#"
unsafe extern "C" fn my_func() -> i32 { 42 }

let x = unsafe { my_func() };
"#,
        );
        assert!(
            out.find(r#"unsafe extern "C" fn my_func"#).unwrap() < out.find("fn main()").unwrap()
        );
    }

    #[test]
    fn process_doctest_prepends_crate_level() {
        let result = process_doctest("#![allow(unused)]\n", "let x = 1;", true, false).unwrap();
        assert!(result.starts_with("#![allow(unused)]\n"));
    }

    #[test]
    fn process_doctest_no_wrapper() {
        let result = process_doctest("#![allow(unused)]\n", "fn main() { }", false, false).unwrap();
        assert!(result.contains("#![allow(unused)]"));
        assert!(result.contains("fn main()"));
    }

    #[test]
    fn build_filename_simple() {
        assert_eq!(
            build_filename("core", "core/src/hint.rs", 234),
            "core_hint_doctest_234.rs"
        );
    }

    #[test]
    fn build_filename_nested_path() {
        assert_eq!(
            build_filename("core", "core/src/num/mod.rs", 42),
            "core_num_mod_doctest_42.rs"
        );
    }

    #[test]
    fn returns_result_adds_return_type() {
        let out = wrap_and_hoist_doctest(
            r#"
let x = "123".parse::<i32>()?;
assert_eq!(x, 123);
Ok(())
"#,
            true,
            true,
        )
        .unwrap();
        assert!(out.contains("-> Result<(), impl core::fmt::Debug>"));
        assert!(out.contains("Ok(())"));
    }

    #[test]
    fn expression_macros_stay_in_main() {
        let out = hoist(
            r#"
let x = vec![1, 2, 3];
assert_eq!(x.len(), 3);
println!("done");
"#,
        );
        let main = out.find("fn main()").unwrap();
        assert!(out.find("assert_eq!").unwrap() > main);
        assert!(out.find("vec!").unwrap() > main);
    }

    #[test]
    fn macro_invocation_hoisted() {
        let out = hoist(
            r#"
use std::cell::RefCell;

thread_local! {
    static FOO: RefCell<u32> = RefCell::new(1);
}

fn get_foo() -> u32 {
    FOO.with_borrow(|v| *v)
}

assert_eq!(get_foo(), 1);
"#,
        );
        let main = out.find("fn main()").unwrap();
        assert!(out.find("thread_local!").unwrap() < main);
        assert!(out.find("fn get_foo()").unwrap() < main);
    }

    #[test]
    fn no_wrapper_cleans_attrs() {
        let out = wrap_and_hoist_doctest(
            r#"
#![deny(warnings)]
#![feature(stmt_expr_attributes, coroutines)]
fn main() {
    let x = 1;
}
"#,
            false,
            false,
        )
        .unwrap();
        assert!(!out.contains("deny"));
        assert!(!out.contains("stmt_expr_attributes"));
        assert!(out.contains("#![feature(coroutines)]"));
    }

    #[test]
    fn unparseable_code_returns_none() {
        assert!(wrap_and_hoist_doctest("this is not valid rust {{{", true, false).is_none());
    }
}
