-- Eenmalig uitvoeren als PostgreSQL-superuser, uitsluitend op de bedoelde server.
-- Stopt bij bestaande namen of andere fouten; niet blind opnieuw uitvoeren.
\set ON_ERROR_STOP on
CREATE ROLE sem_prod_migrate LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE sem_prod_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

CREATE DATABASE sem_prod OWNER sem_prod_migrate TEMPLATE template0 ENCODING 'UTF8';
REVOKE ALL ON DATABASE sem_prod FROM PUBLIC;
GRANT CONNECT ON DATABASE sem_prod TO sem_prod_app;

\connect sem_prod
CREATE EXTENSION postgis;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA sem AUTHORIZATION sem_prod_migrate;
GRANT USAGE ON SCHEMA sem, public TO sem_prod_app;
ALTER DEFAULT PRIVILEGES FOR ROLE sem_prod_migrate IN SCHEMA sem
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sem_prod_app;
ALTER DEFAULT PRIVILEGES FOR ROLE sem_prod_migrate IN SCHEMA sem
  GRANT USAGE, SELECT ON SEQUENCES TO sem_prod_app;
ALTER ROLE sem_prod_migrate IN DATABASE sem_prod SET search_path TO sem, public;
ALTER ROLE sem_prod_app IN DATABASE sem_prod SET search_path TO sem, public;
ALTER ROLE sem_prod_app IN DATABASE sem_prod SET statement_timeout TO '60s';
ALTER ROLE sem_prod_app IN DATABASE sem_prod SET idle_in_transaction_session_timeout TO '60s';


-- Stel hierna beide productiewachtwoorden interactief in via \password.
