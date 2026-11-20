-- Lab schema. Two environments in one database, separated by schema, so a Vault
-- database role can be scoped to exactly one of them.
CREATE SCHEMA dev;
CREATE SCHEMA prod;

CREATE TABLE dev.logs (
  id      serial PRIMARY KEY,
  ts      timestamptz NOT NULL DEFAULT now(),
  service text NOT NULL,
  level   text NOT NULL,
  message text NOT NULL
);
CREATE TABLE prod.logs (LIKE dev.logs INCLUDING ALL);

CREATE TABLE dev.services (
  name         text PRIMARY KEY,
  status       text NOT NULL DEFAULT 'running',
  restarts     integer NOT NULL DEFAULT 0,
  last_restart timestamptz
);
CREATE TABLE prod.services (LIKE dev.services INCLUDING ALL);

INSERT INTO dev.services (name) VALUES ('payments'), ('checkout'), ('search');
INSERT INTO prod.services (name) VALUES ('payments'), ('checkout'), ('search');

INSERT INTO dev.logs (service, level, message) VALUES
  ('payments', 'INFO',  'payment intent created id=pi_dev_1042'),
  ('payments', 'WARN',  'retrying card authorisation, attempt 2 of 3'),
  ('checkout', 'INFO',  'cart 88213 converted to order'),
  ('payments', 'ERROR', 'timeout calling fraud-score after 3000ms'),
  ('search',   'INFO',  'reindex finished in 41s'),
  ('checkout', 'WARN',  'slow query on orders_by_customer: 912ms');

INSERT INTO prod.logs (service, level, message) VALUES
  ('payments', 'INFO',  'payment intent created id=pi_live_77120'),
  ('payments', 'ERROR', 'ledger write failed: connection reset by peer'),
  ('checkout', 'INFO',  'order 5520931 confirmed'),
  ('search',   'WARN',  'replica lag 4.2s');
