create table public.control_agent_performance (
  agent_id uuid primary key references public.agents(id) on delete cascade,
  calls_made integer not null default 0 check (calls_made >= 0),
  connected integer not null default 0 check (connected >= 0),
  conversion numeric(14, 2) not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create or replace function public.enforce_control_agent_performance()
returns trigger language plpgsql as $$
begin
  if not exists (select 1 from public.agents where id = new.agent_id and lower(role) = 'control agent') then
    raise exception 'Performance records can only be created for Control Agents';
  end if;
  new.updated_at = now();
  return new;
end;
$$;

create trigger control_agent_performance_guard
before insert or update on public.control_agent_performance
for each row execute function public.enforce_control_agent_performance();

create index control_agent_performance_updated_idx on public.control_agent_performance (updated_at desc);

create or replace function public.record_disposition(p_customer_id text, p_outcome text, p_status text, p_amount_rec numeric, p_agent_name text, p_comments text, p_business_status text, p_ptp_time text)
returns boolean language plpgsql security definer set search_path = public as $$
declare target_customer public.customers; target_agent uuid;
begin
  select * into target_customer from public.customers where customer_id = trim(p_customer_id) order by created_at limit 1 for update;
  select id into target_agent from public.agents where name = trim(p_agent_name) and lower(role) = 'control agent' limit 1 for update;
  if target_customer.campaign_id is null or target_agent is null then return false; end if;
  update public.customers set worked = true, outcome = coalesce(p_outcome, ''), status = coalesce(p_status, ''), business_status = coalesce(p_business_status, ''), ptp_amount = coalesce(p_amount_rec, 0), ptp_time = coalesce(p_ptp_time, '') where campaign_id = target_customer.campaign_id and customer_id = target_customer.customer_id;
  insert into public.dispositions (campaign_id, customer_id, agent_id, outcome, status, amount_rec, comments, business_status, ptp_time) values (target_customer.campaign_id, target_customer.customer_id, target_agent, coalesce(p_outcome, ''), coalesce(p_status, ''), coalesce(p_amount_rec, 0), coalesce(p_comments, ''), coalesce(p_business_status, ''), coalesce(p_ptp_time, ''));
  insert into public.control_agent_performance (agent_id, calls_made, connected, conversion)
  values (target_agent, 1, case when p_outcome = 'Answered' then 1 else 0 end, coalesce(p_amount_rec, 0))
  on conflict (agent_id) do update set
    calls_made = public.control_agent_performance.calls_made + 1,
    connected = public.control_agent_performance.connected + case when p_outcome = 'Answered' then 1 else 0 end,
    conversion = public.control_agent_performance.conversion + coalesce(p_amount_rec, 0),
    updated_at = now();
  return true;
end;
$$;

grant all on public.control_agent_performance to service_role;