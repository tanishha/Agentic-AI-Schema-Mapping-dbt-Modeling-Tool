select
    *
from {{ ref('stg_transaction') }}
