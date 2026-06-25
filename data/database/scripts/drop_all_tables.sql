-- Drop all target tables. The next migration will recreate them from the uploaded DDL.
PRAGMA foreign_keys = OFF;

DROP TABLE IF EXISTS addresses;
DROP TABLE IF EXISTS contact_details;
DROP TABLE IF EXISTS customers;

PRAGMA foreign_keys = ON;
