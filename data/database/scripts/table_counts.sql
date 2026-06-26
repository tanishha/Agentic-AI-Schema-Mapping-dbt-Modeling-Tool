-- Show row counts for every target table.
SELECT 'customers' AS table_name, COUNT(*) AS row_count FROM "customer";
SELECT 'products' AS table_name, COUNT(*) AS row_count FROM "product";
SELECT 'orders' AS table_name, COUNT(*) AS row_count FROM "order";
