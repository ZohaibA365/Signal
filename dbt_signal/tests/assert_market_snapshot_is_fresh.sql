-- The daily snapshot is the project's compounding asset: a gap in it can
-- never be backfilled, because the API only reports today. Failing loudly
-- when capture stops is the only protection.
select max(snapshot_date) as latest_snapshot
from {{ source('signal_market', 'market_snapshots') }}
having max(snapshot_date) < current_date - interval '3 days'
