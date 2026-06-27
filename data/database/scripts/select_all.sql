-- Inspect loaded/source tables first, then dbt-generated layers.
.headers on
.mode column

SELECT 'loaded/source: customer' AS section;
SELECT COUNT(*) AS row_count FROM "customer";
SELECT * FROM customer LIMIT 100;

SELECT 'loaded/source: product' AS section;
SELECT COUNT(*) AS row_count FROM "product";
SELECT * FROM product LIMIT 100;

SELECT 'loaded/source: order' AS section;
SELECT COUNT(*) AS row_count FROM "order";
SELECT * FROM "order" LIMIT 100;

SELECT 'dbt staging: stg_customer' AS section;
SELECT COUNT(*) AS row_count FROM "stg_customer";
SELECT * FROM "stg_customer" LIMIT 100;

SELECT 'dbt staging: stg_product' AS section;
SELECT COUNT(*) AS row_count FROM "stg_product";
SELECT * FROM "stg_product" LIMIT 100;

SELECT 'dbt staging: stg_order' AS section;
SELECT COUNT(*) AS row_count FROM "stg_order";
SELECT * FROM "stg_order" LIMIT 100;

SELECT 'dbt mart: mart_customer' AS section;
SELECT COUNT(*) AS row_count FROM "mart_customer";
SELECT * FROM "mart_customer" LIMIT 100;

SELECT 'dbt mart: mart_product' AS section;
SELECT COUNT(*) AS row_count FROM "mart_product";
SELECT * FROM "mart_product" LIMIT 100;

SELECT 'dbt mart: mart_order' AS section;
SELECT COUNT(*) AS row_count FROM "mart_order";
SELECT * FROM "mart_order" LIMIT 100;

SELECT 'dbt mart: mart_order_enriched' AS section;
SELECT COUNT(*) AS row_count FROM "mart_order_enriched";
SELECT * FROM "mart_order_enriched" LIMIT 100;
