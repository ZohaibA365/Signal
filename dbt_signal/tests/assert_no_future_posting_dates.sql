-- A posting dated in the future means a source changed its date format or a
-- timezone was mishandled. Every freshness and trend calculation downstream
-- reads posted_date, so this silently corrupts the market index rather than
-- failing loudly.
select source, job_id, posted_date
from {{ ref('stg_jobs') }}
where posted_date > current_date + interval '2 days'
