-- Attack queries from the independent security review (2026-11-02). Each must be
-- refused or return no PII, file contents or environment data. Separated by ';;'.
select * from dim_charterers limit 2
;;
select t from dim_charterers t limit 2
;;
select to_json(t) j from dim_charterers t limit 2
;;
select * from dim_charterers as t(a,b,c,d) limit 2
;;
select 1 a,'x' b,'y' c,'z' d, now() e union all select * from dim_charterers limit 3
;;
select max(columns('contact.*')) from dim_charterers
;;
select columns('contact.*') || '' from dim_charterers limit 2
;;
select * rename (contact_email as e) from dim_charterers limit 2
;;
select count(contact_email::int) from dim_charterers
;;
select count(case when contact_email like 'a%' then 1 end) from dim_charterers
;;
select * from query_table('dim_charterers') limit 2
;;
select * from query('select * from dim_charterers') limit 2
;;
unpivot (select * from dim_charterers limit 1) on columns(*::varchar)
;;
select string_agg(t::varchar, ' | ') from dim_charterers t

;;
select getenv('HOME') h
;;
select current_setting('home_directory') h, current_setting('enable_external_access') e
;;
select * from read_text('/etc/hostname')
;;
select * from '/etc/passwd'
;;
select (select content from read_text('/etc/hostname')) c
;;
select * from dim_vessels, read_text('/etc/hostname') limit 1
;;
with dim_vessels as (select * from read_text('/etc/hostname')) select * from dim_vessels
;;
with x as (select * from dim_charterers) select * from x as dim_vessels(a,b,c) limit 1
;;
select * from system.main.dim_vessels limit 1
;;
select * from duckdb_settings
;;
select * from information_schema.tables
;;
select * from unnest([1,2,3])
;;
select sum(1) from range(100000000000) r(i)
;;
select length(repeat('x', 500000000)) n
;;
select repeat('x', 100) || '' x from range(3)
;;
select * from dim_vessels limit 1 offset 0
;;
select * from dim_vessels fetch first 100000 rows only
;;
select * from (select * from range(1000000)) limit 10000000

;;
from read_text('/etc/hostname') select *
;;
select * from main.read_text('/etc/hostname')
;;
select * from read_text('/etc/hostname') as dim_vessels
;;
select * from dim_vessels positional join read_text('/etc/hostname')
;;
select * from (describe dim_vessels)
;;
select * from (show tables)
;;
select * from (summarize dim_charterers)
;;
select * from (pivot dim_vessels on vessel_class using count(*))
;;
select * from (from read_text('/etc/hostname'))
;;
select * from dim_vessels, lateral read_text('/etc/hostname')
;;
select * from table(read_text('/etc/hostname'))
;;
select * from "read_text"('/etc/hostname')
;;
select * from dim_vessels join read_text('/etc/hostname') using (x)
;;
select * from (select 1) t(dim_vessels), read_text('/etc/hostname')

;;
select * from dim_vessels, lateral read_text('/etc/hostname') limit 1
;;
select content from dim_vessels, lateral read_text('/proc/self/environ') limit 1
;;
select file from dim_vessels, lateral glob('/home/*') limit 5
;;
select column0 from dim_vessels, lateral read_csv('/etc/passwd', header=false, columns={'column0':'varchar'}) limit 3
;;
select * from (summarize dim_charterers)
;;
select * from (describe dim_charterers)
;;
select count(*) from dim_vessels, lateral range(100000000000) limit 1
;;
select name, value from dim_vessels, lateral duckdb_settings() where name like '%access%' limit 5
;;
select * from dim_vessels, lateral duckdb_secrets() limit 1

;;
select t.charterer_id, r.* from dim_charterers t, lateral read_text('/nonexistent/' || t.charterer_name) r limit 1
;;
select content[1:60] c from dim_vessels, lateral read_text('https://example.com/') limit 1
;;
select * from dim_vessels, lateral read_text('/tmp/*.yaml') limit 1
;;
select length(x) from (select repeat('x', 100000000) x)
;;
select list(t) l from dim_charterers t
;;
select * from dim_vessels limit 10 union all select * from dim_vessels

;;
select * from dim_vessels, lateral query('select contact_email, charterer_name from dim_charterers') limit 2
;;
select * from dim_vessels, lateral query('select database_name, path from duckdb_databases()') limit 3
;;
select * from (select 1) x, lateral query_table('dim_charterers') limit 1

;;
select count(case when contact_email like 'ops1@%' then 1 end) n from dim_charterers
;;
select count(case when substr(contact_email,1,4)='ops1' then 1 end) n from dim_charterers where charterer_id = 1

