"""
Controlled vocabulary for data/AI technologies.

Why this exists: the LLM's free-text `tech_stack` output was unusable for
analysis. Real output included "Analytics" and "analytics" as separate
entries, and "AI", "AI/ML", "AI/LLM" as three different things. You cannot
publish a demand index built on that.

So entity extraction moves to a dictionary and the LLM keeps the judgement
work it is actually better at (eligibility, fit). A dictionary is cheaper,
deterministic, and reproducible - and these numbers go on a public page, so
someone must be able to re-run them and get the same answer.

Two distinct uses:
  1. `match_technologies(text)` - extract canonical tools from a posting.
  2. `tracked()` - the subset snapshotted daily against the Adzuna API to
     build the demand time series.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Tech:
    """One canonical technology."""

    slug: str                       # canonical id, e.g. "apache_spark"
    name: str                       # display name, e.g. "Apache Spark"
    category: str
    aliases: tuple[str, ...] = ()   # surface forms found in postings
    # Query string for the Adzuna API. Differs from `name` when the bare name
    # is ambiguous as a search term - "Go" and "R" match everything, "Spark"
    # matches "sparkling water".
    query: str | None = None
    track: bool = False             # include in the daily demand snapshot
    # When False, the display name is NOT used as a match term - only aliases.
    # Needed for names that are ordinary English words: matching bare "Go" or
    # "R" against posting text produces almost entirely false positives
    # ("ready to go", "R&D"), which measured 53 and 10 bogus hits respectively.
    match_name: bool = True

    @property
    def search_query(self) -> str:
        return self.query or self.name


# Categories are deliberately coarse. Finer buckets fragment the index and
# make the "modern stack vs legacy stack" contrast harder to see.
TECHNOLOGIES: list[Tech] = [
    # --- Languages -------------------------------------------------------
    Tech("python", "Python", "language", ("python3", "py"), track=True),
    Tech("sql", "SQL", "language", ("ansi sql",), track=True),
    Tech("scala", "Scala", "language", track=True),
    Tech("java", "Java", "language", ()),
    Tech("golang", "Go", "language", ("golang", "go programming"),
         query="Golang", match_name=False),
    Tech("rust", "Rust", "language", (), query="Rust programming"),
    Tech("r_lang", "R", "language", ("rstudio", "r programming", "r language", "r/python"),
         query="R statistical", match_name=False),
    Tech("typescript", "TypeScript", "language", ("ts",)),
    Tech("bash", "Bash", "language", ("shell scripting", "shell")),

    # --- Warehouses & query engines --------------------------------------
    Tech("snowflake", "Snowflake", "warehouse", track=True),
    Tech("databricks", "Databricks", "warehouse", track=True),
    Tech("bigquery", "BigQuery", "warehouse", ("big query", "google bigquery"), track=True),
    Tech("redshift", "Redshift", "warehouse", ("amazon redshift",), track=True),
    Tech("synapse", "Azure Synapse", "warehouse", ("synapse",), track=True),
    Tech("clickhouse", "ClickHouse", "warehouse", ("click house",), track=True),
    Tech("duckdb", "DuckDB", "warehouse", ("duck db",), track=True),
    Tech("trino", "Trino", "warehouse", ("presto", "prestodb"), track=True),
    Tech("athena", "Athena", "warehouse", ("amazon athena",)),
    Tech("teradata", "Teradata", "warehouse", (), track=True),
    Tech("vertica", "Vertica", "warehouse"),
    Tech("firebolt", "Firebolt", "warehouse"),

    # --- Table formats & storage -----------------------------------------
    Tech("delta_lake", "Delta Lake", "format", ("deltalake", "delta tables"), track=True),
    Tech("iceberg", "Apache Iceberg", "format", ("iceberg",), query="Apache Iceberg", track=True),
    Tech("hudi", "Apache Hudi", "format", ("hudi",), query="Apache Hudi"),
    Tech("parquet", "Parquet", "format", ("apache parquet",), track=True),
    Tech("avro", "Avro", "format"),
    Tech("orc", "ORC", "format"),

    # --- Orchestration ----------------------------------------------------
    Tech("airflow", "Apache Airflow", "orchestration", ("airflow", "mwaa"),
         query="Apache Airflow", track=True),
    Tech("dagster", "Dagster", "orchestration", track=True),
    Tech("prefect", "Prefect", "orchestration", track=True),
    Tech("luigi", "Luigi", "orchestration"),
    Tech("argo", "Argo Workflows", "orchestration", ("argo",)),
    Tech("step_functions", "AWS Step Functions", "orchestration", ("step functions",)),
    Tech("azure_data_factory", "Azure Data Factory", "orchestration", ("adf", "data factory")),

    # --- Transformation & processing -------------------------------------
    Tech("dbt", "dbt", "transform", ("data build tool",), query="dbt analytics", track=True),
    Tech("spark", "Apache Spark", "transform", ("spark", "pyspark", "spark sql"),
         query="Apache Spark", track=True),
    Tech("flink", "Apache Flink", "transform", ("flink",), query="Apache Flink", track=True),
    Tech("beam", "Apache Beam", "transform", ("beam",), query="Apache Beam"),
    Tech("pandas", "pandas", "transform", ("pandas dataframe",), track=True),
    Tech("polars", "Polars", "transform", (), track=True),
    Tech("dask", "Dask", "transform"),
    Tech("ray", "Ray", "transform", ("ray.io", "ray serve", "ray tune"),
         query="Ray distributed", match_name=False),
    Tech("hadoop", "Hadoop", "transform", ("mapreduce", "hdfs"), track=True),
    Tech("hive", "Apache Hive", "transform", ("hive",), query="Apache Hive"),

    # --- Ingestion & CDC --------------------------------------------------
    Tech("fivetran", "Fivetran", "ingestion", track=True),
    Tech("airbyte", "Airbyte", "ingestion", track=True),
    Tech("stitch", "Stitch", "ingestion", (), query="Stitch data"),
    Tech("debezium", "Debezium", "ingestion", ("cdc", "change data capture")),
    Tech("meltano", "Meltano", "ingestion"),

    # --- Streaming --------------------------------------------------------
    Tech("kafka", "Apache Kafka", "streaming", ("kafka", "msk", "confluent"),
         query="Apache Kafka", track=True),
    Tech("kinesis", "AWS Kinesis", "streaming", ("kinesis",), track=True),
    Tech("pulsar", "Apache Pulsar", "streaming", ("pulsar",), query="Apache Pulsar"),
    Tech("rabbitmq", "RabbitMQ", "streaming", ("rabbit mq",)),
    Tech("pubsub", "Google Pub/Sub", "streaming", ("pub/sub", "pubsub")),

    # --- Cloud ------------------------------------------------------------
    Tech("aws", "AWS", "cloud", ("amazon web services",), track=True),
    Tech("gcp", "GCP", "cloud", ("google cloud", "google cloud platform"), track=True),
    Tech("azure", "Azure", "cloud", ("microsoft azure",), track=True),
    Tech("s3", "Amazon S3", "cloud", ("s3",)),
    Tech("glue", "AWS Glue", "cloud", ("glue",), query="AWS Glue"),
    Tech("emr", "AWS EMR", "cloud", ("emr",), query="AWS EMR"),
    Tech("lambda_aws", "AWS Lambda", "cloud", ("lambda",), query="AWS Lambda"),
    Tech("dataflow", "Google Dataflow", "cloud", ("dataflow",)),
    Tech("dataproc", "Google Dataproc", "cloud", ("dataproc",)),

    # --- Infrastructure ---------------------------------------------------
    Tech("docker", "Docker", "infra", ("containerization",), track=True),
    Tech("kubernetes", "Kubernetes", "infra", ("k8s", "eks", "gke"), track=True),
    Tech("terraform", "Terraform", "infra", ("iac", "infrastructure as code"), track=True),
    Tech("ansible", "Ansible", "infra"),
    Tech("github_actions", "GitHub Actions", "infra", ("gh actions",)),
    Tech("jenkins", "Jenkins", "infra"),
    Tech("git", "Git", "infra", ("version control",)),

    # --- BI & visualisation ------------------------------------------------
    Tech("tableau", "Tableau", "bi", track=True),
    Tech("looker", "Looker", "bi", ("lookml",), track=True),
    Tech("power_bi", "Power BI", "bi", ("powerbi",), track=True),
    Tech("superset", "Apache Superset", "bi", ("superset",)),
    Tech("metabase", "Metabase", "bi"),
    Tech("mode", "Mode Analytics", "bi", (), query="Mode Analytics"),
    Tech("sigma", "Sigma Computing", "bi", (), query="Sigma Computing"),
    Tech("quicksight", "QuickSight", "bi", ("amazon quicksight",)),

    # --- Operational databases ---------------------------------------------
    Tech("postgresql", "PostgreSQL", "database", ("postgres", "psql"), track=True),
    Tech("mysql", "MySQL", "database"),
    Tech("mongodb", "MongoDB", "database", ("mongo",), track=True),
    Tech("cassandra", "Cassandra", "database"),
    Tech("dynamodb", "DynamoDB", "database", ("dynamo",)),
    Tech("redis", "Redis", "database"),
    Tech("elasticsearch", "Elasticsearch", "database", ("elastic search", "opensearch")),
    Tech("neo4j", "Neo4j", "database", ("graph database",)),

    # --- ML / AI -----------------------------------------------------------
    Tech("machine_learning", "Machine Learning", "ml",
         ("ml", "ai/ml", "ai / ml", "statistical modeling"), track=True),
    Tech("llm", "LLMs", "ml",
         ("large language model", "large language models", "genai", "generative ai",
          "ai/llm", "gpt", "prompt engineering"),
         query="LLM", track=True),
    Tech("pytorch", "PyTorch", "ml", ("torch",), track=True),
    Tech("tensorflow", "TensorFlow", "ml", ("tf",), track=True),
    Tech("scikit_learn", "scikit-learn", "ml", ("sklearn", "scikit learn")),
    Tech("mlflow", "MLflow", "ml", ("ml flow",), track=True),
    Tech("kubeflow", "Kubeflow", "ml"),
    Tech("sagemaker", "AWS SageMaker", "ml", ("sagemaker",), track=True),
    Tech("vertex_ai", "Vertex AI", "ml", ("vertex",)),
    Tech("huggingface", "Hugging Face", "ml", ("huggingface", "transformers")),
    Tech("nlp", "NLP", "ml", ("natural language processing",)),
    Tech("computer_vision", "Computer Vision", "ml", ("cv",)),

    # --- Data quality & governance -----------------------------------------
    Tech("great_expectations", "Great Expectations", "quality", ("gx",), track=True),
    # "Soda" and "Monte Carlo" as bare names collide with a drink and a
    # simulation method, so both match on qualified forms only.
    Tech("soda", "Soda", "quality", ("soda core", "soda cloud", "soda data"),
         query="Soda data quality", match_name=False),
    Tech("monte_carlo", "Monte Carlo", "quality", ("montecarlo data", "monte carlo data"),
         query="Monte Carlo data", match_name=False),
    Tech("datahub", "DataHub", "quality", ("data hub",)),
    Tech("collibra", "Collibra", "quality"),
    Tech("alation", "Alation", "quality"),

    # --- Mainframe / enterprise legacy -----------------------------------
    # A whole labour market the original data-engineering vocabulary missed
    # entirely. These roles are numerous, well paid, and almost never appear
    # in "modern stack" discussions.
    Tech("cobol", "COBOL", "mainframe", ("cobol ii", "enterprise cobol"), track=True),
    Tech("mainframe", "Mainframe", "mainframe", ("z/os", "zos", "system z"), track=True),
    Tech("jcl", "JCL", "mainframe", ("job control language",), track=True),
    Tech("cics", "CICS", "mainframe", (), track=True),
    Tech("db2", "Db2", "mainframe", ("ibm db2",), track=True),
    Tech("ims", "IMS", "mainframe", (), match_name=False),
    Tech("vsam", "VSAM", "mainframe"),
    Tech("rexx", "REXX", "mainframe"),
    Tech("assembler", "Assembler", "mainframe", ("hlasm", "assembly language")),
    Tech("as400", "AS/400", "mainframe", ("iseries", "ibm i", "as400")),
    Tech("rpg", "RPG", "mainframe", ("rpgle", "rpg iv"), match_name=False),
    Tech("natural_adabas", "Natural/ADABAS", "mainframe", ("adabas", "software ag natural")),
    Tech("tso", "TSO", "mainframe", ("tso/ispf", "ispf")),
    Tech("mq", "IBM MQ", "mainframe", ("websphere mq", "mqseries")),
    Tech("endevor", "Endevor", "mainframe", ("ca endevor",)),
    Tech("changeman", "ChangeMan", "mainframe"),
]

BY_SLUG: dict[str, Tech] = {t.slug: t for t in TECHNOLOGIES}


def tracked() -> list[Tech]:
    """Technologies snapshotted daily to build the demand time series."""
    return [t for t in TECHNOLOGIES if t.track]


def _surface_forms(tech: Tech) -> list[str]:
    """Every string that should resolve to this technology."""
    forms = [a.lower() for a in tech.aliases]
    if tech.match_name:
        forms.insert(0, tech.name.lower())
    return forms


# Precompiled matchers. Word boundaries matter: without them "R" matches every
# word containing r, and "Go" matches "Google". Terms containing regex-special
# characters (C++, AI/ML, dbt) are escaped.
_MATCHERS: list[tuple[str, re.Pattern]] = []
for _t in TECHNOLOGIES:
    _alts = sorted({re.escape(f) for f in _surface_forms(_t)}, key=len, reverse=True)
    _MATCHERS.append((_t.slug, re.compile(r"(?<![\w/])(?:" + "|".join(_alts) + r")(?![\w/])",
                                          re.IGNORECASE)))


def match_technologies(text: str | None) -> list[str]:
    """
    Return canonical slugs for every technology mentioned in `text`.

    Deterministic and order-stable, so the same posting always produces the
    same list - a property the published index depends on.
    """
    if not text:
        return []
    return [slug for slug, pattern in _MATCHERS if pattern.search(text)]


def normalize(raw: str | None) -> str | None:
    """Map one free-text tool name (e.g. from the LLM) onto a canonical slug."""
    if not raw:
        return None
    needle = raw.strip().lower()
    for tech in TECHNOLOGIES:
        if needle in _surface_forms(tech):
            return tech.slug
    return None


if __name__ == "__main__":
    print(f"{len(TECHNOLOGIES)} technologies, {len(tracked())} tracked daily")
    by_cat: dict[str, int] = {}
    for t in TECHNOLOGIES:
        by_cat[t.category] = by_cat.get(t.category, 0) + 1
    for cat, n in sorted(by_cat.items(), key=lambda kv: -kv[1]):
        print(f"  {cat:<14} {n}")

    sample = ("Seeking a Data Engineer with strong Python and SQL. Experience with "
              "Snowflake, dbt, and Apache Airflow required. Familiarity with AI/ML "
              "and PySpark on AWS is a plus. Postgres knowledge helpful.")
    print("\nsample match:", match_technologies(sample))
