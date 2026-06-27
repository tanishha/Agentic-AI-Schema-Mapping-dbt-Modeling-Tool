-- Row counts for dbt-generated staging views and mart tables only.
SELECT 'stg_customer' AS table_name, COUNT(*) AS row_count FROM "stg_customer";
SELECT 'stg_product' AS table_name, COUNT(*) AS row_count FROM "stg_product";
SELECT 'stg_order' AS table_name, COUNT(*) AS row_count FROM "stg_order";
SELECT 'mart_customer' AS table_name, COUNT(*) AS row_count FROM "mart_customer";
SELECT 'mart_product' AS table_name, COUNT(*) AS row_count FROM "mart_product";
SELECT 'mart_order' AS table_name, COUNT(*) AS row_count FROM "mart_order";
SELECT 'mart_order_enriched' AS table_name, COUNT(*) AS row_count FROM "mart_order_enriched";
