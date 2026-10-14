# The base class for everything Rails reads but does not own.
#
# Subclasses map onto relations in the `public` schema that belong to dbt or to the
# Python pipeline. dbt DROPS and recreates its marts every night, so Rails may not
# add a column, an index, a grant or a foreign key to any of them - whatever it
# added would vanish at 07:00. The lesson is already recorded in
# service/sandbox.py:110-121, where a grant disappeared with a rebuilt table and the
# public page started answering "permission denied for table dim_company".
#
# Three layers keep this honest, and only the third is beyond the reach of a future
# mistake in this file:
#
#   1. `database_tasks: false` on the warehouse connection (config/database.yml)
#      takes it out of migrations, schema management and seeds entirely.
#   2. `replica: true` plus the `readonly?` override below make ActiveRecord refuse
#      writes.
#   3. The `signal_rails` Postgres role holds SELECT on two named tables and no
#      INSERT, UPDATE, DELETE or TRUNCATE anywhere in `public`.
#
# script/verify_isolation.rb proves all three by attempting forbidden writes and
# failing loudly if any succeeds.
class WarehouseRecord < ApplicationRecord
  self.abstract_class = true

  connects_to database: { writing: :warehouse, reading: :warehouse }

  # Belt and braces over `replica: true`. This is what makes `save`, `update!` and
  # `destroy` raise ActiveRecord::ReadOnlyRecord on an instance rather than
  # travelling to the database to be refused there.
  def readonly?
    true
  end

  class << self
    # Guard the class-level writers too. `update_all` and `delete_all` build SQL
    # directly and never consult an instance's `readonly?`.
    def update_all(*)
      raise ActiveRecord::ReadOnlyRecord, "#{name} is warehouse-owned and read-only"
    end

    def delete_all(*)
      raise ActiveRecord::ReadOnlyRecord, "#{name} is warehouse-owned and read-only"
    end

    def insert_all(*)
      raise ActiveRecord::ReadOnlyRecord, "#{name} is warehouse-owned and read-only"
    end
  end
end
