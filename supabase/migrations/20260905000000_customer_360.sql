-- Customer 360: normalized tags, structured agent notes, and per-disposition follow-up history.

alter table public.dispositions
  add column if not exists follow_up_at timestamptz;

create table if not exists public.customer_tag_definitions (
  code text primary key,
  label text not null,
  created_at timestamptz not null default now()
);
insert into public.customer_tag_definitions (code, label) values
  ('high_value', 'High Value'), ('high_risk', 'High Risk'), ('ptp', 'PTP'),
  ('follow_up', 'Follow-up'), ('dormant', 'Dormant'), ('repeat_default', 'Repeat Default'),
  ('callback', 'Callback')
on conflict (code) do nothing;

create table if not exists public.customer_tags (
  campaign_id uuid not null,
  customer_id text not null,
  tag_code text not null references public.customer_tag_definitions(code) on delete cascade,
  added_by uuid references public.agents(id) on delete set null,
  added_at timestamptz not null default now(),
  primary key (campaign_id, customer_id, tag_code),
  foreign key (campaign_id, customer_id) references public.customers(campaign_id, customer_id) on delete cascade
);
create index if not exists customer_tags_lookup_idx on public.customer_tags (campaign_id, customer_id);

create table if not exists public.customer_notes (
  id uuid primary key default gen_random_uuid(),
  campaign_id uuid not null,
  customer_id text not null,
  agent_id uuid references public.agents(id) on delete set null,
  note text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (campaign_id, customer_id) references public.customers(campaign_id, customer_id) on delete cascade
);
create index if not exists customer_notes_lookup_idx on public.customer_notes (campaign_id, customer_id, created_at desc);

drop trigger if exists customer_notes_updated_at on public.customer_notes;
create trigger customer_notes_updated_at before update on public.customer_notes for each row execute function public.set_updated_at();

-- Speeds up Customer 360 contact-history pagination (already indexed for campaign_id+customer_id,
-- this adds the reverse lookup used when auditing a single customer's activity across campaigns).
create index if not exists dispositions_created_idx on public.dispositions (created_at desc);

-- record_disposition now also stores the follow-up timestamp against the disposition itself, so
-- Customer 360 can show which follow-up was set at which historical call (not just the latest one).
create or replace function public.record_disposition(p_customer_id text, p_outcome text, p_status text, p_amount_rec numeric, p_agent_name text, p_comments text, p_business_status text, p_ptp_time text, p_follow_up_at timestamptz default null)
returns boolean language plpgsql security definer set search_path = public as $$
declare target_customer public.customers; target_agent uuid;
begin
  select * into target_customer from public.customers where customer_id = trim(p_customer_id) order by created_at limit 1 for update;
  select id into target_agent from public.agents where name = trim(p_agent_name) and lower(role) = 'control agent' limit 1 for update;
  if target_customer.campaign_id is null or target_agent is null then return false; end if;
  update public.customers set
    worked = true, outcome = coalesce(p_outcome, ''), status = coalesce(p_status, ''), business_status = coalesce(p_business_status, ''),
    ptp_amount = coalesce(p_amount_rec, 0), ptp_time = coalesce(p_ptp_time, ''),
    last_contact_at = now(), attempts = attempts + 1, follow_up_at = p_follow_up_at,
    locked_by = null, locked_at = null, lock_expires_at = null, skip_reason = '', skipped_at = null
  where campaign_id = target_customer.campaign_id and customer_id = target_customer.customer_id;
  insert into public.dispositions (campaign_id, customer_id, agent_id, outcome, status, amount_rec, comments, business_status, ptp_time, follow_up_at) values (target_customer.campaign_id, target_customer.customer_id, target_agent, coalesce(p_outcome, ''), coalesce(p_status, ''), coalesce(p_amount_rec, 0), coalesce(p_comments, ''), coalesce(p_business_status, ''), coalesce(p_ptp_time, ''), p_follow_up_at);
  insert into public.control_agent_performance (agent_id, calls_made, connected, conversion)
  values (target_agent, 1, case when p_outcome = 'Answered' then 1 else 0 end, coalesce(p_amount_rec, 0))
  on conflict (agent_id) do update set
    calls_made = public.control_agent_performance.calls_made + 1,
    connected = public.control_agent_performance.connected + case when p_outcome = 'Answered' then 1 else 0 end,
    conversion = public.control_agent_performance.conversion + coalesce(p_amount_rec, 0),
    updated_at = now();
  insert into public.queue_audit_log (agent_id, campaign_id, customer_id, action)
  values (target_agent, target_customer.campaign_id, target_customer.customer_id, 'CUSTOMER_COMPLETED');
  return true;
end;
$$;

revoke all on public.customer_tag_definitions from anon, authenticated;
revoke all on public.customer_tags from anon, authenticated;
revoke all on public.customer_notes from anon, authenticated;
grant all on public.customer_tag_definitions to service_role;
grant all on public.customer_tags to service_role;
grant all on public.customer_notes to service_role;
