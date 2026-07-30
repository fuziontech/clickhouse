# Parquet VARIANT: what's next

This file tracks the remaining performance work on Parquet `VARIANT` read/write.
Context and results so far are in the PR description; numbers below are from an
aarch64 machine (192 cores), 3M-row bench files in `tmp/variant_test/bench/`.

## Current state (best-of-3/5, ms)

| Query | duckdb 1.5.5 | ClickHouse |
|---|---|---|
| full scan shredded | 10545 | ~4000 |
| full scan unshredded | 10015 | ~3600 |
| path groupby shredded | 1740 | 562 |
| path filter shredded | 1733 | 507 |
| path groupby unshredded | 1537 | 590 |
| path filter unshredded | 1534 | 514 |
| pruned groupby/filter (fully-shredded) | n/a (unsupported) | ~71 |

## Next up (ordered by expected win / effort)

- [ ] 1. Cache `DataTypeTuple` per object shape (~15-20% of full scans)
      Full-scan profile: 7.3% `DataTypeTuple` dtor + 5.1% ctor + 4.9% `getName` + 5.5%
      shared_ptr churn — a fresh `Tuple` type is built per row when inserting objects
      into `ColumnDynamic` (`shreddedValueToDecodedImpl` in `VariantDecoding.cpp`).
      Add a per-shape (sorted field names + element types) type cache, keyed per
      metadata/subtree, so identical object shapes share one `DataTypePtr`.
      Target: full scans ~4000ms -> ~3300ms.
- [ ] 2. Arena allocation for decoded values (~10%)
      `DecodedVariantValue` dtor is 5.9% of full-scan time plus allocator churn from
      per-row `Field` trees. Bulk-allocate decoded values per row subgroup and free
      them wholesale after insertion.
- [ ] 3. Parallel row-group decoding (largest ceiling, biggest change)
      The decoder is single-threaded per row group; DuckDB parallelizes scans
      internally. Multi-threaded row-group decode multiplies everything on many-core
      machines. (ReadManager already schedules column-chunk reads in parallel; the
      assembly in `Reader::formOutputColumn` / `formVariantColumn` is the serial part.)
- [ ] 4. Selective reads for plain JSON-annotated leaves
      `processSubcolumnFallback` still reads a whole String leaf and casts it to
      `JSON` before extracting subcolumns. The same selective idea applies:
      `LeanJSONParser` per row + path extraction, or a light projection pushdown into
      `SerializationObject`.
- [ ] 5. Value-side binary search for large objects
      `findVariantObjectFieldById` scans field ids linearly (with early exit when
      sorted). For very wide objects, binary search the sorted id list.

## Known limitations / notes

- `duckdb/duckdb` main cannot read value-less fully-shredded files ("must have
  'metadata' and 'value'") and its `variant_extract` on them assertion-fails;
  duckdb 1.5.5 cannot read them at all. ClickHouse handles them (this is the
  pruned path, ~71ms).
- DuckLake `variant` columns map to `Dynamic`, so SQL path subcolumns
  (`payload.event_type`) do not apply there; the pushdown machinery is exercised
  through `JSON`-typed columns (e.g. `file(..., 'payload JSON')`).
- Pre-existing aarch64 failures unrelated to this work: `04140_parquet_types_roundtrip`
  (column order), `03036_test_parquet_bloom_filter_push_down` (xxHash value) — both
  reproduced on unmodified base.

## Verification checklist (run after each item)

- Build: `ninja -C clickhouse/build clickhouse` (redirect output to a log in the build dir)
- Restart server: `./clickhouse/build/programs/clickhouse client -q "SYSTEM SHUTDOWN"` then
  `setsid nohup ./clickhouse/build/programs/clickhouse server --config-file=tmp/ch_server/config.xml &`
- `tests/clickhouse-test 04648_parquet_variant_read` (from repo root)
- Parquet stateless suite: `tests/clickhouse-test parquet`
- DuckLake gtests: `clickhouse/build/src/unit_tests_dbms --gtest_filter='*DuckLake*'`
- DuckLake e2e (no docker here; replicate `test.py::test_variant` manually):
  copy `tests/integration/test_ducklake_catalog/data/{catalog_variant.db,ducklake_data_variant}`
  to `/var/lib/clickhouse/user_files/`, `CREATE DATABASE ... DataLakeCatalog` with
  `ducklake_backend = 'sqlite'`, `ducklake_connection_string = 'catalog_variant.db'`,
  compare the two SELECTs against `test.py`'s expectations.
- Bench: compare against the table above.
