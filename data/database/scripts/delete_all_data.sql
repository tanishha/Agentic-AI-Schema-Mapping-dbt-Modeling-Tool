-- Delete all migrated rows while keeping the target schema.
PRAGMA foreign_keys = OFF;

DELETE FROM "order";
DELETE FROM "product";
DELETE FROM "customer";`

PRAGMA foreign_keys = ON;
