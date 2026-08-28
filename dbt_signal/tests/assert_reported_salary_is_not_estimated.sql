-- salary_min_reported must only ever carry employer-published figures.
-- Roughly 99% of the source's salary values are model estimates; if one leaks
-- into the reported column it reaches the dashboard, the market index and
-- potentially a cold email as though the employer had stated it.
select source, job_id, salary_min_reported
from {{ ref('stg_jobs') }}
where salary_min_reported is not null
  and salary_is_predicted is true
