-- Intelligent "Next Customer" queue: locking, follow-up tracking, attempts, audit log, configurable weights.

alter table public.customers
  add column if not exists locked_by uuid references public.agents(id) on delete set null,
  add column if not exists locked_at timestamptz,
  add column if not exists lock_expires_at timestamptz,
  add column if not exists attempts integer not null default 0,
  add column if not exists last_contact_at timestamptz,
  add column if not exists follow_up_at timestamptz,
  add column if not exists skip_reason text not null default '',
  add column if not exists skipped_at timestamptz;

-- Supports "my eligible queue" lookups (assigned_agent_id, worked) plus lock/follow-up filtering.
create index if not exists customers_next_queue_idx on public.customers (assigned_agent_id, worked, lock_expires_at);
create index if not exists customers_followup_idx on public.customers (follow_up_at) where follow_up_at is not null;
create index if not exists customers_locked_by_idx on public.customers (locked_by) where locked_by is not null;

create table if not exists public.queue_audit_log (
  id uuid primary key default gen_random_uuid(),
  agent_id uuid references public.agents(id) on delete set null,
  campaign_id uuid,
  customer_id text,
  action text not null,
  reason text not null default '',
  created_at timestamptz not null default now()
);
create index if not exists queue_audit_log_agent_idx on public.queue_audit_log (agent_id, created_at desc);
create index if not exists queue_audit_log_customer_idx on public.queue_audit_log (campaign_id, customer_id, created_at desc);

-- Single-row configurable scoring weights for the Next Customer priority engine. Values are
-- fractional weights (roughly summing to 1.0 across the positive factors) applied to 0-100
-- normalized component scores; see api/priority.py for the full formula.
create table if not exists public.queue_priority_weights (
  id smallint primary key default 1 check (id = 1),
  campaign_priority_weight numeric(5, 2) not null default 0.20,
  overdue_days_weight numeric(5, 2) not null default 0.20,
  ptp_weight numeric(5, 2) not null default 0.25,
  followup_weight numeric(5, 2) not null default 0.15,
  balance_weight numeric(5, 2) not null default 0.10,
  contactability_weight numeric(5, 2) not null default 0.05,
  recent_contact_penalty numeric(5, 2) not null default 0.05,
  attempt_penalty numeric(5, 2) not null default 0.10,
  recent_contact_hours integer not null default 4,
  max_attempts integer not null default 6,
  lock_minutes integer not null default 12,
  updated_at timestamptz not null default now()
);
insert into public.queue_priority_weights (id) values (1) on conflict (id) do nothing;

create or replace function public.set_updated_at_weights() returns trigger language plpgsql as $$
begin new.updated_at = now(); return new; end;
$$;
drop trigger if exists queue_priority_weights_updated_at on public.queue_priority_weights;
create trigger queue_priority_weights_updated_at before update on public.queue_priority_weights for each row execute function public.set_updated_at_weights();

-- Releases any locks whose lock_expires_at has passed and records a CUSTOMER_LOCK_EXPIRED audit
-- entry for each one. Must run before candidate selection so stale locks don't block reassignment.
create or replace function public.release_expired_locks()
returns integer language plpgsql security definer set search_path = public as $$
declare rec record; released integer := 0;
begin
  for rec in
    select campaign_id, customer_id, locked_by from public.customers
    where lock_expires_at is not null and lock_expires_at < now()
    for update skip locked
  loop
    update public.customers set locked_by = null, locked_at = null, lock_expires_at = null
    where campaign_id = rec.campaign_id and customer_id = rec.customer_id;
    insert into public.queue_audit_log (agent_id, campaign_id, customer_id, action)
    values (rec.locked_by, rec.campaign_id, rec.customer_id, 'CUSTOMER_LOCK_EXPIRED');
    released := released + 1;
  end loop;
  return released;
end;
$$;

-- Atomically reserves a single customer for an agent. Fails (returns false) if the customer is
-- not assigned to that agent, already worked, or currently locked by an active (non-expired) lock.
create or replace function public.reserve_customer_lock(p_agent_id uuid, p_campaign_id uuid, p_customer_id text, p_lock_minutes integer default 12)
returns boolean language plpgsql security definer set search_path = public as $$
declare updated_count integer;
begin
  update public.customers
  set locked_by = p_agent_id, locked_at = now(), lock_expires_at = now() + make_interval(mins => greatest(p_lock_minutes, 1))
  where campaign_id = p_campaign_id and customer_id = p_customer_id
    and assigned_agent_id = p_agent_id and worked = false
    and (locked_by is null or lock_expires_at < now());
  get diagnostics updated_count = row_count;
  if updated_count = 0 then return false; end if;
  insert into public.queue_audit_log (agent_id, campaign_id, customer_id, action)
  values (p_agent_id, p_campaign_id, p_customer_id, 'CUSTOMER_RESERVED');
  return true;
end;
$$;

-- Releases a reserved customer back to the queue with an optional skip reason. Does not change
-- worked/outcome so the customer remains eligible (lower priority via the recency/attempt penalty).
create or replace function public.skip_customer(p_agent_id uuid, p_campaign_id uuid, p_customer_id text, p_reason text default '')
returns boolean language plpgsql security definer set search_path = public as $$
declare updated_count integer;
begin
  update public.customers
  set locked_by = null, locked_at = null, lock_expires_at = null,
      skip_reason = coalesce(p_reason, ''), skipped_at = now()
  where campaign_id = p_campaign_id and customer_id = p_customer_id and assigned_agent_id = p_agent_id;
  get diagnostics updated_count = row_count;
  if updated_count = 0 then return false; end if;
  insert into public.queue_audit_log (agent_id, campaign_id, customer_id, action, reason)
  values (p_agent_id, p_campaign_id, p_customer_id, 'CUSTOMER_SKIPPED', coalesce(p_reason, ''));
  return true;
end;
$$;

-- Cheap, database-side queue counts for the "MY QUEUE" summary panel (avoids pulling every
-- customer row into the browser just to count them).
create or replace function public.get_queue_summary(p_agent_id uuid)
returns table(high_priority integer, follow_ups integer, ptp_customers integer, new_customers integer, total integer)
language sql stable security definer set search_path = public as $$
  select
    count(*) filter (where coalesce(balance, 0) >= 50000 or (due_date is not null and due_date < current_date))::integer as high_priority,
    count(*) filter (where follow_up_at is not null and follow_up_at <= now())::integer as follow_ups,
    count(*) filter (where status ilike 'promise to pay%')::integer as ptp_customers,
    count(*) filter (where attempts = 0)::integer as new_customers,
    count(*)::integer as total
  from public.customers
  where assigned_agent_id = p_agent_id and worked = false;
$$;

-- record_disposition now stamps last_contact_at/attempts, releases the queue lock, optionally
-- stores a follow-up timestamp, and logs a CUSTOMER_COMPLETED audit entry.
-- Drop the previous 8-argument signature so PostgREST doesn't see two overloads with the same name.
drop function if exists public.record_disposition(text, text, text, numeric, text, text, text, text);

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
  insert into public.dispositions (campaign_id, customer_id, agent_id, outcome, status, amount_rec, comments, business_status, ptp_time) values (target_customer.campaign_id, target_customer.customer_id, target_agent, coalesce(p_outcome, ''), coalesce(p_status, ''), coalesce(p_amount_rec, 0), coalesce(p_comments, ''), coalesce(p_business_status, ''), coalesce(p_ptp_time, ''));
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

revoke all on public.queue_audit_log from anon, authenticated;
revoke all on public.queue_priority_weights from anon, authenticated;
revoke execute on function public.release_expired_locks() from public;
revoke execute on function public.reserve_customer_lock(uuid, uuid, text, integer) from public;
revoke execute on function public.skip_customer(uuid, uuid, text, text) from public;
revoke execute on function public.get_queue_summary(uuid) from public;
revoke execute on function public.record_disposition(text, text, text, numeric, text, text, text, text, timestamptz) from public;

grant all on public.queue_audit_log to service_role;
grant all on public.queue_priority_weights to service_role;
grant execute on function public.release_expired_locks() to service_role;
grant execute on function public.reserve_customer_lock(uuid, uuid, text, integer) to service_role;
grant execute on function public.skip_customer(uuid, uuid, text, text) to service_role;
grant execute on function public.get_queue_summary(uuid) to service_role;
grant execute on function public.record_disposition(text, text, text, numeric, text, text, text, text, timestamptz) to service_role;
