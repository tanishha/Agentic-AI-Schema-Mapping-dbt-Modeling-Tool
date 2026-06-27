with source as (
    select * from {{ source('project', 'transaction') }}
),
cleaned as (
    select
        nullif(trim(cast("transaction_id" as text)), '') as "transaction_id",
        nullif(trim(cast("member_id" as text)), '') as "member_id",
        cast("points_earned" as integer) as "points_earned",
        nullif(trim(cast("tier" as text)), '') as "tier",
        nullif(trim(cast("activity_date" as text)), '') as "activity_date"
    from source
)
select * from cleaned
