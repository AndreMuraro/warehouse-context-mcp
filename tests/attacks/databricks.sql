-- Databricks attack queries from the security review; each must be refused or masked.
select to_json(struct(*)) j from dim_charterers
;;
select * from read_files('s3://bucket/path', format => 'csv')
;;
select secret('scope', 'key') s
;;
select * from csv.`/Volumes/main/x/y.csv`
;;
select * from identifier('other.schema.secrets')
;;
select * from json.`dbfs:/x`
;;
select http_request(conn => 'c', method => 'POST', path => '/x', json => 'y') r
;;
select * from dim_charterers t, lateral (select * from system.access.audit limit 1)
;;
select ai_query('endpoint', 'prompt') a
;;
select * from `workspace`.`WCM_DEMO`.dim_charterers
;;
select * from dim_vessels, lateral read_files('s3://bucket/path', format => 'csv')
;;
select * from dim_vessels, lateral explode(sequence(1, 100000000))
;;
select * from range(1000000000000)
;;
select * from dim_vessels, lateral range(10)
;;
select current_user() u, session_user() s
