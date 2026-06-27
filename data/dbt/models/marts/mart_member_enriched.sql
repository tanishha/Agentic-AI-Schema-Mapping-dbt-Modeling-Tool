select
    child."member_id" as "member_id",
    child."customer_id" as "customer_id",
    parent_1."full_name" as "customer_full_name",
    parent_1."email" as "customer_email",
    parent_1."phone" as "customer_phone",
    parent_1."city" as "customer_city"
from {{ ref('stg_member') }} as child
left join {{ ref('stg_customer') }} as parent_1
    on child."customer_id" = parent_1."customer_id"
