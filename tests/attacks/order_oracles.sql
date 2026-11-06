-- Second review (2026-11-02): ORDER BY oracles through an output alias that shadows a
-- PII column. The output is masked, so the leak is in the row order: each must be refused.
select charterer_name as contact_email from dim_charterers order by contact_email like 'a%'
;;
select charterer_id as contact_email from dim_charterers order by (contact_email like 'ops1%') desc
;;
select charterer_id, charterer_name as contact_email from dim_charterers order by length(contact_email)

;;
select charterer_id, charterer_id as contact_email from dim_charterers order by contact_email like '%ops5@%' desc limit 1
;;
select charterer_id, charterer_id as contact_email from dim_charterers order by contact_email like 'ops1%' desc, contact_email like 'ops1_@%' desc limit 3
;;
select charterer_id, 1 as contact_email from dim_charterers order by substring(contact_email, 1, 5) desc
;;
select charterer_id, 1 as contact_email from dim_charterers order by contact_email desc
;;
select charterer_id, 1 as contact_email from dim_charterers order by contact_email || '' desc

;;
select charterer_id from dim_charterers order by contact_email like 'ops5%' desc
;;
select charterer_id from dim_charterers order by contact_email desc

-- v0.5.1 (2026-11-06): an output that is the same column as its name may be ordered by.
-- These variants of that exception must stay refused: a PII column ordered by itself, a
-- name two tables share, two outputs with one name, an output from another table.
;;
select contact_email from dim_charterers order by contact_email like 'ops5%' desc
;;
select contact_email from dim_charterers order by contact_email
;;
select c.contact_email as contact_email from dim_charterers c order by length(contact_email), 1
;;
select charterer_id, upper(country) as contact_email, contact_email from dim_charterers order by contact_email like 'a%'
;;
select v.vessel_id as charterer_id, c.charterer_id from dim_vessels v join dim_charterers c on v.vessel_id = c.charterer_id order by charterer_id like '1%'
