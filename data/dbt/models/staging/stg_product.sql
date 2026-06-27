with source as (
    select * from {{ source('project', 'product') }}
),
cleaned as (
    select
        cast("product_id" as integer) as "product_id",
        nullif(trim(cast("product_name" as text)), '') as "product_name"
    from source
)
select * from cleaned
