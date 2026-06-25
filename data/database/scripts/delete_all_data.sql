-- Delete all migrated rows while keeping the target schema.
PRAGMA foreign_keys = OFF;

DELETE FROM addresses;
DELETE FROM contact_details;
DELETE FROM customers;

PRAGMA foreign_keys = ON;
