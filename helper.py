from configparser import ConfigParser
from datetime import datetime
import time
import urllib.parse
import subprocess
import csv
import logging
import sys

logging.basicConfig(
    level=logging.DEBUG,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(f'migration_logs_{datetime.now().strftime("%Y-%m-%d")}'),
        logging.StreamHandler(sys.stdout),
    ],
)


def build_config(config_file):
    migration_config = {}
    parser = ConfigParser()
    parser.read(config_file)
    for section in ["source", "target"]:
        if not parser.has_section(section):
            raise Exception(f"Section {section} not found in the file: {config_file}")
        migration_config[section] = {}
        params = parser.items(section)
        for param in params:
            migration_config[section][param[0]] = param[1]
    return migration_config


def build_connection_string(db_config):
    conn_string = f'postgres://{urllib.parse.quote(db_config["user"])}:{urllib.parse.quote(db_config["password"])}'
    conn_string += f'@{urllib.parse.quote(db_config["host"])}:{urllib.parse.quote(db_config["port"])}'
    conn_string += f'/{urllib.parse.quote(db_config["database"])}?sslmode={urllib.parse.quote(db_config["sslmode"])}'
    return conn_string


def spit_out_schema_files(schema_file_name, config_file_name):
    f = open(schema_file_name)
    lines = [line for line in f.readlines() if line.strip()]
    f.close()

    queries = []
    query = []
    for i in range(len(lines) - 1):
        line = lines[i]
        query.append(line)
        if not line.startswith("--"):
            if lines[i + 1].startswith("--"):
                queries.append(query)
                query = []

    queries_index = []
    queries_tables = []
    for query in queries:
        is_index_query = False
        for line in query:
            if "CREATE INDEX" in line:
                is_index_query = True
                queries_index.append(query)
                break
        if not is_index_query:
            queries_tables.append(query)

    schema_no_indexes = f"schema_no_indexes_{config_file_name}.sql"
    out1 = open(schema_no_indexes, "w+")
    for query in queries_tables:
        for line in query:
            out1.writelines(line)
    out1.close()

    out2 = open(f"schema_indexes_only_{config_file_name}.sql", "w+")
    for query in queries_index:
        for line in query:
            out2.writelines(line)
    out2.close()


def migrate_schema(config_file_name, create_indexes):
    migration_config = build_config(config_file_name)
    source_conn_url = build_connection_string(migration_config["source"])
    target_conn_url = build_connection_string(migration_config["target"])
    schema_file_name = f"schema_{config_file_name}.sql"
    print(
        f'Geting latest schema from source db: {migration_config["source"]["host"]}/{migration_config["source"]["database"]}'
    )
    subprocess.check_output(
        f'rm -v {schema_file_name};pg_dump --schema-only "{source_conn_url}" > {schema_file_name}',
        shell=True,
    )
    spit_out_schema_files(schema_file_name, config_file_name)
    schema_no_indexes_file_name = f"schema_no_indexes_{config_file_name}.sql"
    sync_schema_file_name = (
        schema_file_name if create_indexes == "True" else schema_no_indexes_file_name
    )
    print(f"Sync schema with no indexes in target db...")
    subprocess.check_output(
        f'psql "{target_conn_url}" < {sync_schema_file_name}', shell=True
    )


def migrate_roles(config_file_name):
    # TODO
    print("This feature is to be implemented. ")


def logging_thread(statement, thread):
    if thread:
        thread_msg = f"[thread={thread}]"
    else:
        thread_msg = ""
    logging.debug(f"{thread_msg} {statement}")


def get_duration(start_time):
    return time.strftime("%H:%M:%S:", time.gmtime(time.time() - start_time))


def parse_table_files(table_file):
    messages = []
    message_set = set()
    with open(table_file, "r") as f:
        lines = list(f.read().splitlines())
        for line in lines:
            line = line.strip()
            if "|" in line:
                # TODO: validate line format:
                # schema.tablename
                # schema.tablename|columnname|I|range1,range2
                # schema.tablename|columnname|I|range1,
                # schema.tablename|columnname|V|value
                if line in message_set:
                    logging.warning(f"Duplicated table parition by range: {line}")
                    return
                message_set.add(line.split("|")[0])
            else:
                if line in message_set:
                    logging.warning(f"Duplicated table: {line}", "ERROR")
                    return
            message_set.add(line)
            messages.append(line)
    f.close()
    return messages


def migrate_pg_dump_restore(thread, table, source_conn_url, target_conn_url):
    truncate_command = f'psql "{target_conn_url}" -c "TRUNCATE table {table}"'
    logging_thread(f"Truncating target table: {table} ... ", thread)
    subprocess.check_output(truncate_command, shell=True)
    dump_restore_command = f'pg_dump -t {table} -Fc --compress=0 -d "{source_conn_url}" | pg_restore --no-acl --no-owner --data-only -d "{target_conn_url}"'
    logging_thread(f"Running `pg_dump | pg_restore` for table: {table} ... ", thread)
    subprocess.check_output(dump_restore_command, shell=True)


def migrate_copy_by_partition(thread, message, source_conn_url, target_conn_url):
    condition_string = ""
    table, column, type, value = message.split("|")
    if "V" == type:
        condition_string = f"{column} = '{value}'"
    elif "I" == type:
        interval_0, interval_1 = [interval.strip() for interval in value.split(",")]
        if interval_1 == "":
            condition_string = f"{column} >= '{interval_0}'"
        else:
            condition_string = (
                f"{column} >= '{interval_0}' AND {column} < '{interval_1}'"
            )
    delete_command = (
        f'psql "{target_conn_url}" -c "DELETE FROM {table} WHERE {condition_string};"'
    )
    logging_thread(
        f"Deleting from target table: {table}, WHERE by: {condition_string} ...",
        thread,
    )
    subprocess.check_output(delete_command, shell=True)
    logging_thread(
        f"Copying table: {table}, WHERE: {condition_string} ... ", thread
    )
    select_query = f"\COPY (SELECT * from {table} WHERE {condition_string}) TO STDOUT;"
    read_query = f'psql "{source_conn_url}" -c "{select_query}"'
    write_query = f'psql "{target_conn_url}" -c "\COPY {table} FROM STDIN;"'
    copy_command = f"{read_query} | {write_query} >> /dev/null"
    subprocess.check_output(copy_command, shell=True)


def log_migration_jobs_status(message, status, duration):
    f = open(f"migration_jobs_status.tsv", "a+")
    writer = csv.writer(f, delimiter="\t")
    writer.writerow([message, status, datetime.now(), duration])
    f.close()
