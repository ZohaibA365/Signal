{{ config(materialized='view') }}

/*
  Cleans and standardises raw postings.

  Two things happen here that the raw layer deliberately did not do:
  1. Deduplication. The same role is posted repeatedly under different job_ids
     (Booz Allen's "Data Engineer" in Chantilly appears 7 times), so identical
     company + title + location collapses to the most recently posted row.
  2. Derived attributes - seniority, staleness, and whether a salary is a real
     posted figure or one of Adzuna's model estimates.

  PORTABILITY: written to run on both Postgres and Snowflake. FILTER (WHERE)
  is Postgres-only, so counts use CASE WHEN; regex uses regexp_like(), which
  both engines support, rather than the Postgres-only ~* operator.

  REGEX PORTABILITY, two separate traps:

  1. Anchoring. Postgres's regexp_like searches anywhere in the string;
     Snowflake's must match the WHOLE value. Identical SQL classified 1,590
     postings as internships on Postgres and 0 on Snowflake - compiling
     cleanly on both while silently disagreeing.
  2. Word boundaries. Postgres spells them \y; Snowflake has no equivalent
     (\b is a backspace in Postgres ARE), so there is no shared escape. They
     are written as explicit ([^a-z]|^) character classes instead, which also
     keeps "internal" from matching "intern".

  Patterns are wrapped in .* deliberately. Postgres's
  regexp_like searches for a match anywhere in the string; Snowflake's is
  anchored and must match the WHOLE value. Identical SQL therefore classified
  1,590 postings as internships on Postgres and 0 on Snowflake - it compiled
  cleanly on both and silently produced different answers. The wrapped form
  behaves the same everywhere.
*/

with source as (

    select * from {{ source('signal', 'raw_postings') }}

),

cleaned as (

    select
        source,
        job_id,
        country,
        -- Canonicalised, so "Databricks" and "Databricks, Inc." are one
        -- employer rather than two company pages, two peer computations and
        -- two sponsorship lookups. The mapping is built in Python by
        -- storage/resolve_companies.py with the same normaliser that
        -- canonicalises both sides of the DOL join - one normaliser in one
        -- language, rather than a second copy in SQL that would drift and
        -- would have to survive three engines' differing string functions.
        coalesce(ci.canonical_name, nullif(trim(source.company_name), ''))
                                                as company_name,
        nullif(trim(job_title), '')             as job_title,
        location                                as location_raw,
        location_state,
        split_part(location, ',', 1)            as location_city,
        posted_date,
        (current_date - posted_date::date)      as days_since_posted,

        salary_min,
        salary_max,
        salary_is_predicted,
        -- Only ~1% of these are real. Surfacing an estimate as fact would
        -- actively mislead, so keep the reported figure separate.
        case when salary_is_predicted then null else salary_min end as salary_min_reported,

        description_raw,
        category,
        redirect_url,
        search_term,
        ingested_at,
        first_seen,
        last_seen,

        -- Order matters: "Senior Associate" must resolve to senior, not entry.
        -- \y is a word boundary, without which "Internal" matches "intern".
        case
            -- "Student" and "New Grad" belong here, not in mid or entry.
            -- Canadian employers rarely write "intern": the term is co-op, and
            -- a title like "IS Data Engineering Student" was landing in mid,
            -- so it never appeared in a search for internships - on a site
            -- whose main use is finding them. Ordered before senior so that
            -- "Senior Design Student" reads as a student role.
            when regexp_like(job_title,
                 '^(.*[^a-z])?(intern|interns|internship|interning|co.?op|'
              || 'new.grad|new.graduate|university.grad|campus|'
              || 'student|students|placement|trainee|apprentice)([^a-z].*)?$',
                 'i') then 'intern'
            -- Canadian employers frequently name the work term instead of
            -- using the word. RBC posts "2027 CAE, Winter Audit Planning &
            -- Reporting Analyst (4 months)" - a Winter 2027 co-op whose title
            -- contains neither "intern" nor "co-op". A season with a year, or
            -- an explicit month count, is the tell.
            when regexp_like(job_title,
                 '.*((winter|summer|fall|spring)[^a-z0-9]*20[0-9][0-9]|'
              || '20[0-9][0-9][^a-z0-9]*(winter|summer|fall|spring)|'
              || '[(][0-9]{1,2}[ -]*months?[)]).*', 'i') then 'intern'
            when regexp_like(job_title, '^(.*[^a-z])?(senior|sr|staff|principal|lead|distinguished|manager|director|head|vp|chief|architect|expert)([^a-z].*)?$', 'i') then 'senior'
            when regexp_like(job_title, '^(.*[^a-z])?(new grad|graduate|entry.level|junior|jr|associate|apprentice)([^a-z].*)?$', 'i') then 'entry'
            else 'mid'
        end as seniority,

        regexp_like(job_title, '^(.*[^a-z])?(intern|interns|internship|co-?op)([^a-z].*)?$', 'i') as is_internship,

        -- Evaluated at load time by storage/sponsorship_text.py and stored, not
        -- recomputed here. Carried through so description_raw does not have to be.
        source.refuses_sponsorship,
        source.offers_sponsorship,

        -- Adzuna keeps postings live long after they are realistically open.
        (current_date - posted_date::date) > 60  as is_stale

    from source
    left join {{ source('signal', 'company_identity') }} ci
           on ci.company_name = nullif(trim(source.company_name), '')
    -- A handful of postings (9 of 20,084) carry no employer at all - Adzuna
    -- returns them anonymised, with location flattened to just "US". They
    -- cannot be applied to and cannot be attributed to a company, so they are
    -- useless downstream. The raw layer keeps them; staging drops them.
    where nullif(trim(source.company_name), '') is not null

),

deduplicated as (

    select
        *,
        row_number() over (
            partition by lower(coalesce(company_name, '')),
                         lower(coalesce(job_title, '')),
                         lower(coalesce(location_raw, ''))
            order by posted_date desc nulls last, job_id
        ) as _row_num
    from cleaned

)

select
    source,
    job_id,
    company_name,
    job_title,
    location_raw,
    location_state,
    location_city,
    posted_date,
    days_since_posted,
    salary_min,
    salary_max,
    salary_is_predicted,
    salary_min_reported,
    description_raw,
    category,
    redirect_url,

    /*
      Where "Apply" actually sends someone, and whether it can be trusted.

      Adzuna supplies 90% of this corpus and its redirect_url is a
      country-gated adzuna.com link: opened from outside the posting's own
      country it shows "not available in your region", and it 403s to
      anything that is not a browser, so it cannot be resolved to the real
      posting either. Adzuna's API never exposes the employer's own URL, so
      these are unfixable rather than merely unfixed.

      A career-board posting carries the employer's own URL by construction.
      Only those are linkable, so only those reach the job list; the rest
      still count toward market statistics, where no link is involved.
    */
    /*
      Does THIS posting refuse sponsorship, whatever the company has done
      historically?

      The visa label otherwise comes from Department of Labor filings, which
      are a fact about the employer and not about the role. A posting can say
      the opposite and hundreds here do: General Motors has 1,141 filings and
      carries a "sponsors" tag, while 33 GM postings state that GM does not
      provide immigration-related sponsorship for the role. Acting on the
      company-level label there means applying for a job that rejects you on
      submission.

      Per-posting rather than per-company for a demonstrable reason: Capital
      One appears on both sides, some postings saying "will not sponsor a new
      applicant" and others "will consider sponsoring a new qualified
      applicant".

      A refusal wins over any offer language in the same posting. The first
      version of this tried to let an offer cancel a refusal and got GM
      exactly backwards: "DO NOT APPLY IF YOU WILL NEED SPONSORSHIP" contains
      "will ... sponsorship", which a loose pattern reads as an offer. Offers
      are therefore matched only where the employer is plainly the subject,
      and they never override an explicit refusal - a posting saying "we
      sponsor visas, but not for this role" is a refusal for this role.

      No backslashes in the patterns: escapes do not survive dbt templating,
      and an earlier macro reached Snowflake as an unbalanced paren and
      Postgres as a literal "s". regexp_like is used over ~* for the same
      portability reason.

      The flag is 'is', not 'i', on every predicate over description_raw. The s
      means "dot matches newline", and descriptions are multi-line: without it
      Snowflake's .* stops at the first line break, so a refusal written anywhere
      below the opening paragraph is missed. Measured: wrapping alone took
      refuses_sponsorship from 0 to 1,023 against Postgres's 3,172; the flag
      closes the rest. Postgres already behaves that way by default and accepts
      the flag, so one form serves both. Job titles keep 'i' - they are one line.

      Every search-style pattern is wrapped in .*(...).* because the two engines
      disagree about anchoring, and they disagree SILENTLY. Postgres regexp_like
      searches anywhere; Snowflake's matches the whole string. Measured on the
      same 64,141 rows: refuses_sponsorship found 3,172 postings on Postgres and
      ZERO on Snowflake, and nothing failed - the number was simply wrong on one
      side. The project documented this hazard for is_internship, which is why
      that pattern is written ^(.*[^a-z])?...([^a-z].*)?$ and matches identically
      on both; the sponsorship predicates were never given the same treatment.
      Wrapping costs nothing on Postgres, where .* already matched anywhere.

      Long patterns are joined with an explicit || rather than by putting two
      quoted strings on adjacent lines. Postgres concatenates adjacent literals
      separated by a newline; Snowflake rejects it outright with "syntax error
      unexpected ''unable to|not be able to...''". Building these models on a
      second engine is how that was found, and it was sitting in the two most
      load-bearing predicates in the project - the internship classifier and the
      sponsorship refusal. Spark SQL accepts || as well, so one form serves all
      three.
    */
    -- Read, not recomputed. These are evaluated once per posting when it is
    -- loaded, by storage/sponsorship_text.py, and stored on raw_postings.
    --
    -- Two reasons they moved. Recomputing them here meant description_raw could
    -- never leave the serving database, and that text is 208 MB of a 512 MB
    -- budget for something whose only other readers - technology extraction and
    -- LLM scoring - have already finished with it.
    --
    -- And these predicates were the source of three separate cross-engine bugs:
    -- Postgres regexp_like searches anywhere while Snowflake's matches the whole
    -- string, Snowflake's . stops at a line break without the s flag, and
    -- adjacent quoted strings concatenate on Postgres but are a syntax error on
    -- Snowflake. refuses_sponsorship read 3,172 on Postgres and 0 on Snowflake
    -- with nothing failing. Computing them once, in Postgres, on the way in
    -- leaves this layer with no regex over description text at all.
    coalesce(refuses_sponsorship, false)                  as refuses_sponsorship,
    coalesce(offers_sponsorship, false)                   as offers_sponsorship,

    case when source = 'company_board' then redirect_url end   as link_url,
    case when source = 'company_board' then 'direct'
         else 'aggregator' end                                 as link_tier,

    search_term,
    ingested_at,
    first_seen,
    last_seen,
    seniority,
    is_internship,
    is_stale,
    country
from deduplicated
where _row_num = 1
