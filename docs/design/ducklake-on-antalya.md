# Running the DuckLake PRs on Altinity Antalya

Status: research plan (2026-08). Related PRs on fuziontech/clickhouse: #6 (read core), #7 (Parquet VARIANT), #8 (write core). Deploy skeleton: PostHog/charts#14353.

## Goal

Run the DuckLake integration (read core, optionally VARIANT and writes) on the Altinity `antalya` architecture — i.e. an `antalya-*` release branch of Altinity/ClickHouse — so the managed-warehouse deployment (charts#14353) rides the same ClickHouse build family PostHog already operates in prod (`26.3.10.20001.altinityantalya`), and can optionally exploit Antalya's swarm (stateless distributed object-storage reads).

## What Antalya is (survey of `antalya-26.3`, tip 2026-08-14)

`Antalya.md` at the repo root is the feature matrix. Antalya-26.3 tracks the upstream 26.3 LTS branch closely (merge-base `c57540de`, 2026-07-19). Deltas vs upstream, by area:

- **Swarms**: distributed execution of object-storage table functions/engines to stateless swarm nodes (`object_storage_cluster`, `StorageObjectStorageCluster`, stable task distribution, task restart on connection loss, swarm JOINs, `SYSTEM STOP SWARM MODE`). Cluster reads for catalog databases are already wired: `DatabaseDataLake` builds a `StorageObjectStorageCluster` per table and routes via the `object_storage_cluster` setting.
- **Catalogs**: Glue/BigLake/REST/Unity hardening, RBAC for S3/Glue, namespace filters, catalog DBs for distributed processing (upstream 88273), datalake catalogs enabled by default, `allow_local_data_lakes` default off.
- **Performance**: Parquet metadata cache v2, ListObjectsV2 cache, async Iceberg metadata prefetch, parallel object-storage output.
- **Tiered storage**: `Hybrid` engine, part export to Parquet/Iceberg.
- The Parquet v3 reader originated in Antalya and was upstreamed (25.11); the VARIANT PR sits on its direct descendant.

## Base-gap analysis: antalya-26.3 vs the PRs' base (upstream master ~26.8, `930f863`)

The three stacks are ~95% new files (DuckLakeCatalog, DuckLake/*, VariantEncoding/Decoding, DuckLakeWrites) that carry over nearly verbatim. The porting surface is the glue, and antalya-26.3 differs from the PR base in these glue areas:

| Glue area | 26.3 state | Port action |
|---|---|---|
| `StorageObjectStorageConfiguration` vs `DataLakes/DataLakeConfiguration.h` | pre-refactor monolith (upstream refactored after 26.3) | re-target configuration glue to the 26.3 layout (`getColumnMapperForObject` already exists there) |
| `StorageObjectStorageSource.cpp` filter stripping / fallback `FilterTransform`s | present (same lineage) | add the `force_post_read_filters` flag + one condition |
| `ReadFromObjectStorageStep` pipe union | present (`Pipe::unitePipes`) | add the `getAdditionalReadPipe` hook to `IDataLakeMetadata` + call site (inlined data) |
| `FormatFilterInfo` name mapping | absent | add `setStorageColumnNameMapping` (26.3 has no Iceberg string/optionality maps to preserve — simpler) |
| Parquet `SchemaConverter`/`Reader` | pre-GeoParquet (no spatial pushdown, no `current_schema_column_mapper`) | apply VARIANT/name-mapping changes onto the simpler reader — strictly less conflict surface than the master merge was |
| `IcebergOptionality` in `PrepareForWrite` | absent | write VARIANT groups as `REQUIRED` (consistent with how 26.3 writes every other complex type) |
| Experimental settings gating | no ducklake flag; antalya enables datalake catalogs by default | decide: keep `allow_experimental_database_ducklake_catalog` or follow antalya convention |
| Docs | `docs/en/interfaces/formats/Parquet/Parquet.md` still exists (pre-autogeneration move) | put VARIANT docs there |

## Porting plan

Phase order is chosen so each phase is independently deployable.

1. **Read core onto `antalya-26.3`** (largest effort): new DuckLake files verbatim; glue per the table above; integration tests (`test_ducklake_catalog`) come along unchanged. Build with the antalya toolchain (same clang-21/cmake flow), run the suite.
2. **VARIANT onto the port** (optional but cheap here): new Variant* files verbatim; reader/schema-converter changes apply more cleanly than they did on master (no GeoParquet interplay); add the DuckLake `variant` → `Dynamic` mapping in `DuckLakeTypes.cpp` (the bridge deliberately left out of the upstream-facing split).
3. **Write core onto the port** (optional): `DuckLakeWrites` + catalog transaction code nearly verbatim; `allow_insert_into_ducklake` gating.
4. **Swarm validation** (the reason to be on antalya): point the catalog DB's `object_storage_cluster` at a swarm and verify/extend:
   - *Snapshot consistency across nodes*: the initiator pins a snapshot; secondaries must list files for the same snapshot. Iceberg solves this by injecting `iceberg_metadata_file_path` into secondary queries; DuckLake needs the equivalent (propagate the pinned `snapshot_id` as a query setting into secondary queries).
   - *ObjectInfo fidelity*: `DuckLakeDataObjectInfo` carries positional deletes, name mappers, partition constants. If secondaries re-derive ObjectInfos from the catalog, every swarm node needs catalog reachability (Postgres conninfo + credentials); if tasks serialize ObjectInfos, those fields must be serialized. Pick after reading `StorageObjectStorageStableTaskDistributor`'s contract.
   - The atomic-increment iterator fix already matches antalya's swarm-era iterator locking (#1436).
   - Fallback: single-node antalya + DuckLake works without any of this.

## Build / image / deploy path

- Branch: `antalya-26.3-ducklake` on a fork with Altinity's packaging intact (`docker/packager` produces the `clickhouse-server` image and debs; Altinity CI conventions live in the repo).
- Image: build `clickhouse-server` from that branch, push to a registry the mw clusters can pull (ECR via posthog-cloud-infra, or GHCR), pin the digest in charts#14353 and flip `enabled: true`.
- Runtime config is already in the charts PR: IRSA-only S3 auth, `PGPASSWORD`-injected catalog credentials, startup-script catalog attach.

## Options and recommendation

- **A (recommended): single-node read-only port to antalya-26.3 first** — immediate value (ClickHouse SQL over megaduck in mw-prod-us), smallest diff, deployable as soon as the image exists.
- **B: A + VARIANT + write** — full parity with the upstream-facing stacks.
- **C: wait for `antalya-26.8`** — if the three PRs merge upstream before Altinity cuts the 26.8 line, the port becomes nearly free, at the cost of months.
- **D: swarm from day one** — only if interactive megaduck queries demonstrably need multi-node scan throughput; adds the snapshot-propagation work in phase 4.

## Effort estimate

- Phase 1: 2–4 focused days (mostly `DatabaseDataLake`/configuration glue + settings + tests).
- Phase 2: 1–2 days. Phase 3: ~1 day. Phase 4: 2–3 days including a mw-dev deploy.

## Open questions

1. Target `antalya-26.3` (PostHog's deployed line) or `antalya-26.4`?
2. Where does the fork + image CI live (fuziontech/clickhouse has no antalya packaging pipeline; a PostHog-owned fork of Altinity/ClickHouse with a packaging workflow is the clean option)?
3. Who operates the swarm if phase 4 happens (new pool in the mw cluster vs reuse)?

## Decisions (2026-08-15) and port status

Decided with the repo owner:

- **Target line: `antalya-26.4` or newer** (no need to match the prod-deployed 26.3 exactly).
- **Home: `PostHog/clickhouse`** (fork of Altinity/ClickHouse, created for this work). Images to GHCR unless PostHog ECR turns out easier.
- **Design for swarm from the start**, but swarm enablement does not have to land in the first PR.

Port progress:

- `port/ducklake-read-antalya-26.4` on PostHog/clickhouse: the full read-core stack cherry-picked onto `antalya-26.4` (9 commits) plus one adaptation commit (`b5ed66e`). Notable port work beyond plain conflict resolution:
  - backported the stripped-filter / fallback `FilterTransform` machinery into `StorageObjectStorageSource.cpp` (present in antalya-26.3, absent in the June 26.4 cut);
  - `ICatalog` interface on 26.4 is the older `DB::Names getTables()` shape;
  - 2-arg `ActionsDAGWithInversionPushDown`, no `ObjectInfo::getFileSizeHint`, no `ColumnConstPtr` alias, no `current_schema_column_mapper` on 26.4.
- Builds clean (clang-21, RelWithDebInfo); 13/13 DuckLake gtests pass; local smoke over the sqlite fixture catalog passes: table enumeration, plain/nested/types reads, positional deletes, schema evolution.
- Remaining before image: full docker integration suite (`test_ducklake_catalog`), then VARIANT + write stacks, then the swarm snapshot-propagation item from phase 4.

