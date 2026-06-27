with source as (
    select * from {{ source('project', 'member') }}
),
cleaned as (
    select
        nullif(trim(cast("member_id" as text)), '') as "member_id",
        cast("customer_id" as integer) as "customer_id"
    from source
)
select * from cleaned
