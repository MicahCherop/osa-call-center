create extension if not exists pgcrypto;

create table public.agents (
  id uuid primary key default gen_random_uuid(), name text not null, email text not null unique,
  role text not null default 'Control Agent', status text not null default 'Active',
  calls_made integer not null default 0 check (calls_made >= 0), connected integer not null default 0 check (connected >= 0),
  conversion numeric(14, 2) not null default 0, created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create table public.campaigns (
  id uuid primary key default gen_random_uuid(), name text not null unique, type text not null, priority text not null,
  start_date date, end_date date, date_added date not null default current_date, created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create table public.customers (
  campaign_id uuid not null references public.campaigns(id) on delete cascade, customer_id text not null,
  name text not null, phone text not null default '', branch text not null default '', sector text not null default '',
  balance numeric(14, 2), due_date date, pair text not null default '', disb_amount numeric(14, 2), total_paid numeric(14, 2),
  assigned_agent_id uuid references public.agents(id) on delete set null, worked boolean not null default false,
  outcome text not null default '', status text not null default '', business_status text not null default '', ptp_amount numeric(14, 2), ptp_time text not null default '',
  feedback text not null default '', days_inactive integer, days_dormant integer, loyalty text not null default '', last_loan_amount numeric(14, 2),
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(), primary key (campaign_id, customer_id)
);
create table public.dispositions (
  id uuid primary key default gen_random_uuid(), campaign_id uuid not null, customer_id text not null,
  agent_id uuid references public.agents(id) on delete set null, outcome text not null, status text not null default '', amount_rec numeric(14, 2) not null default 0,
  comments text not null default '', business_status text not null default '', ptp_time text not null default '', created_at timestamptz not null default now(),
  foreign key (campaign_id, customer_id) references public.customers(campaign_id, customer_id) on delete cascade
);
create index customers_queue_idx on public.customers (assigned_agent_id, worked, campaign_id);
create index customers_campaign_worked_idx on public.customers (campaign_id, worked);
create index dispositions_customer_idx on public.dispositions (campaign_id, customer_id, created_at desc);
create index agents_role_status_idx on public.agents (role, status);

create or replace function public.set_updated_at() returns trigger language plpgsql as $$
begin new.updated_at = now(); return new; end;
$$;
create trigger agents_updated_at before update on public.agents for each row execute function public.set_updated_at();
create trigger campaigns_updated_at before update on public.campaigns for each row execute function public.set_updated_at();
create trigger customers_updated_at before update on public.customers for each row execute function public.set_updated_at();

create view public.campaign_summary with (security_invoker = true) as
select campaign.id, campaign.name, campaign.type, campaign.priority, campaign.start_date, campaign.end_date, campaign.date_added, count(customer.customer_id)::integer as account_count
from public.campaigns campaign left join public.customers customer on customer.campaign_id = campaign.id group by campaign.id;

create or replace function public.assign_customer(p_customer_id text, p_agent_name text)
returns boolean language plpgsql security definer set search_path = public as $$
declare target_customer public.customers; target_agent uuid;
begin
  select id into target_agent from public.agents where name = trim(p_agent_name) limit 1;
  select * into target_customer from public.customers where customer_id = trim(p_customer_id) order by created_at limit 1 for update;
  if target_agent is null or target_customer.campaign_id is null then return false; end if;
  update public.customers set assigned_agent_id = target_agent where campaign_id = target_customer.campaign_id and customer_id = target_customer.customer_id;
  return true;
end;
$$;

create or replace function public.distribute_campaign(p_campaign_name text, p_agent_names text[])
returns integer language plpgsql security definer set search_path = public as $$
declare target_campaign uuid; eligible_agents uuid[]; assigned_count integer;
begin
  select id into target_campaign from public.campaigns where name = trim(p_campaign_name);
  if target_campaign is null then return 0; end if;
  select array_agg(id order by name) into eligible_agents from public.agents where name = any(p_agent_names) and lower(role) = 'control agent' and lower(status) in ('clocked in', 'online');
  if coalesce(array_length(eligible_agents, 1), 0) = 0 then return 0; end if;
  with waiting as (
    select campaign_id, customer_id, row_number() over (order by created_at, customer_id) - 1 as sequence from public.customers
    where campaign_id = target_campaign and assigned_agent_id is null and not worked for update
  ), updated as (
    update public.customers customer set assigned_agent_id = eligible_agents[(waiting.sequence % array_length(eligible_agents, 1)) + 1]
    from waiting where customer.campaign_id = waiting.campaign_id and customer.customer_id = waiting.customer_id returning 1
  ) select count(*) into assigned_count from updated;
  return assigned_count;
end;
$$;

create or replace function public.record_disposition(p_customer_id text, p_outcome text, p_status text, p_amount_rec numeric, p_agent_name text, p_comments text, p_business_status text, p_ptp_time text)
returns boolean language plpgsql security definer set search_path = public as $$
declare target_customer public.customers; target_agent uuid;
begin
  select * into target_customer from public.customers where customer_id = trim(p_customer_id) order by created_at limit 1 for update;
  select id into target_agent from public.agents where name = trim(p_agent_name) limit 1 for update;
  if target_customer.campaign_id is null or target_agent is null then return false; end if;
  update public.customers set worked = true, outcome = coalesce(p_outcome, ''), status = coalesce(p_status, ''), business_status = coalesce(p_business_status, ''), ptp_amount = coalesce(p_amount_rec, 0), ptp_time = coalesce(p_ptp_time, '') where campaign_id = target_customer.campaign_id and customer_id = target_customer.customer_id;
  insert into public.dispositions (campaign_id, customer_id, agent_id, outcome, status, amount_rec, comments, business_status, ptp_time) values (target_customer.campaign_id, target_customer.customer_id, target_agent, coalesce(p_outcome, ''), coalesce(p_status, ''), coalesce(p_amount_rec, 0), coalesce(p_comments, ''), coalesce(p_business_status, ''), coalesce(p_ptp_time, ''));
  update public.agents set calls_made = calls_made + 1, connected = connected + case when p_outcome = 'Answered' then 1 else 0 end, conversion = conversion + coalesce(p_amount_rec, 0) where id = target_agent;
  return true;
end;
$$;

revoke all on all tables in schema public from anon, authenticated;
revoke execute on function public.assign_customer(text, text) from public;
revoke execute on function public.distribute_campaign(text, text[]) from public;
revoke execute on function public.record_disposition(text, text, text, numeric, text, text, text, text) from public;
grant usage on schema public to service_role;
grant all on all tables in schema public to service_role;
grant execute on function public.assign_customer(text, text) to service_role;
grant execute on function public.distribute_campaign(text, text[]) to service_role;
grant execute on function public.record_disposition(text, text, text, numeric, text, text, text, text) to service_role;