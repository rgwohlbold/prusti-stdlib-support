use quote::quote;
use serde::Deserialize;
use std::collections::HashSet;
use std::fs;
use std::path::PathBuf;
use std::process::Command;
use syn::{AttrStyle, File as SynFile};

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
    before: String,
    after: String,
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

/// Assemble a complete doctest source file, clean crate attrs, and format.
fn process_doctest(dc: &DoctestCode) -> Option<String> {
    let source = match &dc.wrapper {
        Some(w) => format!("{}{}{}{}", dc.crate_level, w.before, dc.code, w.after),
        None => format!("{}{}", dc.crate_level, dc.code),
    };
    let mut ast = syn::parse_str::<SynFile>(&source).ok()?;
    clean_crate_attrs(&mut ast.attrs);
    let tokens = quote!(#ast);
    let parsed: SynFile = syn::parse2(tokens).ok()?;
    Some(prettyplease::unparse(&parsed))
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

        let full_code = match process_doctest(dc) {
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

    fn simple_wrapper() -> Option<Wrapper> {
        Some(Wrapper {
            before: "fn main() {\n".into(),
            after: "\n}".into(),
            returns_result: false,
        })
    }

    fn result_wrapper() -> Option<Wrapper> {
        Some(Wrapper {
            before: "fn main() { fn _inner() -> core::result::Result<(), impl core::fmt::Debug> {\n".into(),
            after: "\n} _inner().unwrap() }".into(),
            returns_result: true,
        })
    }

    fn dc(crate_level: &str, code: &str, wrapper: Option<Wrapper>) -> DoctestCode {
        DoctestCode {
            crate_level: crate_level.into(),
            code: code.into(),
            wrapper,
        }
    }

    #[test]
    fn simple_statements_wrapped_in_main() {
        let out = process_doctest(&dc("", "let x = 1;\nassert_eq!(x, 1);", simple_wrapper())).unwrap();
        assert!(out.contains("fn main()"));
        assert!(out.contains("let x = 1;"));
    }

    #[test]
    fn crate_level_attrs_preserved() {
        let out = process_doctest(&dc(
            "#![allow(unused)]\n#![feature(test_feature)]\n\n",
            "let x = 1;",
            simple_wrapper(),
        )).unwrap();
        assert!(out.contains("#![allow(unused)]"));
        assert!(out.contains("#![feature(test_feature)]"));
    }

    #[test]
    fn user_provided_main_not_double_wrapped() {
        let out = process_doctest(&dc(
            "",
            "use std::fmt;\nfn main() {\n    println!(\"hello\");\n}",
            None,
        )).unwrap();
        assert_eq!(out.matches("fn main()").count(), 1);
        assert!(out.contains("use std::fmt;"));
    }

    #[test]
    fn deny_warnings_removed() {
        let out = process_doctest(&dc(
            "#![deny(warnings)]\n",
            "let x = 1;",
            simple_wrapper(),
        )).unwrap();
        assert!(!out.contains("deny"));
    }

    #[test]
    fn deny_warnings_removed_with_other_attrs() {
        let out = process_doctest(&dc(
            "#![deny(warnings)]\n#![feature(test_feature)]\n\n",
            "let x = 1;",
            simple_wrapper(),
        )).unwrap();
        assert!(!out.contains("deny"));
        assert!(out.contains("#![feature(test_feature)]"));
    }

    #[test]
    fn stmt_expr_attributes_removed_when_sole_feature() {
        let out = process_doctest(&dc(
            "#![feature(stmt_expr_attributes)]\n\n",
            "let x = 1;",
            simple_wrapper(),
        )).unwrap();
        assert!(!out.contains("stmt_expr_attributes"));
        assert!(!out.contains("#![feature"));
    }

    #[test]
    fn stmt_expr_attributes_removed_but_others_kept() {
        let out = process_doctest(&dc(
            "#![feature(test_feature, stmt_expr_attributes)]\n\n",
            "let x = 1;",
            simple_wrapper(),
        )).unwrap();
        assert!(!out.contains("stmt_expr_attributes"));
        assert!(out.contains("#![feature(test_feature)]"));
    }

    #[test]
    fn no_wrapper_cleans_attrs() {
        let out = process_doctest(&dc(
            "#![deny(warnings)]\n#![feature(stmt_expr_attributes, coroutines)]\n\n",
            "fn main() {\n    let x = 1;\n}",
            None,
        )).unwrap();
        assert!(!out.contains("deny"));
        assert!(!out.contains("stmt_expr_attributes"));
        assert!(out.contains("#![feature(coroutines)]"));
    }

    #[test]
    fn returns_result_wrapper() {
        let out = process_doctest(&dc(
            "",
            "let x = \"123\".parse::<i32>()?;\nassert_eq!(x, 123);\nOk(())",
            result_wrapper(),
        )).unwrap();
        assert!(out.contains("_inner"));
        assert!(out.contains("Ok(())"));
    }

    #[test]
    fn unparseable_code_returns_none() {
        let out = process_doctest(&dc("", "this is not valid rust {{{", simple_wrapper()));
        assert!(out.is_none());
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
}
