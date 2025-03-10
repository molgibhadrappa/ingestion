import logging
from google.cloud import bigquery
from google.cloud.exceptions import NotFound

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SQLToBigQueryProcessor:
    def __init__(self, project, dataset, state_table_name, cloudsql_table_name, filter_column=None, mode='incremental', change_log_indicator=None):
        self.project = project
        self.dataset = dataset
        self.state_table_name = state_table_name
        self.cloudsql_table_name = cloudsql_table_name
        self.filter_column = filter_column  # Used for batch processing
        self.mode = mode  # 'incremental' or 'full'
        self.change_log_indicator = change_log_indicator  # Used in WHERE clause
        self.client = bigquery.Client()
        self.state_table_id = f'{self.project}.{self.dataset}.{self.state_table_name}'
        self.create_state_table_if_not_exists()

    def create_state_table_if_not_exists(self):
        """Create state table if it does not exist"""
        try:
            self.client.get_table(self.state_table_id)
        except NotFound:
            schema = [
                bigquery.SchemaField("cloudsql_table_name", "STRING", mode="REQUIRED"),
                bigquery.SchemaField("filter_column", "STRING", mode="NULLABLE"),
                bigquery.SchemaField("last_processed", "STRING", mode="REQUIRED"),
                bigquery.SchemaField("mode", "STRING", mode="REQUIRED"),
            ]
            table = bigquery.Table(self.state_table_id, schema=schema)
            self.client.create_table(table)
            logger.info(f"Created state table {self.state_table_id}")

    def get_last_processed_change(self, cloudsql_table_name):
        """Get the last processed `change_log_indicator` per table"""
        query = f"""
        SELECT MAX(last_processed) as last_processed FROM `{self.state_table_id}`
        WHERE cloudsql_table_name = '{cloudsql_table_name}'
        """
        query_job = self.client.query(query)
        rows = list(query_job.result())
        return rows[0].last_processed if rows else None

    def process_column(self):
        """Process data, batch loading if `filter_column` is available"""
        max_change_log_indicator = None  # Track overall max

        if self.filter_column:
            distinct_values_query = f"""
            SELECT DISTINCT {self.filter_column} FROM `{self.project}.{self.dataset}.{self.cloudsql_table_name}`
            """
            query_job = self.client.query(distinct_values_query)
            distinct_values = [row[self.filter_column] for row in query_job.result()]

            if not distinct_values:
                logger.info(f"No distinct values found for {self.filter_column}, skipping further processing.")
                return

            logger.info(f"Found {len(distinct_values)} distinct values to process.")

            for filter_column_value in distinct_values:
                self.process_data(filter_column_value)

            # Get the max `change_log_indicator` after processing all batches
            max_change_log_indicator = self.get_max_change_log_indicator()
        else:
            self.process_data()
            max_change_log_indicator = self.get_max_change_log_indicator()

        # Only update last_processed AFTER all batches have been processed
        if max_change_log_indicator:
            self.update_last_processed_change(self.cloudsql_table_name, max_change_log_indicator, self.mode)

    def process_data(self, filter_column_value=None):
        """Process and insert data into BigQuery from Cloud SQL external table"""
        temp_table_id = f'{self.project}.{self.dataset}.temp_{self.cloudsql_table_name}'
        raw_table_id = f'{self.project}.{self.dataset}.raw_table_{self.cloudsql_table_name}'

        last_processed_change = self.get_last_processed_change(self.cloudsql_table_name)
        filter_condition = f"AND {self.change_log_indicator} > '{last_processed_change}'" if last_processed_change else ""
        filter_column_condition = f"AND {self.filter_column} = '{filter_column_value}'" if filter_column_value else ""

        # Query to get data from Cloud SQL external table
        sql_query = f"""
        CREATE OR REPLACE TEMP TABLE `{temp_table_id}` AS
        SELECT * FROM `{self.project}.{self.dataset}.{self.cloudsql_table_name}`
        WHERE 1=1
        {filter_column_condition}
        {filter_condition}
        """

        # Execute the query to create the temp table
        self.client.query(sql_query).result()
        logger.info(f"Temporary table created with data from {self.cloudsql_table_name}.")

        # Insert into raw table, ensuring all columns are cast to STRING
        self.insert_into_raw_table(temp_table_id, raw_table_id)

    def insert_into_raw_table(self, temp_table_id, raw_table_id):
        """Insert processed data into the final BigQuery raw table"""
        temp_table = self.client.get_table(temp_table_id)
        columns = [field.name for field in temp_table.schema]

        # Convert all columns to STRING to handle schema changes safely
        cast_columns = ", ".join([f"CAST({col} AS STRING) AS {col}" for col in columns])

        insert_query = f"""
        INSERT INTO `{raw_table_id}` ({", ".join(columns)}, bq_load_timestamp)
        SELECT {cast_columns}, CURRENT_TIMESTAMP() FROM `{temp_table_id}`
        """
        
        query_job = self.client.query(insert_query)
        query_job.result()
        logger.info(f"Inserted data into {raw_table_id} from temporary table {temp_table_id}.")

        # Cleanup the temporary table
        self.client.query(f"DROP TABLE `{temp_table_id}`").result()
        logger.info(f"Temporary table {temp_table_id} dropped after insert.")

    def get_max_change_log_indicator(self):
        """Get the maximum `change_log_indicator` from the raw table after all batch inserts"""
        raw_table_id = f'{self.project}.{self.dataset}.raw_table_{self.cloudsql_table_name}'

        max_change_log_query = f"""
        SELECT MAX({self.change_log_indicator}) AS max_change_log_indicator FROM `{raw_table_id}`
        """
        query_job = self.client.query(max_change_log_query)
        rows = list(query_job.result())
        return rows[0].max_change_log_indicator if rows else None

    def update_last_processed_change(self, cloudsql_table_name, max_change_log_indicator, mode):
        """MERGE into state table: update `last_processed` if exists, insert otherwise"""
        merge_query = f"""
        MERGE INTO `{self.state_table_id}` AS target
        USING (SELECT '{cloudsql_table_name}' AS cloudsql_table_name, 
                      '{max_change_log_indicator}' AS last_processed, 
                      '{mode}' AS mode) AS source
        ON target.cloudsql_table_name = source.cloudsql_table_name
        WHEN MATCHED THEN
          UPDATE SET last_processed = source.last_processed
        WHEN NOT MATCHED THEN
          INSERT (cloudsql_table_name, last_processed, mode) 
          VALUES (source.cloudsql_table_name, source.last_processed, source.mode)
        """

        query_job = self.client.query(merge_query)
        query_job.result()
        logger.info(f"Updated last processed `change_log_indicator`: {max_change_log_indicator}")






procedure:
CREATE OR REPLACE PROCEDURE `your_project.your_dataset.load_from_cloudsql`
(
    IN table_name STRING,                   -- Target raw table in BigQuery
    IN change_log_indicator STRING,         -- Column used for incremental filtering
    IN connection_id STRING,                -- BigQuery Connection ID (Cloud SQL)
    IN filter_column STRING                  -- Column used for batch loading (e.g., region, category)
)
BEGIN
    DECLARE max_change_log STRING;
    DECLARE sql_query STRING;
    DECLARE target_bigquery_table STRING;
    DECLARE column_list STRING;
    DECLARE casted_column_list STRING;
    DECLARE alter_table_statements STRING;
    DECLARE filter_values ARRAY<STRING>;
    DECLARE filter_value STRING;
    DECLARE table_exists BOOL;

    -- Define Target BigQuery Raw Table (Same as External Table)
    SET target_bigquery_table = FORMAT("your_project.your_dataset.%s", table_name);

    -- Check if Target Table Exists
    SET table_exists = (
        SELECT COUNT(1) > 0
        FROM `your_project.region.INFORMATION_SCHEMA.TABLES`
        WHERE table_schema = 'your_dataset'
        AND table_name = table_name
    );

    -- If Table Doesn't Exist, Fetch Schema from Cloud SQL & Create Table
    IF table_exists = FALSE THEN
        -- Get column names from Cloud SQL using EXTERNAL_QUERY
        SET column_list = (
            SELECT STRING_AGG(FORMAT("%s STRING", column_name), ", ")
            FROM (
                SELECT column_name
                FROM EXTERNAL_QUERY(connection_id, """
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_schema = DATABASE() 
                    AND table_name = ?
                """) USING table_name
            )
        );

        -- Create Table with Retrieved Schema
        EXECUTE IMMEDIATE FORMAT("""
            CREATE TABLE `%s` (%s, bq_load_timestamp TIMESTAMP)
        """, target_bigquery_table, column_list);
    END IF;

    -- Get max change_log_indicator from the raw table (incremental load)
    SET max_change_log = (
        SELECT COALESCE(MAX(change_log_indicator), '1900-01-01 00:00:00')
        FROM UNNEST([EXECUTE IMMEDIATE FORMAT("""
            SELECT MAX(%s) FROM `%s`
        """, change_log_indicator, target_bigquery_table)])
    );

    -- Get column names from the Cloud SQL external table
    SET column_list = (
        SELECT STRING_AGG(column_name, ', ')
        FROM EXTERNAL_QUERY(connection_id, """
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_schema = DATABASE() 
            AND table_name = ?
        """) USING table_name
    );

    -- Construct column list with CAST to STRING
    SET casted_column_list = (
        SELECT STRING_AGG(FORMAT("CAST(%s AS STRING) AS %s", column_name, column_name), ', ')
        FROM EXTERNAL_QUERY(connection_id, """
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_schema = DATABASE() 
            AND table_name = ?
        """) USING table_name
    );

    -- Construct ALTER TABLE statements to add missing columns dynamically
    SET alter_table_statements = (
        SELECT STRING_AGG(FORMAT("""
            ALTER TABLE `%s` ADD COLUMN IF NOT EXISTS %s STRING
        """, target_bigquery_table, column_name), ' ')
        FROM (
            SELECT column_name
            FROM EXTERNAL_QUERY(connection_id, """
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_schema = DATABASE() 
                AND table_name = ?
            """) USING table_name
            WHERE column_name NOT IN (
                SELECT column_name
                FROM `your_project.region.INFORMATION_SCHEMA.COLUMNS`
                WHERE table_schema = 'your_dataset'
                AND table_name = table_name
            )
        )
    );

    -- Execute ALTER TABLE to add new columns if any exist
    IF alter_table_statements IS NOT NULL THEN
        EXECUTE IMMEDIATE alter_table_statements;
    END IF;

    -- Get distinct values for the batch filter column from Cloud SQL dynamically
    SET filter_values = ARRAY(
        SELECT DISTINCT filter_column FROM (
            SELECT * FROM EXTERNAL_QUERY(
                connection_id, 
                "SELECT DISTINCT " || filter_column || " FROM ?"
            ) USING table_name
        )
    );

    -- Iterate over each distinct value and load data in batches
    FOR filter_value IN (SELECT * FROM UNNEST(filter_values)) DO
        -- Construct SQL Query to Read from Cloud SQL via EXTERNAL_QUERY for the batch
        SET sql_query = FORMAT("""
            SELECT %s, CURRENT_TIMESTAMP() AS bq_load_timestamp 
            FROM EXTERNAL_QUERY('%s', 
            "SELECT * FROM ? 
             WHERE %s > '%s' 
             AND %s = ?") 
        """, casted_column_list, connection_id, table_name, change_log_indicator, max_change_log, filter_column)
        USING filter_value;

        -- Load Data into BigQuery Raw Table with bq_load_timestamp
        EXECUTE IMMEDIATE FORMAT("""
            INSERT INTO `%s` (%s, bq_load_timestamp) SELECT * FROM (%s)
        """, target_bigquery_table, column_list, sql_query);
        
        -- Log batch processing
        INSERT INTO `your_project.your_dataset.batch_log_table` (table_name, filter_column, filter_value, processed_at)
        VALUES (table_name, filter_column, filter_value, CURRENT_TIMESTAMP());

    END FOR;
END;


CALL `your_project.your_dataset.load_from_cloudsql`(
    'orders',                         -- Table Name (Cloud SQL & BigQuery)
    'updated_at',                      -- Change Log Indicator Column
    'my_project.us.my_connection',     -- Cloud SQL External Connection ID
    'region'                           -- Filter Column (for batch processing)
);
