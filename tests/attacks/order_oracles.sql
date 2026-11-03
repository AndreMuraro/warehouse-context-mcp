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
