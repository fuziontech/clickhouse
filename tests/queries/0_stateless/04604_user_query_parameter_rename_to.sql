-- Query parameters are supported for access-entity names, but not RENAME TO targets.
SELECT formatQuery('ALTER USER {user:Identifier} RENAME TO {new_user:Identifier}'); -- { serverError SYNTAX_ERROR }
SELECT formatQuery('ALTER ROLE {role:Identifier} RENAME TO {new_role:Identifier}'); -- { serverError SYNTAX_ERROR }
