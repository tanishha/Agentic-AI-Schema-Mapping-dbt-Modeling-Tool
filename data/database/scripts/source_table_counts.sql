-- Row counts for DBMapper-loaded source/final tables only.
SELECT 'customer' AS table_name, COUNT(*) AS row_count FROM "customer";
SELECT 'product' AS table_name, COUNT(*) AS row_count FROM "product";
SELECT 'order' AS table_name, COUNT(*) AS row_count FROM "order";
