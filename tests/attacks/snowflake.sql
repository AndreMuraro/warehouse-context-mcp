-- Snowflake attack queries from the security review; each must be refused or masked.
select object_construct(*) o from dim_charterers
;;
select $1, $2 from @my_stage/file.csv
;;
select * from table(result_scan(last_query_id(-2)))
;;
select * from table(information_schema.query_history())
;;
select * from identifier('OTHER_DB.PUBLIC.SECRETS')
;;
select system$cancel_all_queries(123)
;;
select * from dim_charterers t, lateral flatten(input => object_construct(t.*)) f
;;
select * from snowflake.account_usage.query_history
;;
select * from "WCM_DEMO"."shipping"."dim_charterers"
;;
select hash(*) from dim_charterers
;;
select * from dim_vessels, table(result_scan(last_query_id(-2)))
;;
select count(contact_email::int) from dim_charterers
;;
select * from dim_vessels, lateral flatten(input => parse_json(system$get_snowflake_platform_info())) f
;;
select get_ddl('database', 'OTHER_DB') d
;;
select system$abort_session(1) a
;;
select * from table(generator(rowcount => 1e12))
