with source as (
    select * from {{ source('project', 'order') }}
),
cleaned as (
    select
        cast("order_id" as integer) as "order_id",
        cast("customer_id" as integer) as "customer_id",
        cast("product_id" as integer) as "product_id",
        cast("quantity" as integer) as "quantity",
        cast("price" as real) as "price",
        nullif(trim(cast("order_date" as text)), '') as "order_date"
    from source
)
select * from cleaned
