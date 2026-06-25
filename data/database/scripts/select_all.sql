-- Inspect all current target tables.
.headers on
.mode column

SELECT 'customers' AS table_name, COUNT(*) AS row_count FROM customers;
SELECT * FROM customers LIMIT 100;

SELECT 'contact_details' AS table_name, COUNT(*) AS row_count FROM contact_details;
SELECT * FROM contact_details LIMIT 100;

SELECT 'addresses' AS table_name, COUNT(*) AS row_count FROM addresses;
SELECT * FROM addresses LIMIT 100;
