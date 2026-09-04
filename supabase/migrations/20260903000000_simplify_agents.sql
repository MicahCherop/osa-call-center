alter table public.agents
  drop column if exists calls_made,
  drop column if exists connected,
  drop column if exists conversion,
  drop column if exists created_at,
  drop column if exists updated_at;

drop trigger if exists agents_updated_at on public.agents;

create or replace function public.record_disposition(p_customer_id text, p_outcome text, p_status text, p_amount_rec numeric, p_agent_name text, p_comments text, p_business_status text, p_ptp_time text)
returns boolean language plpgsql security definer set search_path = public as $$
declare target_customer public.customers; target_agent uuid;
begin
  select * into target_customer from public.customers where customer_id = trim(p_customer_id) order by created_at limit 1 for update;
  select id into target_agent from public.agents where name = trim(p_agent_name) limit 1 for update;
  if target_customer.campaign_id is null or target_agent is null then return false; end if;
  update public.customers set worked = true, outcome = coalesce(p_outcome, ''), status = coalesce(p_status, ''), business_status = coalesce(p_business_status, ''), ptp_amount = coalesce(p_amount_rec, 0), ptp_time = coalesce(p_ptp_time, '') where campaign_id = target_customer.campaign_id and customer_id = target_customer.customer_id;
  insert into public.dispositions (campaign_id, customer_id, agent_id, outcome, status, amount_rec, comments, business_status, ptp_time) values (target_customer.campaign_id, target_customer.customer_id, target_agent, coalesce(p_outcome, ''), coalesce(p_status, ''), coalesce(p_amount_rec, 0), coalesce(p_comments, ''), coalesce(p_business_status, ''), coalesce(p_ptp_time, ''));
  return true;
end;
$$;