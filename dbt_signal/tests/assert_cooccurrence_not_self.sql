-- A technology co-occurring with itself would report a meaningless lift of
-- 100% and would rank above every genuine pair.
select tech_slug, co_tech_slug
from {{ ref('tech_cooccurrence') }}
where tech_slug = co_tech_slug
