-- Tags: no-parallel
-- no-parallel: access entities are server-global.

DROP USER IF EXISTS {CLICKHOUSE_DATABASE:Identifier}, {CLICKHOUSE_DATABASE_1:Identifier}, {CLICKHOUSE_DATABASE_2:Identifier};

CREATE USER {CLICKHOUSE_DATABASE:Identifier};
SELECT count() FROM system.users WHERE name = currentDatabase();
ALTER USER {CLICKHOUSE_DATABASE:Identifier} SETTINGS max_threads = 1;

GRANT SELECT ON *.* TO {CLICKHOUSE_DATABASE:Identifier};
SELECT count() FROM system.grants WHERE user_name = currentDatabase() AND access_type = 'SELECT';
REVOKE SELECT ON *.* FROM {CLICKHOUSE_DATABASE:Identifier};
SELECT count() FROM system.grants WHERE user_name = currentDatabase() AND access_type = 'SELECT';

CREATE USER {CLICKHOUSE_DATABASE_1:Identifier}, {CLICKHOUSE_DATABASE_2:Identifier};
GRANT SELECT ON *.* TO {CLICKHOUSE_DATABASE_1:Identifier}, {CLICKHOUSE_DATABASE_2:Identifier};
SELECT count() FROM system.grants WHERE user_name IN (currentDatabase() || '_1', currentDatabase() || '_2') AND access_type = 'SELECT';
REVOKE SELECT ON *.* FROM {CLICKHOUSE_DATABASE_1:Identifier}, {CLICKHOUSE_DATABASE_2:Identifier} EXCEPT {CLICKHOUSE_DATABASE_2:Identifier};
SELECT count() FROM system.grants WHERE user_name = currentDatabase() || '_1' AND access_type = 'SELECT';
SELECT count() FROM system.grants WHERE user_name = currentDatabase() || '_2' AND access_type = 'SELECT';
REVOKE SELECT ON *.* FROM {CLICKHOUSE_DATABASE_2:Identifier};
SELECT count() FROM system.grants WHERE user_name IN (currentDatabase() || '_1', currentDatabase() || '_2') AND access_type = 'SELECT';

SELECT count() FROM system.users WHERE name IN (currentDatabase(), currentDatabase() || '_1', currentDatabase() || '_2');
DROP USER {CLICKHOUSE_DATABASE:Identifier}, {CLICKHOUSE_DATABASE_1:Identifier}, {CLICKHOUSE_DATABASE_2:Identifier};
SELECT count() FROM system.users WHERE name IN (currentDatabase(), currentDatabase() || '_1', currentDatabase() || '_2');

CREATE USER {CLICKHOUSE_DATABASE:Identifier}@'192.168.%.%';
SELECT count() FROM system.users WHERE name = currentDatabase() || '@192.168.%.%';
GRANT SELECT ON *.* TO {CLICKHOUSE_DATABASE:Identifier}@'192.168.%.%';
SELECT count() FROM system.grants WHERE user_name = currentDatabase() || '@192.168.%.%' AND access_type = 'SELECT';
REVOKE SELECT ON *.* FROM {CLICKHOUSE_DATABASE:Identifier}@'192.168.%.%';
DROP USER {CLICKHOUSE_DATABASE:Identifier}@'192.168.%.%';
SELECT count() FROM system.users WHERE name = currentDatabase() || '@192.168.%.%';

GRANT SELECT ON *.* TO {nonexistent_param:Identifier}; -- { serverError UNKNOWN_QUERY_PARAMETER }
REVOKE SELECT ON *.* FROM {nonexistent_param:Identifier}; -- { serverError UNKNOWN_QUERY_PARAMETER }
DROP USER {nonexistent_param:Identifier}; -- { serverError UNKNOWN_QUERY_PARAMETER }

-- Query-parameter substitution must preserve strict identifier formatting.
SET param_strict_user_04603 = 'user_04603_$';
SET enforce_strict_identifier_format = 1;
CREATE USER `user_04603_$`; -- { serverError BAD_ARGUMENTS }
CREATE USER {strict_user_04603:Identifier}; -- { serverError BAD_ARGUMENTS }
CREATE ROLE `role_04603_$`;
GRANT SELECT ON *.* TO `role_04603_$`;
REVOKE SELECT ON *.* FROM `role_04603_$`;
DROP ROLE `role_04603_$`;
CREATE ROLE {strict_user_04603:Identifier}; -- { serverError BAD_ARGUMENTS }
GRANT SELECT ON *.* TO {strict_user_04603:Identifier}; -- { serverError BAD_ARGUMENTS }
DROP ROLE {strict_user_04603:Identifier}; -- { serverError BAD_ARGUMENTS }
SET enforce_strict_identifier_format = 0;

-- Static access DDL must retain the master AST-size budget.
CREATE USER `static_ast_elements_04603`;
SET max_ast_elements = 4;
GRANT SELECT ON *.* TO `static_ast_elements_04603`;
REVOKE SELECT ON *.* FROM `static_ast_elements_04603`;
DROP USER `static_ast_elements_04603`;
SET max_ast_elements = DEFAULT;

-- Preserve the legacy ALL/ANY list syntax, but do not discard query parameters before substitution.
REVOKE SELECT ON *.* FROM ALL, {nonexistent_param:Identifier}; -- { serverError UNKNOWN_QUERY_PARAMETER }
REVOKE ALL, {nonexistent_param:Identifier} FROM default; -- { serverError UNKNOWN_QUERY_PARAMETER }
ALTER USER default DEFAULT ROLE ALL, {nonexistent_param:Identifier}; -- { serverError UNKNOWN_QUERY_PARAMETER }
ALTER USER default GRANTEES ANY, {nonexistent_param:Identifier}; -- { serverError UNKNOWN_QUERY_PARAMETER }

CREATE USER {CLICKHOUSE_DATABASE:Identifier};
ALTER USER {CLICKHOUSE_DATABASE:Identifier} DEFAULT ROLE ALL, {CLICKHOUSE_DATABASE_1:Identifier};
SELECT count() FROM system.users WHERE name = currentDatabase();
DROP USER {CLICKHOUSE_DATABASE:Identifier};

DROP USER IF EXISTS {CLICKHOUSE_DATABASE:Identifier}, {CLICKHOUSE_DATABASE_1:Identifier}, {CLICKHOUSE_DATABASE_2:Identifier};

-- The same parameter works as the entity name in CREATE / ALTER / DROP ROLE.
DROP ROLE IF EXISTS {CLICKHOUSE_DATABASE:Identifier}, {CLICKHOUSE_DATABASE_1:Identifier};
CREATE ROLE {CLICKHOUSE_DATABASE:Identifier}, {CLICKHOUSE_DATABASE_1:Identifier};
SELECT count() FROM system.roles WHERE name IN (currentDatabase(), currentDatabase() || '_1');
ALTER ROLE {CLICKHOUSE_DATABASE:Identifier} SETTINGS max_threads = 1;
DROP ROLE {CLICKHOUSE_DATABASE:Identifier}, {CLICKHOUSE_DATABASE_1:Identifier};
SELECT count() FROM system.roles WHERE name IN (currentDatabase(), currentDatabase() || '_1');
DROP ROLE {nonexistent_param:Identifier}; -- { serverError UNKNOWN_QUERY_PARAMETER }

-- Query parameters work in the granted-role list of GRANT / REVOKE.
CREATE USER {CLICKHOUSE_DATABASE:Identifier};
CREATE ROLE {CLICKHOUSE_DATABASE_1:Identifier};
GRANT {CLICKHOUSE_DATABASE_1:Identifier} TO {CLICKHOUSE_DATABASE:Identifier};
SELECT count() FROM system.role_grants WHERE user_name = currentDatabase() AND granted_role_name = currentDatabase() || '_1';

-- ... and in the DEFAULT ROLE / GRANTEES clauses of CREATE / ALTER USER.
ALTER USER {CLICKHOUSE_DATABASE:Identifier} DEFAULT ROLE {CLICKHOUSE_DATABASE_1:Identifier};
SELECT count() FROM system.users WHERE name = currentDatabase() AND has(default_roles_list, currentDatabase() || '_1');
ALTER USER {CLICKHOUSE_DATABASE:Identifier} GRANTEES {CLICKHOUSE_DATABASE_1:Identifier};
SELECT count() FROM system.users WHERE name = currentDatabase() AND has(grantees_list, currentDatabase() || '_1');
REVOKE {CLICKHOUSE_DATABASE_1:Identifier} FROM {CLICKHOUSE_DATABASE:Identifier};
SELECT count() FROM system.role_grants WHERE user_name = currentDatabase() AND granted_role_name = currentDatabase() || '_1';
DROP USER {CLICKHOUSE_DATABASE:Identifier};
CREATE USER {CLICKHOUSE_DATABASE:Identifier} DEFAULT ROLE {CLICKHOUSE_DATABASE_1:Identifier};
SELECT count() FROM system.users WHERE name = currentDatabase() AND has(default_roles_list, currentDatabase() || '_1');
DROP USER {CLICKHOUSE_DATABASE:Identifier};
CREATE USER {CLICKHOUSE_DATABASE:Identifier} ROLE {CLICKHOUSE_DATABASE_1:Identifier};
SELECT count() FROM system.role_grants WHERE user_name = currentDatabase() AND granted_role_name = currentDatabase() || '_1';
DROP USER {CLICKHOUSE_DATABASE:Identifier};
CREATE USER {CLICKHOUSE_DATABASE:Identifier} GRANTEES {CLICKHOUSE_DATABASE_1:Identifier};
SELECT count() FROM system.users WHERE name = currentDatabase() AND has(grantees_list, currentDatabase() || '_1');
DROP USER {CLICKHOUSE_DATABASE:Identifier};
DROP ROLE {CLICKHOUSE_DATABASE_1:Identifier};

SELECT formatQuery('REVOKE SELECT ON *.* FROM ALL, role');
SELECT formatQuery('GRANT SELECT ON *.* TO {g:Identifier}');
SELECT formatQuery('DROP ROLE IF EXISTS r1@''%'', ''r2@%.myhost.com''');
