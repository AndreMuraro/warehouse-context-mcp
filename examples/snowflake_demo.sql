-- Synthetic shipping demo warehouse for Snowflake. Fully generated; no real data.
-- Run in a Snowsight worksheet as a role that can create databases (ACCOUNTADMIN on a trial).
-- Uses an X-Small warehouse for a few seconds: negligible credit use.

create warehouse if not exists WCM_XS with warehouse_size = 'XSMALL' auto_suspend = 60 auto_resume = true;
use warehouse WCM_XS;
create database if not exists WCM_DEMO;
create schema if not exists WCM_DEMO.SHIPPING;
use schema WCM_DEMO.SHIPPING;

create or replace table dim_vessels comment = 'One row per vessel in the fleet register.' as
select seq4() + 1 as vessel_id,
       'MV Example ' || lpad((seq4() + 1)::varchar, 3, '0') as vessel_name,
       decode(mod(seq4(), 5), 0, 'VLCC', 1, 'Suezmax', 2, 'Aframax', 3, 'LR2', 'MR') as vessel_class,
       uniform(40000, 320000, random(42)) as dwt_tonnes,
       uniform(2005, 2024, random(43)) as built_year,
       dateadd(hour, -uniform(1, 20, random(44)), current_timestamp())::timestamp_ntz as updated_at
from table(generator(rowcount => 40));

create or replace table dim_charterers comment = 'Companies that charter vessels. Contact details are personal data.' as
select seq4() + 1 as charterer_id,
       'Charterer ' || char(65 + seq4()) as charterer_name,
       'ops' || (seq4() + 1) || '@charterer-' || (seq4() + 1) || '.example' as contact_email,
       decode(mod(seq4(), 5), 0, 'NL', 1, 'SG', 2, 'US', 3, 'BR', 'AE') as country,
       dateadd(day, -uniform(1, 60, random(45)), current_timestamp())::timestamp_ntz as updated_at
from table(generator(rowcount => 12));

create or replace table stg_voyages comment = 'Voyages as received from the fixtures system, lightly cleaned.' as
select seq4() + 1 as voyage_id,
       uniform(1, 40, random(46)) as vessel_id,
       uniform(1, 12, random(47)) as charterer_id,
       decode(mod(seq4(), 4), 0, 'Rotterdam', 1, 'Singapore', 2, 'Houston', 'Santos') as load_port,
       decode(mod(seq4(), 3), 0, 'Fujairah', 1, 'Ningbo', 'Antwerp') as discharge_port,
       dateadd(day, -uniform(1, 365, random(48)), current_date()) as laycan_start,
       uniform(30000, 280000, random(49)) as cargo_tonnes,
       dateadd(hour, -uniform(1, 30, random(50)), current_timestamp())::timestamp_ntz as loaded_at
from table(generator(rowcount => 500));

create or replace table fct_voyage_economics
  comment = 'Time-charter-equivalent economics per voyage. tce_usd_per_day = (revenue_usd - voyage_costs_usd) / duration_days.' as
select voyage_id, vessel_id, charterer_id,
       round(cargo_tonnes * (8 + mod(voyage_id, 17)), 2) as revenue_usd,
       round(cargo_tonnes * (5 + mod(voyage_id, 11)), 2) as voyage_costs_usd,
       20 + mod(voyage_id, 25) as duration_days,
       round((revenue_usd - voyage_costs_usd) / duration_days, 2) as tce_usd_per_day,
       loaded_at as computed_at
from stg_voyages;

comment on column dim_vessels.vessel_class is 'Size class: VLCC, Suezmax, Aframax, LR2 or MR.';
comment on column dim_vessels.dwt_tonnes is 'Deadweight tonnage in metric tonnes.';
comment on column dim_charterers.contact_email is 'Operations contact. Personal data.';
comment on column dim_charterers.country is 'ISO 3166-1 alpha-2 country of the legal entity.';
comment on column stg_voyages.cargo_tonnes is 'Contracted cargo quantity in metric tonnes.';
comment on column fct_voyage_economics.tce_usd_per_day is 'Time-charter equivalent in USD per day.';

-- PII tag (object tagging needs Enterprise edition, which trials include).
create tag if not exists pii comment = 'Column holds personal data';
alter table dim_charterers modify column contact_email set tag pii = 'true';

-- Least-privilege, read-only role for the MCP server.
create role if not exists WCM_READER;
grant usage on warehouse WCM_XS to role WCM_READER;
grant usage on database WCM_DEMO to role WCM_READER;
grant usage on schema WCM_DEMO.SHIPPING to role WCM_READER;
grant select on all tables in schema WCM_DEMO.SHIPPING to role WCM_READER;
set wcm_user = '"' || current_user() || '"';  -- quoted: user names can be case-sensitive
grant role WCM_READER to user identifier($wcm_user);

select table_name, row_count, comment from WCM_DEMO.information_schema.tables
where table_schema = 'SHIPPING' order by 1;
