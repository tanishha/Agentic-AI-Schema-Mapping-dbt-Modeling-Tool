with source as (
    select * from {{ source('project', 'customer') }}
),
cleaned as (
    select
        cast("customer_id" as integer) as "customer_id",
        nullif(trim(cast("full_name" as text)), '') as "full_name",
        nullif(trim(cast("email" as text)), '') as "email",
        nullif(trim(cast("phone" as text)), '') as "phone",
        nullif(trim(cast("city" as text)), '') as "city"
    from source
)
select * from cleaned
