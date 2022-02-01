# Flexy
## Introduction
A command-line tool for faster PG to PG offline parallel migration. Run from any VM that can use `psql` to connect to migrating db servers.
## Set up
* In your vm workspace, 
```
    git clone https://github.com/ljiang001/Flexy.git
```
* Install the following if not yet installed.
    * python3
    * postgresql-client
    * pip3
* `cd` to the folder
* Install python packages:
```
    pip3 intsall -r requirements.txt
```  
### Pre-Migration
1. Config: Fill source/target db configuration in the `config.ini`. Can copy to any new config file for different migration project but must follow the template.
2. Migrate Schema (skip if tables already migrated)
    * Create target database in target db server
    * Migrate schema:<br>
     ```
     python3 pre_migration.py --config-file=yourconfigfile --function=migrate_schema
     ```
    * *Flags:*
        * required: `--function` (`-f`)(choice: `migrate_schema`, `create_list`, `create_parts`)
        * optional:
            * `--config-file`(`-i`) (default: `config.ini`)
            * `--indexes=True`(`-i`) (default: `False`)
    * This command will generate 3 files:
        * `schema_{config-file-name}.sql`
        * `schema_no_indexes_{config-file-name}.sql`
        * `schema_indexes_only_{config-file-name}.sql`
    
        if `--indexes=True`: migrate with indexes
        <br>else: migrate with no indexes
3. (optional) Create a list of all tables from the source database order by descending data size
    * Create list:<br>
     ```
     python3 pre_migration.py --config-file=yourconfigfile --function=create_list
     ```
    * This command will generate 2 files:
        * `tables_{config-file-name}`
        * `tables_size_{config-file-name}.tsv`
    * Query used in the function: 
    ```
    SELECT schemaname||'.'||relname as schema_table, pg_size_pretty(pg_relation_size(relid)) AS data_size
    FROM pg_catalog.pg_statio_user_tables
    ORDER BY pg_relation_size(relid) DESC;
     ```
4. (optional) Create partitions for large tables based on an indexed monotonically increasing column (e.g., id column) (OR) a timestamp column (e.g., created_at, updated_at, etc). 
    * Create a file containing tables and columns, each line formatted as following:
    ```
    schemaname.tablename|columnname
    ```
    * Create parts:
    ```
    python3 pre_migration.py --config-file=yourconfigfile --function=create_parts --tables-file=yourtablefile
    ```
    * This command will generate a file containing a list of migration job string used in the `migrate_parallel.py` function, which divide a large table into multiple migration jobs based on the watermark column and table size.  
    * Create the index for the column if not yet created in source and target tables 
    * **Note**: 
        * strongly recommand for table size large than 100 GB
        * Recommand for table size large than 50 GB
        * The function assumes the data are evenly distributed by the id/timestamp. The average size of each chunk will be 10 GB. 
5. Create a file for selected tables to migrate:
    * Four different string formats are accepted in each line <br>
        | line in file | translation in program |
        | -----------  |----------- |
        |`schemaname.tablename` |`SELECT * FROM schemaname.tablename`|
        |`schemaname.tablename\|columnname\|I\|,interval_0`|`SELECT * FROM schemaname.tablename WHERE columnname < 'interval_1'` <br>(no lower bound, exclusive upper bound)|
        |`schemaname.tablename\|columnname\|I\|interval_0,interval_1`|`SELECT * FROM schemaname.tablename WHERE columnname >= 'interval_0' AND columnname < 'interval_1'` <br>[inclusive lower bound, exclusive upper bound)|
        |`schemaname.tablename\|columnname\|I\|interval_0,`| `SELECT * FROM schemaname.tablename WHERE columnname >= 'interval_0'` <br> [inclusive lower bound, no upper bound)|
        |`schemaname.tablename\|columnname\|V\|value`| `SELECT * FROM schemaname.tablename WHERE columnname = 'value'`|
        |`schemaname.tablename\|columnname\|V\|NULL`| `SELECT * FROM schemaname.tablename WHERE columnname IS NULL`|
    * Example file:
         ```
         public.orders
         public.items|id|I|1,50000000
         public.items|id|I|50000000,
         public.customers|country|V|US
         ```  
    * **Tips**: <br>
        Put the large partitioned tables together on the top. The rest should sort by descending data size. 
4. Disable triggers and foreign keys validation
    * In target server: Change server parameter `session_replication_role` TO `REPLICA` in global server level
5. Optional performance tuning:
    * In target server: Increase `max_wal_size` to largest value
    * In target server: Increase `maintenance_work_mem` to largest value
    * In target & source servers: Increase `max_parallel_workers_per_gather`
    * In target & source servers: Scale up vCores


| :warning: WARNING          |
|:---------------------------|
| Schemas and Tables must be created in target database before starting migration   |


## Migration
* Migrate tables in Parallel
    ```
    python3 migrate_parallel.py --config-file=yourconfigfile --queue-file=yourtablesfile
    ````
    * *Flags:*
        * required: `--queue-file`(`-q`)
        * optional:
            * `--config-file` (`-c`) (default: config.ini) 
            * `--number-thread` (`-n`) (default: `20`)
    * In each migration, each table will be truncated or deleted for the partitoned part
    * Tracking job status
        * The status of each migration job will be appended to local file `migration_jobs_status.tsv`.
    * Re-Run
        * Any migration jobs logged as `success` in `migration_jobs_status.tsv` will not be re-migrated
        * To re-run the whole migration process, remove `migration_jobs_status.tsv`
* Migrate single table
    ```
    python3 migrate_single.py --config-file=yourconfigfile --table=schema.table
    ```
    * Will migrate/re-migrate the table regardless if it's already logged as success in `migration_jobs_status.tsv`
* Logging
    * You can check logs saved in `migration_logs_{date}` during/after migration.
* Monitor
    * Monitor CPUs in your source/target db servers
* Add indexes during migration
    * To reduce entire migration spanning time, you can add indexes back for tables that already completely migrate while still running migration for other tables
## Post Migration
* Add all indexes back
* Alter sequences restart ids
* Revert changed server parameter values
* Scale down resources that are not needed
## Feedback
* Send email to lingjiang@microsoft.com if you encounter any issues/question or have any feature request. Thank you!
