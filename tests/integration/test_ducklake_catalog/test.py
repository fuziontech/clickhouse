import os
import tarfile

import pytest

from helpers.client import QueryRuntimeException
from helpers.cluster import ClickHouseCluster, get_docker_compose_path, run_and_check

# Fixtures in data/ were generated once with DuckDB 1.5.x + the ducklake extension
# (ATTACH 'ducklake:sqlite:catalog.db' with DATA_INLINING_ROW_LIMIT 0), then data_path
# was rewritten to the user_files location. No DuckDB dependency is needed at test time.
# catalog.sql is a PostgreSQL dump of the same catalog (catalog.db is used for sqlite).
#
# catalog2.db / catalog2.sql (loaded into a separate postgres database "ducklake2") were
# generated the same way with DATA_INLINING_ROW_LIMIT 10 and cover data inlining
# (inserts, deletes, updates, schema evolution, nested types) and partitioning;
# ducklake_data2/ holds the data files for both.

cluster = ClickHouseCluster(__file__)
node = cluster.add_instance("node", stay_alive=True)

cluster.base_cmd.extend(
    ["--file", os.path.join(get_docker_compose_path(), "docker_compose_postgres.yml")]
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "data")


def create_sqlite_db():
    node.query("DROP DATABASE IF EXISTS ducklake_sqlite SYNC")
    node.query(
        "CREATE DATABASE ducklake_sqlite ENGINE = DataLakeCatalog('ducklake')"
        " SETTINGS catalog_type = 'ducklake', ducklake_backend = 'sqlite',"
        " ducklake_connection_string = 'catalog.db';",
        settings={"allow_experimental_database_ducklake_catalog": 1},
    )


def create_postgres_db():
    node.query("DROP DATABASE IF EXISTS ducklake_pg SYNC")
    # the postgres compose fixture uses trust auth, so no password is needed (and the
    # test-only SensitiveDataMasker rule forbids one in query text)
    node.query(
        "CREATE DATABASE ducklake_pg ENGINE = DataLakeCatalog('ducklake')"
        " SETTINGS catalog_type = 'ducklake', ducklake_backend = 'postgres',"
        " ducklake_connection_string = 'host=postgres1 port=5432 dbname=postgres user=postgres';",
        settings={"allow_experimental_database_ducklake_catalog": 1},
    )


def create_sqlite2_db():
    node.query("DROP DATABASE IF EXISTS ducklake_sqlite2 SYNC")
    node.query(
        "CREATE DATABASE ducklake_sqlite2 ENGINE = DataLakeCatalog('ducklake')"
        " SETTINGS catalog_type = 'ducklake', ducklake_backend = 'sqlite',"
        " ducklake_connection_string = 'catalog2.db';",
        settings={"allow_experimental_database_ducklake_catalog": 1},
    )


def create_postgres2_db():
    node.query("DROP DATABASE IF EXISTS ducklake_pg2 SYNC")
    node.query(
        "CREATE DATABASE ducklake_pg2 ENGINE = DataLakeCatalog('ducklake')"
        " SETTINGS catalog_type = 'ducklake', ducklake_backend = 'postgres',"
        " ducklake_connection_string = 'host=postgres1 port=5432 dbname=ducklake2 user=postgres';",
        settings={"allow_experimental_database_ducklake_catalog": 1},
    )


def create_sqlite3_db():
    node.query("DROP DATABASE IF EXISTS ducklake_sqlite3 SYNC")
    node.query(
        "CREATE DATABASE ducklake_sqlite3 ENGINE = DataLakeCatalog('ducklake')"
        " SETTINGS catalog_type = 'ducklake', ducklake_backend = 'sqlite',"
        " ducklake_connection_string = 'catalog3.db';",
        settings={"allow_experimental_database_ducklake_catalog": 1},
    )


def create_postgres3_db():
    node.query("DROP DATABASE IF EXISTS ducklake_pg3 SYNC")
    node.query(
        "CREATE DATABASE ducklake_pg3 ENGINE = DataLakeCatalog('ducklake')"
        " SETTINGS catalog_type = 'ducklake', ducklake_backend = 'postgres',"
        " ducklake_connection_string = 'host=postgres1 port=5432 dbname=ducklake3 user=postgres';",
        settings={"allow_experimental_database_ducklake_catalog": 1},
    )


def copy_dir_to_container(instance, src_dir, dest_dir):
    """copy_file_to_container handles files only; ship a tarball instead."""
    tar_path = os.path.join(
        cluster.instances_dir, instance.name, os.path.basename(src_dir) + ".tar.gz"
    )
    os.makedirs(os.path.dirname(tar_path), exist_ok=True)
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(src_dir, arcname=os.path.basename(src_dir))
    instance.copy_file_to_container(tar_path, "/tmp/data.tar.gz")
    instance.exec_in_container(
        ["bash", "-c", f"mkdir -p {os.path.dirname(dest_dir)} && tar -xzf /tmp/data.tar.gz -C {os.path.dirname(dest_dir)}"]
    )


@pytest.fixture(scope="module")
def started_cluster():
    cluster.start()
    try:
        for data_dir in ("ducklake_data", "ducklake_data2", "ducklake_data3"):
            for instance in (node, node2):
                copy_dir_to_container(
                    instance,
                    os.path.join(FIXTURES_DIR, data_dir),
                    f"/var/lib/clickhouse/user_files/{data_dir}",
                )
        # the .db files are too large for copy_file_to_container (argv limit), tar them
        tar_path = os.path.join(cluster.instances_dir, node.name, "catalogs.tar.gz")
        os.makedirs(os.path.dirname(tar_path), exist_ok=True)
        with tarfile.open(tar_path, "w:gz") as tar:
            for db_file in ("catalog.db", "catalog2.db", "catalog3.db", "catalog_inlined.db", "catalog_badversion.db"):
                tar.add(os.path.join(FIXTURES_DIR, db_file), arcname=db_file)
        node.copy_file_to_container(tar_path, "/tmp/catalogs.tar.gz")
        node.exec_in_container(
            ["bash", "-c", "tar -xzf /tmp/catalogs.tar.gz -C /var/lib/clickhouse/user_files"]
        )
        node2.copy_file_to_container(tar_path, "/tmp/catalogs.tar.gz")
        node2.exec_in_container(
            ["bash", "-c", "tar -xzf /tmp/catalogs.tar.gz -C /var/lib/clickhouse/user_files"]
        )

        postgres_container_id = cluster.get_instance_docker_id("postgres1")
        cluster.copy_file_to_container(
            postgres_container_id,
            os.path.join(FIXTURES_DIR, "catalog.sql"),
            "/tmp/catalog.sql",
        )
        run_and_check(
            [
                f"docker exec {postgres_container_id} psql -U postgres -d postgres -f /tmp/catalog.sql"
            ],
            shell=True,
        )
        # the second and third catalogs live in their own databases (same ducklake_* table names)
        for dump_name in ("catalog2.sql", "catalog3.sql"):
            cluster.copy_file_to_container(
                postgres_container_id,
                os.path.join(FIXTURES_DIR, dump_name),
                f"/tmp/{dump_name}",
            )
        for db_name in ("ducklake2", "ducklake3"):
            run_and_check(
                [
                    f"docker exec {postgres_container_id} psql -U postgres -c 'DROP DATABASE IF EXISTS {db_name}'"
                ],
                shell=True,
            )
            run_and_check(
                [
                    f"docker exec {postgres_container_id} psql -U postgres -c 'CREATE DATABASE {db_name}'"
                ],
                shell=True,
            )
            run_and_check(
                [
                    f"docker exec {postgres_container_id} psql -U postgres -d {db_name} -f /tmp/{db_name.replace('ducklake', 'catalog')}.sql"
                ],
                shell=True,
            )
        yield cluster
    finally:
        cluster.shutdown()


def run_checks(database):
    assert node.query("SHOW TABLES", database=database) == (
        "main.evolved\n"
        "main.nested\n"
        "main.plain\n"
        "main.types\n"
        "main.with_deletes\n"
    )

    assert (
        node.query("SELECT * FROM `main.plain` ORDER BY id", database=database)
        == "1\ta\n2\tb\n3\tc\n"
    )

    assert (
        node.query("SELECT * FROM `main.nested` ORDER BY id", database=database)
        == "1\t(1,'u')\t[1,2]\t{'a':1}\n2\t(2,'v')\t[3]\t{'b':2}\n"
    )

    # positional deletes: rows 2 and 3 are deleted
    assert (
        node.query("SELECT * FROM `main.with_deletes` ORDER BY id", database=database)
        == "1\ta\n4\td\n"
    )

    # schema evolution: added column is filled with defaults for old files,
    # renamed-then-dropped column is gone entirely
    assert (
        node.query("DESC `main.evolved`", database=database)
        == "id\tNullable(Int32)\t\t\t\t\t\nextra\tNullable(Float64)\t\t\t\t\t\n"
    )
    assert (
        node.query("SELECT * FROM `main.evolved` ORDER BY id", database=database)
        == "1\t\\N\n2\t\\N\n3\t1.5\n4\t2.5\n5\t3.5\n"
    )

    assert (
        node.query(
            "SELECT b, i8, i16, i32, i64, h, u8, u16, u32, u64, f32, f64, d, vc, bl, dt, tm, ts, tstz, u "
            "FROM `main.types`",
            database=database,
        )
        == "1\t-1\t-2\t-3\t-4\t12345\t1\t2\t3\t4\t1.5\t2.5\t12.34\tstr\tblobdata\t"
        "2024-01-15\t10:30:00\t2024-01-15 10:30:00.000000\t2024-01-15 10:30:00.000000\t"
        "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11\n"
    )


def run_checks2(database):
    assert node.query("SHOW TABLES", database=database) == (
        "main.inlined_evolved\n"
        "main.inlined_mixed\n"
        "main.inlined_nested\n"
        "main.inlined_types\n"
        "main.partitioned\n"
        "main.partitioned_cal\n"
    )

    # file with 100 rows minus 3 inlined deletions, plus 5 inlined inserts of which
    # one deleted and two updated
    assert (
        node.query("SELECT count() FROM `main.inlined_mixed`", database=database) == "101\n"
    )
    assert (
        node.query(
            "SELECT * FROM `main.inlined_mixed` WHERE id >= 1000 ORDER BY id",
            database=database,
        )
        == "1000\tinl0\n1001\tupdated\n1003\tupdated\n1004\tinl4\n"
    )
    assert (
        node.query(
            "SELECT count() FROM `main.inlined_mixed` WHERE id IN (3, 17, 42)",
            database=database,
        )
        == "0\n"
    )
    assert (
        node.query(
            "SELECT * FROM `main.inlined_mixed` WHERE v = 'updated' ORDER BY id",
            database=database,
        )
        == "1001\tupdated\n1003\tupdated\n"
    )

    assert (
        node.query(
            "SELECT b, i8, i16, i32, i64, h, u8, u16, u32, u64, f32, f64, d, vc, bl, dt, tm, ts, tstz, u "
            "FROM `main.inlined_types` ORDER BY i32",
            database=database,
        )
        == "1\t-1\t-2\t-3\t-4\t12345\t1\t2\t3\t4\t1.5\t2.5\t12.34\tstr\tblobdata\t"
        "2024-01-15\t10:30:00\t2024-01-15 10:30:00.000000\t2024-01-15 10:30:00.000000\t"
        "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11\n"
        "0\t1\t2\t3\t4\t-12345\t5\t6\t7\t8\t-1.5\t-2.5\t-12.34\tweird \\'quote\t\\0ff\t"
        "2025-02-16\t11:31:01\t2025-02-16 11:31:01.123456\t2025-02-16 11:31:01.123456\t"
        "b1ffbc99-9c0b-4ef8-bb6d-6bb9bd380a22\n"
    )

    # NULL nested values become default tuples/arrays/maps, like in the Parquet reader
    assert (
        node.query("SELECT * FROM `main.inlined_nested` ORDER BY id", database=database)
        == "1\t(1,'u')\t[1,2]\t{'a':1}\n2\t(2,'v w')\t[3]\t{'b':2,'c':3}\n3\t(NULL,NULL)\t[]\t{}\n"
    )
    assert (
        node.query("SELECT s.x, s.y FROM `main.inlined_nested` WHERE id = 1", database=database)
        == "1\tu\n"
    )
    assert (
        node.query("SELECT m['b'] FROM `main.inlined_nested` WHERE id = 2", database=database)
        == "2\n"
    )

    # inlined rows from before an ADD COLUMN get defaults; rows from before a RENAME
    # are tracked via the column history
    assert (
        node.query("SELECT * FROM `main.inlined_evolved` ORDER BY id", database=database)
        == "1\tone\t\\N\n2\ttwo\t\\N\n3\tthree\t3.5\n4\tfour\t4.5\n5\tfive\t5.5\n"
    )

    # partitioned table: correctness of filtered reads
    assert node.query("SELECT count() FROM `main.partitioned`", database=database) == "120\n"
    assert node.query(
        "SELECT count() FROM `main.partitioned` WHERE region = 'a'", database=database
    ) == "40\n"
    assert node.query(
        "SELECT count() FROM `main.partitioned` WHERE id < 10", database=database
    ) == "10\n"
    assert node.query(
        "SELECT count() FROM `main.partitioned` WHERE dt >= '2024-01-01' AND dt < '2024-06-01'",
        database=database,
    ) == "60\n"
    assert node.query(
        "SELECT count() FROM `main.partitioned` WHERE region = 'b' AND year(dt) = 2024",
        database=database,
    ) == "20\n"

    # calendar-partitioned (year/month/day) table: exact bucket pruning correctness
    assert node.query("SELECT count() FROM `main.partitioned_cal`", database=database) == "36\n"
    assert node.query(
        "SELECT count() FROM `main.partitioned_cal` WHERE ts >= '2024-06-01 00:00:00' AND ts < '2024-06-02 00:00:00'",
        database=database,
    ) == "2\n"
    assert node.query(
        "SELECT count() FROM `main.partitioned_cal` WHERE ts < '2024-01-01 00:00:00'",
        database=database,
    ) == "8\n"
    assert node.query(
        "SELECT count() FROM `main.partitioned_cal` WHERE toMonth(ts) = 6",
        database=database,
    ) == "8\n"
    assert node.query(
        "SELECT count() FROM `main.partitioned_cal` WHERE toYear(ts) = 2024 AND toMonth(ts) = 6",
        database=database,
    ) == "4\n"


def run_checks3(database):
    # one normal file (region=aa, written with field ids), one name-mapped file
    # (region=bb, added via ducklake_add_data_files without field ids; region comes
    # from the hive path), one normal file written after a RENAME + ADD COLUMN
    assert node.query("SHOW TABLES", database=database) == "main.mapped\n"
    assert (
        node.query("SELECT id, title, region, s, l, m, extra FROM `main.mapped` ORDER BY id", database=database)
        == "1\tone\taa\t(1,'u')\t[1]\t{'a':1}\t\\N\n"
        "2\ttwo\taa\t(2,'v')\t[2]\t{'b':2}\t\\N\n"
        "3\tthree\tbb\t(3,'w')\t[3]\t{'c':3}\t\\N\n"
        "4\tfour\tbb\t(4,'x')\t[4]\t{'d':4}\t\\N\n"
        "5\tfive\tcc\t(5,'z')\t[5]\t{'e':5}\t5.5\n"
    )
    # filter on a hive partition column (constant from the catalog, applied post-read)
    assert (
        node.query("SELECT count() FROM `main.mapped` WHERE region = 'bb'", database=database) == "2\n"
    )
    # the name mapping resolves the old file's 'name' column to the renamed 'title'
    assert (
        node.query("SELECT id FROM `main.mapped` WHERE title = 'three'", database=database) == "3\n"
    )
    # subcolumns of name-mapped nested columns
    assert (
        node.query("SELECT id, s.x, s.y FROM `main.mapped` WHERE id = 3", database=database) == "3\t3\tw\n"
    )
    assert (
        node.query("SELECT count() FROM `main.mapped`", database=database) == "5\n"
    )


def test_ducklake_sqlite(started_cluster):
    create_sqlite_db()
    run_checks("ducklake_sqlite")


def test_ducklake_postgres(started_cluster):
    create_postgres_db()
    run_checks("ducklake_pg")


def test_ducklake_sqlite2(started_cluster):
    create_sqlite2_db()
    run_checks2("ducklake_sqlite2")


def test_ducklake_postgres2(started_cluster):
    create_postgres2_db()
    run_checks2("ducklake_pg2")


def test_ducklake_sqlite3(started_cluster):
    create_sqlite3_db()
    run_checks3("ducklake_sqlite3")


def test_ducklake_postgres3(started_cluster):
    create_postgres3_db()
    run_checks3("ducklake_pg3")


def test_pruning(started_cluster):
    create_sqlite2_db()
    # effectiveness of file pruning is observable in the server log
    node.query("SELECT count() FROM `main.partitioned` WHERE id < 10", database="ducklake_sqlite2")
    assert node.grep_in_log("DuckLake: pruned 5 of 6 files")
    node.query("SELECT count() FROM `main.partitioned` WHERE region = 'a'", database="ducklake_sqlite2")
    assert node.grep_in_log("DuckLake: pruned 4 of 6 files")
    node.query("SELECT count() FROM `main.partitioned` WHERE region = 'zzz'", database="ducklake_sqlite2")
    assert node.grep_in_log("DuckLake: pruned 6 of 6 files")
    # calendar buckets: exact single-day, upper-bound-only, and function-form pruning
    node.query(
        "SELECT count() FROM `main.partitioned_cal` WHERE ts >= '2024-06-01 00:00:00' AND ts < '2024-06-02 00:00:00'",
        database="ducklake_sqlite2",
    )
    assert node.grep_in_log("DuckLake: pruned 17 of 18 files")
    node.query("SELECT count() FROM `main.partitioned_cal` WHERE ts < '2024-01-01 00:00:00'", database="ducklake_sqlite2")
    assert node.grep_in_log("DuckLake: pruned 14 of 18 files")
    node.query("SELECT count() FROM `main.partitioned_cal` WHERE toMonth(ts) = 6", database="ducklake_sqlite2")
    assert node.grep_in_log("DuckLake: pruned 14 of 18 files")


def test_requires_experimental_setting(started_cluster):
    node.query("DROP DATABASE IF EXISTS ducklake_no_setting SYNC")
    with pytest.raises(QueryRuntimeException, match="allow_experimental_database_ducklake_catalog"):
        node.query(
            "CREATE DATABASE ducklake_no_setting ENGINE = DataLakeCatalog('ducklake')"
            " SETTINGS catalog_type = 'ducklake', ducklake_backend = 'sqlite',"
            " ducklake_connection_string = 'catalog.db';"
        )


def test_requires_connection_string(started_cluster):
    node.query("DROP DATABASE IF EXISTS ducklake_no_conn SYNC")
    with pytest.raises(QueryRuntimeException, match="ducklake_connection_string"):
        node.query(
            "CREATE DATABASE ducklake_no_conn ENGINE = DataLakeCatalog('ducklake')"
            " SETTINGS catalog_type = 'ducklake', ducklake_backend = 'sqlite';",
            settings={"allow_experimental_database_ducklake_catalog": 1},
        )


def test_inlined_data(started_cluster):
    node.query("DROP DATABASE IF EXISTS ducklake_inlined SYNC")
    node.query(
        "CREATE DATABASE ducklake_inlined ENGINE = DataLakeCatalog('ducklake')"
        " SETTINGS catalog_type = 'ducklake', ducklake_backend = 'sqlite',"
        " ducklake_connection_string = 'catalog_inlined.db';",
        settings={"allow_experimental_database_ducklake_catalog": 1},
    )
    # the whole table lives in the catalog (no data files at all)
    assert node.query("SELECT * FROM `main.inl`", database="ducklake_inlined") == "1\ta\n"
    assert node.query("SELECT * FROM `main.inl` WHERE id = 2", database="ducklake_inlined") == ""


def test_unsupported_catalog_version(started_cluster):
    node.query("DROP DATABASE IF EXISTS ducklake_badversion SYNC")
    with pytest.raises(QueryRuntimeException, match="schema version"):
        node.query(
            "CREATE DATABASE ducklake_badversion ENGINE = DataLakeCatalog('ducklake')"
            " SETTINGS catalog_type = 'ducklake', ducklake_backend = 'sqlite',"
            " ducklake_connection_string = 'catalog_badversion.db';",
            settings={"allow_experimental_database_ducklake_catalog": 1},
        )


def test_ducklake_read_during_concurrent_commits(started_cluster):
    """A query must observe one consistent catalog snapshot even while another writer
    commits (compaction/flush advancing ducklake_snapshot). The catalog read now runs
    inside one REPEATABLE READ transaction (DuckLakeCatalog.beginSnapshotRead); before
    that, a commit landing between the listing's autocommit statements aborted the query
    with "DuckLake catalog changed while reading table metadata" whenever the table had
    an inlined delete table, and a flush physically removing inlined rows mid-read could
    resurrect deleted rows / lose flushed ones.

    The inlined delete table below is fabricated to match DuckDB's
    ducklake_inlined_delete_<table_id> shape; the old failure mode only engaged when it
    existed. The mutator advances the snapshot and churns the inlined delete table
    without touching data files, so the visible row count must stay constant throughout.
    This test is a stress check (it cannot deterministically place a commit between two
    reader statements), so it asserts the property that must hold for ANY interleaving:
    reads never fail and never observe a torn snapshot.
    """
    import threading

    create_postgres_db()
    db = "ducklake_pg"
    base = int(node.query("SELECT count() FROM `main.plain`", database=db))

    postgres_container_id = cluster.get_instance_docker_id("postgres1")

    def psql(sql):
        run_and_check(
            [f'docker exec {postgres_container_id} psql -U postgres -d postgres -v ON_ERROR_STOP=1 -c "{sql}"'],
            shell=True,
        )

    table_id = int(
        run_and_check(
            [
                f"docker exec {postgres_container_id} psql -U postgres -d postgres -t -A -c "
                f"\"SELECT table_id FROM ducklake_table WHERE table_name = 'plain' AND end_snapshot IS NULL\""
            ],
            shell=True,
        ).strip()
    )
    psql(
        f"CREATE TABLE IF NOT EXISTS ducklake_inlined_delete_{table_id} "
        f"(file_id BIGINT, row_id BIGINT, begin_snapshot BIGINT)"
    )

    errors = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                count = int(node.query("SELECT count() FROM `main.plain`", database=db))
                assert count == base, f"torn snapshot observed: count {count} != {base}"
            except Exception as e:
                errors.append(e)

    def mutator():
        # Each commit advances the snapshot and churns the inlined delete table, like a
        # delete flush does (physically removing rows a reader at the old snapshot needs).
        for i in range(60):
            psql(
                f"WITH s AS (SELECT MAX(snapshot_id) + 1 AS next_id, MAX(schema_version) AS sv, "
                f"MAX(next_catalog_id) AS nc, MAX(next_file_id) AS nf FROM ducklake_snapshot) "
                f"INSERT INTO ducklake_snapshot SELECT next_id, now(), sv, nc, nf FROM s"
            )
            psql(
                f"INSERT INTO ducklake_inlined_delete_{table_id} VALUES (0, {i}, 0); "
                f"DELETE FROM ducklake_inlined_delete_{table_id}"
            )

    readers = [threading.Thread(target=reader) for _ in range(3)]
    writer = threading.Thread(target=mutator)
    for thread in readers:
        thread.start()
    writer.start()
    writer.join()
    stop.set()
    for thread in readers:
        thread.join()

    assert not errors, f"read failures during concurrent commits: {errors[:3]}"
    assert node.query("SELECT count() FROM `main.plain`", database=db) == f"{base}\n"


def test_ducklake_snapshot_id_time_travel(started_cluster):
    """ducklake_snapshot_id pins the catalog snapshot explicitly (the same code path a
    parallel-replicas secondary uses to follow the initiator's pinned snapshot): reading
    main.evolved at an older snapshot must return only the files visible then (2 rows at
    snapshot 9, 5 at the latest snapshot 17)."""
    create_postgres_db()
    db = "ducklake_pg"

    assert node.query("SELECT count() FROM `main.evolved`", database=db) == "5\n"
    assert (
        node.query(
            "SELECT count() FROM `main.evolved`",
            database=db,
            settings={"ducklake_snapshot_id": 9},
        )
        == "2\n"
    )
    assert (
        node.query(
            "SELECT count() FROM `main.evolved`",
            database=db,
            settings={"ducklake_snapshot_id": 11},
        )
        == "3\n"
    )


def test_ducklake_snapshot_id_propagation_stamp(started_cluster):
    """After reading a DuckLake table without an explicit snapshot, the pinned
    ducklake_snapshot_id is stamped into the query settings (logged at pin time) — that
    stamp is what a parallel-replicas query ships to secondaries so all nodes read one
    catalog snapshot."""
    create_postgres_db()
    node.query("SELECT count() FROM `main.plain`", database="ducklake_pg")
    assert node.grep_in_log("DuckLake: pinned catalog snapshot")


PARALLEL_REPLICAS_SETTINGS = {
    "allow_experimental_parallel_reading_from_replicas": 1,
    "parallel_replicas_for_cluster_engines": 1,
    "cluster_for_parallel_replicas": "ducklake_cluster",
    "max_parallel_replicas": 2,
}


def create_postgres_db_on(instance):
    instance.query("DROP DATABASE IF EXISTS ducklake_pg SYNC")
    instance.query(
        "CREATE DATABASE ducklake_pg ENGINE = DataLakeCatalog('ducklake')"
        " SETTINGS catalog_type = 'ducklake', ducklake_backend = 'postgres',"
        " ducklake_connection_string = 'host=postgres1 port=5432 dbname=postgres user=postgres';",
        settings={"allow_experimental_database_ducklake_catalog": 1},
    )


def create_postgres2_db_on(instance):
    instance.query("DROP DATABASE IF EXISTS ducklake_pg2 SYNC")
    instance.query(
        "CREATE DATABASE ducklake_pg2 ENGINE = DataLakeCatalog('ducklake')"
        " SETTINGS catalog_type = 'ducklake', ducklake_backend = 'postgres',"
        " ducklake_connection_string = 'host=postgres1 port=5432 dbname=ducklake2 user=postgres';",
        settings={"allow_experimental_database_ducklake_catalog": 1},
    )


def create_postgres3_db_on(instance):
    instance.query("DROP DATABASE IF EXISTS ducklake_pg3 SYNC")
    instance.query(
        "CREATE DATABASE ducklake_pg3 ENGINE = DataLakeCatalog('ducklake')"
        " SETTINGS catalog_type = 'ducklake', ducklake_backend = 'postgres',"
        " ducklake_connection_string = 'host=postgres1 port=5432 dbname=ducklake3 user=postgres';",
        settings={"allow_experimental_database_ducklake_catalog": 1},
    )


def create_parallel_fresh_db_on(instance):
    """A pristine copy of the pg fixture catalog; both nodes can read every file it lists."""
    postgres_container_id = cluster.get_instance_docker_id("postgres1")
    run_and_check(
        [f"docker exec {postgres_container_id} psql -U postgres -c 'DROP DATABASE IF EXISTS ducklake_parallel WITH (FORCE)'"],
        shell=True,
    )
    run_and_check(
        [f"docker exec {postgres_container_id} psql -U postgres -c 'CREATE DATABASE ducklake_parallel'"],
        shell=True,
    )
    run_and_check(
        [f"docker exec {postgres_container_id} psql -U postgres -d ducklake_parallel -f /tmp/catalog.sql"],
        shell=True,
    )
    instance.query("DROP DATABASE IF EXISTS ducklake_par SYNC")
    instance.query(
        "CREATE DATABASE ducklake_par ENGINE = DataLakeCatalog('ducklake')"
        " SETTINGS catalog_type = 'ducklake', ducklake_backend = 'postgres',"
        " ducklake_connection_string = 'host=postgres1 port=5432 dbname=ducklake_parallel user=postgres';",
        settings={"allow_experimental_database_ducklake_catalog": 1},
    )


def test_ducklake_parallel_replicas(started_cluster):
    """A parallel-replicas query over a DuckLake table returns exactly the single-node
    result: the initiator pins one catalog snapshot (ducklake_snapshot_id propagates with
    the query) and every task carries the file's full read state (positional delete files,
    inlined deletions, per-file name mapping, partition constants) to the secondary."""
    for instance in (node, node2):
        create_parallel_fresh_db_on(instance)
        create_postgres3_db_on(instance)

    single_node_deletes = node.query("SELECT * FROM `main.with_deletes` ORDER BY id", database="ducklake_par")
    single_node_mapped = node.query(
        "SELECT id, title, region, s, l, m, extra FROM `main.mapped` ORDER BY id", database="ducklake_pg3"
    )
    single_node_plain = node.query("SELECT count(), sum(id) FROM `main.plain`", database="ducklake_par")

    # positional deletes must hold on the secondary (delete files ride the task)
    assert (
        node.query(
            "SELECT * FROM `main.with_deletes` ORDER BY id",
            database="ducklake_par",
            settings=PARALLEL_REPLICAS_SETTINGS,
        )
        == single_node_deletes
    )
    assert single_node_deletes == "1\ta\n4\td\n"

    # name-mapped files + catalog-side partition constants must survive the task round-trip
    assert (
        node.query(
            "SELECT id, title, region, s, l, m, extra FROM `main.mapped` ORDER BY id",
            database="ducklake_pg3",
            settings=PARALLEL_REPLICAS_SETTINGS,
        )
        == single_node_mapped
    )

    # aggregate over a table both nodes must split
    assert (
        node.query(
            "SELECT count(), sum(id) FROM `main.plain`",
            database="ducklake_par",
            settings=PARALLEL_REPLICAS_SETTINGS,
        )
        == single_node_plain
    )

    # evidence the secondary actually participated (only this test queries mapped on node2)
    assert node2.grep_in_log("main.mapped")


def test_ducklake_parallel_replicas_under_concurrent_commits(started_cluster):
    """Under catalog commits, a parallel-replicas query must observe one consistent
    snapshot across both nodes (the initiator's pinned ducklake_snapshot_id), never
    failing with a changed-catalog error and never returning a torn count."""
    import threading

    for instance in (node, node2):
        create_parallel_fresh_db_on(instance)

    base = int(node.query("SELECT count() FROM `main.plain`", database="ducklake_par"))

    postgres_container_id = cluster.get_instance_docker_id("postgres1")

    def psql(sql):
        run_and_check(
            [f'docker exec {postgres_container_id} psql -U postgres -d ducklake_parallel -v ON_ERROR_STOP=1 -c "{sql}"'],
            shell=True,
        )

    errors = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                count = int(
                    node.query(
                        "SELECT count() FROM `main.plain`",
                        database="ducklake_par",
                        settings=PARALLEL_REPLICAS_SETTINGS,
                    )
                )
                assert count == base, f"torn snapshot observed: count {count} != {base}"
            except Exception as e:
                errors.append(e)

    def mutator():
        for _ in range(40):
            psql(
                "WITH s AS (SELECT MAX(snapshot_id) + 1 AS next_id, MAX(schema_version) AS sv, "
                "MAX(next_catalog_id) AS nc, MAX(next_file_id) AS nf FROM ducklake_snapshot) "
                "INSERT INTO ducklake_snapshot SELECT next_id, now(), sv, nc, nf FROM s"
            )

    readers = [threading.Thread(target=reader) for _ in range(2)]
    writer = threading.Thread(target=mutator)
    for thread in readers:
        thread.start()
    writer.start()
    writer.join()
    stop.set()
    for thread in readers:
        thread.join()

    assert not errors, f"parallel-replicas read failures during concurrent commits: {errors[:3]}"


def test_ducklake_count_from_metadata(started_cluster):
    """Unfiltered count(*) is answered from the catalog listing (exact record/delete
    counts at the pinned snapshot) instead of opening every file's footer. The answer
    must match the read pipeline exactly, including positional deletes, inlined
    deletions, and rows inlined in the catalog."""
    # a pristine fixture copy: the write tests mutate ducklake_pg.main.plain and can
    # race this test under xdist. ducklake_pg2 is never written by any test.
    create_parallel_fresh_db_on(node)
    create_postgres2_db()

    # positional deletes: 4 file rows minus 2 deleted
    assert node.query("SELECT count() FROM `main.with_deletes`", database="ducklake_par") == "2\n"
    # 100 file rows, 3 inlined deletions, 5 inlined inserts (one of them deleted)
    assert node.query("SELECT count() FROM `main.inlined_mixed`", database="ducklake_pg2") == "101\n"
    assert node.query("SELECT count() FROM `main.plain`", database="ducklake_par") == "3\n"
    # schema evolution: 5 rows across files written at different schema versions
    assert node.query("SELECT count() FROM `main.evolved`", database="ducklake_par") == "5\n"
    # filtered count takes the generic path
    assert (
        node.query("SELECT count() FROM `main.plain` WHERE id = 1", database="ducklake_par") == "1\n"
    )
    # the metadata path was taken (logged at the read step)
    assert node.grep_in_log("Answering count from catalog metadata")

    # distributed counts also skip footer reads: each assigned file's count comes
    # from the catalog record/delete counts carried in the task
    for instance in (node, node2):
        create_parallel_fresh_db_on(instance)
    assert (
        node.query(
            "SELECT count() FROM `main.with_deletes`",
            database="ducklake_par",
            settings=PARALLEL_REPLICAS_SETTINGS,
        )
        == "2\n"
    )
    assert node2.grep_in_log("from catalog record count") or node.grep_in_log("from catalog record count")


def test_ducklake_count_from_metadata_parallel_replicas(started_cluster):
    """The metadata count path engages on the parallel-replicas initiator and returns
    the same answer as the single-node count (the initiator's pinned snapshot)."""
    for instance in (node, node2):
        create_parallel_fresh_db_on(instance)

    single = node.query("SELECT count() FROM `main.plain`", database="ducklake_par")
    assert (
        node.query(
            "SELECT count() FROM `main.plain`",
            database="ducklake_par",
            settings=PARALLEL_REPLICAS_SETTINGS,
        )
        == single
    )
    assert single == "3\n"


def test_ducklake_parallel_replicas_inlined_data(started_cluster):
    """Inlined data rows (catalog-stored, not yet flushed) must be read by EXACTLY ONE
    replica: they are emitted as synthetic tasks. Before this, every replica added the
    inlined-data pipe and the rows were duplicated (101 -> 202)."""
    for instance in (node, node2):
        create_postgres2_db_on(instance)

    # 100 file rows, 3 inlined deletions, 5 inlined inserts (one of them deleted)
    expected = node.query("SELECT count() FROM `main.inlined_mixed`", database="ducklake_pg2")
    assert expected == "101\n"
    assert (
        node.query(
            "SELECT count() FROM `main.inlined_mixed`",
            database="ducklake_pg2",
            settings=PARALLEL_REPLICAS_SETTINGS,
        )
        == "101\n"
    )
    # and the inlined rows themselves are right, not just the count
    expected_rows = node.query(
        "SELECT * FROM `main.inlined_mixed` WHERE id >= 1000 ORDER BY id", database="ducklake_pg2"
    )
    assert (
        node.query(
            "SELECT * FROM `main.inlined_mixed` WHERE id >= 1000 ORDER BY id",
            database="ducklake_pg2",
            settings=PARALLEL_REPLICAS_SETTINGS,
        )
        == expected_rows
    )


def test_ducklake_postgres_password_env(started_cluster):
    # password_env=<VAR> in ducklake_connection_string substitutes the named env var's
    # value as the libpq password at connect time, keeping secrets out of the DDL (the
    # compose postgres uses trust auth, so any substituted value authenticates; HOSTNAME
    # is always set in the server container). A missing variable must fail the attach
    # loudly rather than retry doomed auth.
    node.query("DROP DATABASE IF EXISTS ducklake_pg_pwenv SYNC")
    node.query(
        "CREATE DATABASE ducklake_pg_pwenv ENGINE = DataLakeCatalog('ducklake')"
        " SETTINGS catalog_type = 'ducklake', ducklake_backend = 'postgres',"
        " ducklake_connection_string = 'host=postgres1 port=5432 dbname=postgres user=postgres"
        " password_env=HOSTNAME';",
        settings={"allow_experimental_database_ducklake_catalog": 1},
    )
    assert (
        node.query("SELECT count() FROM `main.inlined_mixed`", database="ducklake_pg_pwenv")
        == "101\n"
    )
    node.query("DROP DATABASE ducklake_pg_pwenv SYNC")

    node.query("DROP DATABASE IF EXISTS ducklake_pg_pwenv SYNC")
    try:
        node.query(
            "CREATE DATABASE ducklake_pg_pwenv ENGINE = DataLakeCatalog('ducklake')"
            " SETTINGS catalog_type = 'ducklake', ducklake_backend = 'postgres',"
            " ducklake_connection_string = 'host=postgres1 port=5432 dbname=postgres user=postgres"
            " password_env=DUCKLAKE_DEFINITELY_MISSING_PASSWORD_VAR';",
            settings={"allow_experimental_database_ducklake_catalog": 1},
        )
        assert False, "attach with a missing password_env variable must fail"
    except QueryRuntimeException as e:
        assert "password_env" in str(e)
        assert "DUCKLAKE_DEFINITELY_MISSING_PASSWORD_VAR" in str(e)
