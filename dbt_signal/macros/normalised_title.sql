{#
  Strip a trailing location suffix from a job title.

  Some employers append the city to the title itself rather than using the
  location field - BNY Mellon posts "2027 BNY Summer Internship Program -
  Engineering (Data Science) - Pittsburgh, PA" alongside identical Jersey City,
  Lake Mary and New York variants. Deduplicating on the raw title treats those
  as four separate opportunities, which clutters the feed and pays the scorer
  four times for one answer. Measured impact when introduced: 15 of 649
  intern/entry roles were redundant.

  DELIBERATELY BACKSLASH-FREE. The first version used \s and \) and the escapes
  did not survive templating on every adapter: the pattern reached Snowflake as
  '[s-–(,]...s*)?s*$' with an unbalanced paren, which errored there and, worse,
  compiled on Postgres as something that matched a literal "s" rather than
  whitespace. A regex that silently matches the wrong thing is harder to catch
  than one that fails, so this uses literal characters and bracket classes
  only - no escape sequence to lose.
#}
{% macro normalised_title(column) %}
    regexp_replace(
        lower({{ column }}),
        '[ ,(-]+[a-z .]+, *(al|ak|az|ar|ca|co|ct|de|fl|ga|hi|id|il|in|ia|ks|ky|la|me|md|ma|mi|mn|ms|mo|mt|ne|nv|nh|nj|nm|ny|nc|nd|oh|ok|or|pa|ri|sc|sd|tn|tx|ut|vt|va|wa|wv|wi|wy|dc|on|qc|bc|ab|mb|sk|ns|nb|nl|pe)[)]? *$',
        ''
    )
{% endmacro %}
