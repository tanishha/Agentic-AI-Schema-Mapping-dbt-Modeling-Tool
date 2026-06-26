-- Drop all target tables. The next migration will recreate them from the uploaded DDL.
PRAGMA foreign_keys = OFF;

DROP TABLE IF EXISTS "order";
DROP TABLE IF EXISTS "product";
DROP TABLE IF EXISTS "customer";

PRAGMA foreign_keys = ON;
