-- Execute as PostgreSQL administrator on db-pg after retiring the dev containers.
\set ON_ERROR_STOP on
\connect postgres
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_database d JOIN pg_roles r ON r.oid=d.datdba
               WHERE d.datname='sem_test' AND r.rolname <> 'sem_test_migrate') THEN
        RAISE EXCEPTION 'Unexpected sem_test owner; refusing removal';
    END IF;
END $$;
DROP DATABASE IF EXISTS sem_test WITH (FORCE);
DROP ROLE IF EXISTS sem_test_app;
DROP ROLE IF EXISTS sem_test_migrate;
