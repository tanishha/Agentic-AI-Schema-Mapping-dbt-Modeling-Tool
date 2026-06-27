select
    child."order_id" as "order_id",
    child."customer_id" as "customer_id",
    child."product_id" as "product_id",
    child."quantity" as "quantity",
    child."price" as "price",
    child."order_date" as "order_date",
    parent_1."product_name" as "product_product_name",
    parent_2."full_name" as "customer_full_name",
    parent_2."email" as "customer_email",
    parent_2."phone" as "customer_phone",
    parent_2."city" as "customer_city"
from {{ ref('stg_order') }} as child
left join {{ ref('stg_product') }} as parent_1
    on child."product_id" = parent_1."product_id"
left join {{ ref('stg_customer') }} as parent_2
    on child."customer_id" = parent_2."customer_id"
