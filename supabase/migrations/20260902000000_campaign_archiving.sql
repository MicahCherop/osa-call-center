alter table public.campaigns
  add column if not exists archived_at timestamptz;

drop view if exists public.campaign_summary;

create view public.campaign_summary with (security_invoker = true) as
select
  campaign.id,
  campaign.name,
  campaign.type,
  campaign.priority,
  campaign.start_date,
  campaign.end_date,
  campaign.date_added,
  campaign.archived_at,
  count(customer.customer_id)::integer as account_count
from public.campaigns campaign
left join public.customers customer on customer.campaign_id = campaign.id
group by campaign.id;