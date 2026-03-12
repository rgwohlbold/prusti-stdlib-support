# prusti-stdlib

Browse Prusti verification results for the Rust standard library.

## Running the browser

```bash
docker build --build-arg DB_FILE=prusti-20260309-165527-9eba9fcdc.db -t prusti-browse .
docker run -p 8765:8765 prusti-browse
```

Then open http://localhost:8765.

Replace the `DB_FILE` value with whichever `prusti-*.db` file you want to serve.
