{#
  Strip a trailing location suffix from a job title.

  Some employers append the city to the title itself rather than relying on
  the location field - BNY Mellon posts "2027 BNY Summer Internship Program -
  Engineering (Data Science) - Pittsburgh, PA" alongside identical Jersey City,
  Lake Mary and New York variants. Deduplicating on the raw title treats those
  as four separate opportunities, which clutters the feed and pays the scorer
  four times for one answer.

  Measured impact when introduced: 15 of 649 intern/entry roles were redundant.
#}
{% macro normalised_title(column) %}
    regexp_replace(
        lower({{ column }}),
        '[\s\-–(,]+[a-z .]+,\s*(a[lkzr]|c[aot]|d[ce]|fl|ga|hi|i[adln]|k[sy]|la|m[adeinost]|n[cdehjmvy]|o[hkr]|pa|ri|s[cd]|t[nx]|ut|v[at]|w[aivy])\s*\)?\s*$',
        ''
    )
{% endmacro %}
