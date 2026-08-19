-- db-seed.sql — baked into the image at build time (see Dockerfile).
-- Seeds the `corp` database with sensitive-looking data so the host genuinely
-- "holds database data" worth stealing. Two loot avenues:
--   * direct SQL read once you have any DB login (SELECT the tables), and
--   * the on-disk win-flag reachable only via the superuser COPY..PROGRAM RCE.

CREATE DATABASE corp;
\connect corp

-- Sensitive customer records (the headline data-at-rest loot).
CREATE TABLE customers (
    id         serial PRIMARY KEY,
    name       text,
    email      text,
    card_last4 text,
    balance    numeric
);
INSERT INTO customers (name, email, card_last4, balance) VALUES
    ('Norte Logistica',   'ops@norte.example',  '4417', 182430.55),
    ('Sur Distribucion',  'admin@sur.example',  '9921',  54120.10),
    ('Andes Cargo',       'it@andes.example',   '3380', 301999.00);

-- A notes table carrying a reusable credential + the data-retrieval flag.
CREATE TABLE secret_notes (
    id   serial PRIMARY KEY,
    note text
);
INSERT INTO secret_notes (note) VALUES
    ('DB_FLAG{postgres-weak-superuser-data-read-4a1f}'),
    ('replication user: repl / R3plic8-2024'),
    ('backup share: //file-server/share');

-- A low-privilege application role (a realistic secondary credential an attacker
-- might harvest; NOT a superuser, so it cannot COPY..PROGRAM).
CREATE ROLE webapp LOGIN PASSWORD 'webapp';
GRANT CONNECT ON DATABASE corp TO webapp;
GRANT SELECT ON customers TO webapp;
