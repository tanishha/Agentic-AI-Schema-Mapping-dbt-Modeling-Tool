select
    child."transaction_id" as "transaction_id",
    child."member_id" as "member_id",
    child."points_earned" as "points_earned",
    child."tier" as "tier",
    child."activity_date" as "activity_date",
    parent_1."customer_id" as "member_customer_id"
from {{ ref('stg_transaction') }} as child
left join {{ ref('stg_member') }} as parent_1
    on child."member_id" = parent_1."member_id"
