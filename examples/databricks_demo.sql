-- Synthetic shipping demo warehouse for Databricks (Unity Catalog). Fully generated; no real data.
-- Run in the SQL editor on the Free Edition serverless warehouse. Default catalog: `workspace`.

create schema if not exists workspace.wcm_demo comment 'Synthetic shipping demo for warehouse-context-mcp';
use workspace.wcm_demo;

create or replace table dim_vessels comment 'One row per vessel in the fleet register.' as
select id + 1 as vessel_id,
       concat('MV Example ', lpad(cast(id + 1 as string), 3, '0')) as vessel_name,
       element_at(array('VLCC', 'Suezmax', 'Aframax', 'LR2', 'MR'), cast(id % 5 as int) + 1) as vessel_class,
       cast(40000 + floor(rand(42) * 280000) as int) as dwt_tonnes,
       cast(2005 + floor(rand(43) * 20) as int) as built_year,
       current_timestamp() - make_interval(0, 0, 0, 0, cast(1 + floor(rand(44) * 20) as int)) as updated_at
from range(40);

create or replace table dim_charterers comment 'Companies that charter vessels. Contact details are personal data.' as
select id + 1 as charterer_id,
       concat('Charterer ', char(65 + id)) as charterer_name,
       concat('ops', id + 1, '@charterer-', id + 1, '.example') as contact_email,
       element_at(array('NL', 'SG', 'US', 'BR', 'AE'), cast(id % 5 as int) + 1) as country,
       current_timestamp() - make_interval(0, 0, 0, cast(1 + floor(rand(45) * 60) as int)) as updated_at
from range(12);

create or replace table stg_voyages comment 'Voyages as received from the fixtures system, lightly cleaned.' as
select id + 1 as voyage_id,
       cast(1 + floor(rand(46) * 40) as int) as vessel_id,
       cast(1 + floor(rand(47) * 12) as int) as charterer_id,
       element_at(array('Rotterdam', 'Singapore', 'Houston', 'Santos'), cast(id % 4 as int) + 1) as load_port,
       element_at(array('Fujairah', 'Ningbo', 'Antwerp'), cast(id % 3 as int) + 1) as discharge_port,
       date_sub(current_date(), cast(1 + floor(rand(48) * 365) as int)) as laycan_start,
       cast(30000 + floor(rand(49) * 250000) as int) as cargo_tonnes,
       current_timestamp() - make_interval(0, 0, 0, 0, cast(1 + floor(rand(50) * 30) as int)) as loaded_at
from range(500);

create or replace table fct_voyage_economics
  comment 'Time-charter-equivalent economics per voyage. tce_usd_per_day = (revenue_usd - voyage_costs_usd) / duration_days.' as
select voyage_id, vessel_id, charterer_id, revenue_usd, voyage_costs_usd, duration_days,
       round((revenue_usd - voyage_costs_usd) / duration_days, 2) as tce_usd_per_day,
       computed_at
from (
  select voyage_id, vessel_id, charterer_id,
         round(cargo_tonnes * (8 + voyage_id % 17), 2) as revenue_usd,
         round(cargo_tonnes * (5 + voyage_id % 11), 2) as voyage_costs_usd,
         20 + voyage_id % 25 as duration_days,
         loaded_at as computed_at
  from stg_voyages
);

alter table dim_vessels alter column vessel_class comment 'Size class: VLCC, Suezmax, Aframax, LR2 or MR.';
alter table dim_vessels alter column dwt_tonnes comment 'Deadweight tonnage in metric tonnes.';
alter table dim_charterers alter column contact_email comment 'Operations contact. Personal data.';
alter table dim_charterers alter column country comment 'ISO 3166-1 alpha-2 country of the legal entity.';
alter table stg_voyages alter column cargo_tonnes comment 'Contracted cargo quantity in metric tonnes.';
alter table fct_voyage_economics alter column tce_usd_per_day comment 'Time-charter equivalent in USD per day.';

-- PII tag on the column (Unity Catalog governed tags).
alter table dim_charterers alter column contact_email set tags ('pii' = 'true');

select table_name, comment from workspace.information_schema.tables
where table_schema = 'wcm_demo' order by 1;
