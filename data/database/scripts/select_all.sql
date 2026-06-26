-- Inspect all current target tables.
.headers on
.mode column

SELECT 'customers' AS table_name, COUNT(*) AS row_count FROM customer;
SELECT * FROM customer LIMIT 100;

SELECT 'products' AS table_name, COUNT(*) AS row_count FROM product;
SELECT * FROM product LIMIT 100;

SELECT 'orders' AS table_name, COUNT(*) AS row_count FROM "order";
SELECT * FROM "order" LIMIT 100;
