-- Show row counts for every target table.
SELECT 'customers' AS table_name, COUNT(*) AS row_count FROM customers;
SELECT 'contact_details' AS table_name, COUNT(*) AS row_count FROM contact_details;
SELECT 'addresses' AS table_name, COUNT(*) AS row_count FROM addresses;
