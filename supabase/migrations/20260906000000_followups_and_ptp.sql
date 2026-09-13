-- Follow-Up and Promise-to-Pay management: normalized tables, workflow RPCs (with optimistic
-- locking + audit logging reusing queue_audit_log), and Next-Customer/config integration.

create table if not exists public.follow_ups (
  id uuid primary key default gen_random_uuid(),
  campaign_id uuid not null,
  customer_id text not null,
  agent_id uuid references public.agents(id) on delete set null,
  created_by uuid references public.agents(id) on delete set null,
  type text not null default 'CALLBACK' check (type in ('CALLBACK', 'PTP', 'PAYMENT_CHECK', 'DOCUMENT_REQUEST', 'SUPERVISOR_FOLLOWUP', 'OTHER')),
  reason text not null default '',
  scheduled_at timestamptz not null,
  status text not null default 'PENDING' check (status in ('PENDING', 'DUE', 'OVERDUE', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED', 'RESCHEDULED')),
  priority text not null default 'medium',
  notes text not null default '',
  completed_at timestamptz,
  completed_by uuid references public.agents(id) on delete set null,
  completed_outcome text not null default '',
  previous_agent uuid references public.agents(id) on delete set null,
  reassigned_by uuid references public.agents(id) on delete set null,
  reassigned_at timestamptz,
  reassignment_reason text not null default '',
  rescheduled_from uuid references public.follow_ups(id) on delete set null,
  ptp_id uuid,
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (campaign_id, customer_id) references public.customers(campaign_id, customer_id) on delete cascade
);
create index if not exists follow_ups_lookup_idx on public.follow_ups (campaign_id, customer_id);
create index if not exists follow_ups_agent_status_idx on public.follow_ups (agent_id, status, scheduled_at);
create index if not exists follow_ups_status_scheduled_idx on public.follow_ups (status, scheduled_at);

drop trigger if exists follow_ups_updated_at on public.follow_ups;
create trigger follow_ups_updated_at before update on public.follow_ups for each row execute function public.set_updated_at();

create table if not exists public.promise_to_pay (
  id uuid primary key default gen_random_uuid(),
  campaign_id uuid not null,
  customer_id text not null,
  agent_id uuid references public.agents(id) on delete set null,
  disposition_id uuid references public.dispositions(id) on delete set null,
  follow_up_id uuid references public.follow_ups(id) on delete set null,
  promised_amount numeric(14, 2) not null check (promised_amount >= 0),
  promised_date date not null,
  payment_method text not null default '',
  status text not null default 'PENDING' check (status in ('PENDING', 'PARTIALLY_PAID', 'FULFILLED', 'BROKEN', 'CANCELLED', 'EXPIRED')),
  paid_amount numeric(14, 2) not null default 0 check (paid_amount >= 0),
  remaining_amount numeric(14, 2) generated always as (greatest(promised_amount - paid_amount, 0)) stored,
  notes text not null default '',
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (campaign_id, customer_id) references public.customers(campaign_id, customer_id) on delete cascade
);
create index if not exists promise_to_pay_lookup_idx on public.promise_to_pay (campaign_id, customer_id);
create index if not exists promise_to_pay_agent_status_idx on public.promise_to_pay (agent_id, status);
create index if not exists promise_to_pay_status_date_idx on public.promise_to_pay (status, promised_date);

drop trigger if exists promise_to_pay_updated_at on public.promise_to_pay;
create trigger promise_to_pay_updated_at before update on public.promise_to_pay for each row execute function public.set_updated_at();

alter table public.follow_ups add constraint follow_ups_ptp_id_fkey foreign key (ptp_id) references public.promise_to_pay(id) on delete set null;
create index if not exists follow_ups_ptp_idx on public.follow_ups (ptp_id);

-- Extend the existing single-row scoring config with Follow-Up/PTP knobs so they can be tuned
-- without a deploy, same rationale as the original queue_priority_weights columns.
alter table public.queue_priority_weights
  add column if not exists overdue_followup_weight numeric(5, 2) not null default 0.10,
  add column if not exists ptp_overdue_weight numeric(5, 2) not null default 0.10,
  add column if not exists ptp_followup_offset_minutes integer not null default 0,
  add column if not exists allow_ptp_exceeding_balance boolean not null default true,
  add column if not exists ptp_expiry_grace_days integer not null default 7,
  add column if not exists auto_confirm_ptp_outcomes boolean not null default true;

-- record_disposition now returns the new disposition id (instead of a bare boolean) so the API
-- layer can link a follow-on Promise-to-Pay record to the call that produced it.
drop function if exists public.record_disposition(text, text, text, numeric, text, text, text, text, timestamptz);
create or replace function public.record_disposition(p_customer_id text, p_outcome text, p_status text, p_amount_rec numeric, p_agent_name text, p_comments text, p_business_status text, p_ptp_time text, p_follow_up_at timestamptz default null)
returns uuid language plpgsql security definer set search_path = public as $$
declare target_customer public.customers; target_agent uuid; new_disposition_id uuid;
begin
  select * into target_customer from public.customers where customer_id = trim(p_customer_id) order by created_at limit 1 for update;
  select id into target_agent from public.agents where name = trim(p_agent_name) and lower(role) = 'control agent' limit 1 for update;
  if target_customer.campaign_id is null or target_agent is null then return null; end if;
  update public.customers set
    worked = true, outcome = coalesce(p_outcome, ''), status = coalesce(p_status, ''), business_status = coalesce(p_business_status, ''),
    ptp_amount = coalesce(p_amount_rec, 0), ptp_time = coalesce(p_ptp_time, ''),
    last_contact_at = now(), attempts = attempts + 1, follow_up_at = p_follow_up_at,
    locked_by = null, locked_at = null, lock_expires_at = null, skip_reason = '', skipped_at = null
  where campaign_id = target_customer.campaign_id and customer_id = target_customer.customer_id;
  insert into public.dispositions (campaign_id, customer_id, agent_id, outcome, status, amount_rec, comments, business_status, ptp_time, follow_up_at)
  values (target_customer.campaign_id, target_customer.customer_id, target_agent, coalesce(p_outcome, ''), coalesce(p_status, ''), coalesce(p_amount_rec, 0), coalesce(p_comments, ''), coalesce(p_business_status, ''), coalesce(p_ptp_time, ''), p_follow_up_at)
  returning id into new_disposition_id;
  insert into public.control_agent_performance (agent_id, calls_made, connected, conversion)
  values (target_agent, 1, case when p_outcome = 'Answered' then 1 else 0 end, coalesce(p_amount_rec, 0))
  on conflict (agent_id) do update set
    calls_made = public.control_agent_performance.calls_made + 1,
    connected = public.control_agent_performance.connected + case when p_outcome = 'Answered' then 1 else 0 end,
    conversion = public.control_agent_performance.conversion + coalesce(p_amount_rec, 0),
    updated_at = now();
  insert into public.queue_audit_log (agent_id, campaign_id, customer_id, action)
  values (target_agent, target_customer.campaign_id, target_customer.customer_id, 'CUSTOMER_COMPLETED');
  return new_disposition_id;
end;
$$;

-- Creates a Promise-to-Pay record and, optionally, an auto-scheduled PTP follow-up in one
-- transaction so the two are never left inconsistent with each other.
create or replace function public.create_ptp(
  p_agent_id uuid, p_campaign_id uuid, p_customer_id text, p_disposition_id uuid,
  p_promised_amount numeric, p_promised_date date, p_payment_method text, p_notes text,
  p_create_followup boolean default true, p_followup_offset_minutes integer default 0
) returns table(ptp_id uuid, follow_up_id uuid) language plpgsql security definer set search_path = public as $$
declare new_ptp_id uuid; new_followup_id uuid;
begin
  if p_promised_amount is null or p_promised_amount < 0 then
    raise exception 'Promised amount must be zero or greater';
  end if;
  if p_promised_date is null then
    raise exception 'Promised date is required';
  end if;
  insert into public.promise_to_pay (campaign_id, customer_id, agent_id, disposition_id, promised_amount, promised_date, payment_method, notes)
  values (p_campaign_id, trim(p_customer_id), p_agent_id, p_disposition_id, p_promised_amount, p_promised_date, coalesce(p_payment_method, ''), coalesce(p_notes, ''))
  returning id into new_ptp_id;

  insert into public.queue_audit_log (agent_id, campaign_id, customer_id, action, reason)
  values (p_agent_id, p_campaign_id, trim(p_customer_id), 'PTP_CREATED', 'amount:' || p_promised_amount::text);

  if p_create_followup then
    insert into public.follow_ups (campaign_id, customer_id, agent_id, created_by, type, reason, scheduled_at, status, notes, ptp_id)
    values (p_campaign_id, trim(p_customer_id), p_agent_id, p_agent_id, 'PTP', 'Promise to Pay follow-up',
            p_promised_date::timestamptz + make_interval(mins => coalesce(p_followup_offset_minutes, 0)), 'PENDING', coalesce(p_notes, ''), new_ptp_id)
    returning id into new_followup_id;
    update public.promise_to_pay set follow_up_id = new_followup_id where id = new_ptp_id;
    insert into public.queue_audit_log (agent_id, campaign_id, customer_id, action, reason)
    values (p_agent_id, p_campaign_id, trim(p_customer_id), 'FOLLOWUP_CREATED', 'type:PTP');
  end if;

  return query select new_ptp_id, new_followup_id;
end;
$$;

create or replace function public.create_callback_followup(p_agent_id uuid, p_campaign_id uuid, p_customer_id text, p_scheduled_at timestamptz, p_reason text, p_notes text)
returns uuid language plpgsql security definer set search_path = public as $$
declare new_id uuid;
begin
  if p_scheduled_at is null then
    raise exception 'Scheduled time is required';
  end if;
  insert into public.follow_ups (campaign_id, customer_id, agent_id, created_by, type, reason, scheduled_at, status, notes)
  values (p_campaign_id, trim(p_customer_id), p_agent_id, p_agent_id, 'CALLBACK', coalesce(p_reason, ''), p_scheduled_at, 'PENDING', coalesce(p_notes, ''))
  returning id into new_id;
  insert into public.queue_audit_log (agent_id, campaign_id, customer_id, action, reason)
  values (p_agent_id, p_campaign_id, trim(p_customer_id), 'FOLLOWUP_CREATED', 'type:CALLBACK');
  return new_id;
end;
$$;

create or replace function public.complete_follow_up(p_follow_up_id uuid, p_agent_id uuid, p_version integer, p_outcome text, p_notes text)
returns boolean language plpgsql security definer set search_path = public as $$
declare target public.follow_ups;
begin
  update public.follow_ups set
    status = 'COMPLETED', completed_at = now(), completed_by = p_agent_id, completed_outcome = coalesce(p_outcome, ''),
    notes = case when coalesce(p_notes, '') <> '' then p_notes else notes end, version = version + 1
  where id = p_follow_up_id and version = p_version and status not in ('COMPLETED', 'CANCELLED')
  returning * into target;
  if target.id is null then return false; end if;
  insert into public.queue_audit_log (agent_id, campaign_id, customer_id, action, reason)
  values (p_agent_id, target.campaign_id, target.customer_id, 'FOLLOWUP_COMPLETED', 'outcome:' || coalesce(p_outcome, ''));
  return true;
end;
$$;

-- Reschedule never rewrites the original row (audit trail intact): it closes the current
-- follow-up as RESCHEDULED and inserts a fresh PENDING one linked via rescheduled_from.
create or replace function public.reschedule_follow_up(p_follow_up_id uuid, p_agent_id uuid, p_version integer, p_new_scheduled_at timestamptz, p_reason text, p_notes text)
returns uuid language plpgsql security definer set search_path = public as $$
declare target public.follow_ups; new_id uuid;
begin
  if p_new_scheduled_at is null then
    raise exception 'New scheduled time is required';
  end if;
  update public.follow_ups set status = 'RESCHEDULED', version = version + 1
  where id = p_follow_up_id and version = p_version and status not in ('COMPLETED', 'CANCELLED')
  returning * into target;
  if target.id is null then return null; end if;

  insert into public.follow_ups (campaign_id, customer_id, agent_id, created_by, type, reason, scheduled_at, status, priority, notes, rescheduled_from)
  values (target.campaign_id, target.customer_id, target.agent_id, p_agent_id, target.type, coalesce(p_reason, target.reason), p_new_scheduled_at, 'PENDING', target.priority, coalesce(p_notes, ''), target.id)
  returning id into new_id;

  insert into public.queue_audit_log (agent_id, campaign_id, customer_id, action, reason)
  values (p_agent_id, target.campaign_id, target.customer_id, 'FOLLOWUP_RESCHEDULED', coalesce(p_reason, ''));
  return new_id;
end;
$$;

create or replace function public.cancel_follow_up(p_follow_up_id uuid, p_agent_id uuid, p_version integer, p_reason text)
returns boolean language plpgsql security definer set search_path = public as $$
declare target public.follow_ups;
begin
  update public.follow_ups set status = 'CANCELLED', version = version + 1
  where id = p_follow_up_id and version = p_version and status not in ('COMPLETED', 'CANCELLED')
  returning * into target;
  if target.id is null then return false; end if;
  insert into public.queue_audit_log (agent_id, campaign_id, customer_id, action, reason)
  values (p_agent_id, target.campaign_id, target.customer_id, 'FOLLOWUP_CANCELLED', coalesce(p_reason, ''));
  return true;
end;
$$;

create or replace function public.reassign_follow_up(p_follow_up_id uuid, p_changed_by uuid, p_version integer, p_new_agent_id uuid, p_reason text)
returns boolean language plpgsql security definer set search_path = public as $$
declare target public.follow_ups; old_agent uuid;
begin
  select agent_id into old_agent from public.follow_ups where id = p_follow_up_id;
  update public.follow_ups set
    previous_agent = agent_id, agent_id = p_new_agent_id, reassigned_by = p_changed_by, reassigned_at = now(),
    reassignment_reason = coalesce(p_reason, ''), version = version + 1
  where id = p_follow_up_id and version = p_version and status not in ('COMPLETED', 'CANCELLED')
  returning * into target;
  if target.id is null then return false; end if;
  insert into public.queue_audit_log (agent_id, campaign_id, customer_id, action, reason)
  values (p_changed_by, target.campaign_id, target.customer_id, 'FOLLOWUP_REASSIGNED', format('from:%s to:%s reason:%s', coalesce(old_agent::text, ''), coalesce(p_new_agent_id::text, ''), coalesce(p_reason, '')));
  return true;
end;
$$;

create or replace function public.update_ptp_status(p_ptp_id uuid, p_agent_id uuid, p_version integer, p_status text, p_paid_amount numeric, p_notes text)
returns boolean language plpgsql security definer set search_path = public as $$
declare target public.promise_to_pay; audit_action text;
begin
  if p_status not in ('PENDING', 'PARTIALLY_PAID', 'FULFILLED', 'BROKEN', 'CANCELLED') then
    raise exception 'Invalid PTP status: %', p_status;
  end if;
  update public.promise_to_pay set
    status = p_status, paid_amount = coalesce(p_paid_amount, paid_amount),
    notes = case when coalesce(p_notes, '') <> '' then p_notes else notes end, version = version + 1
  where id = p_ptp_id and version = p_version and status not in ('FULFILLED', 'CANCELLED', 'EXPIRED')
  returning * into target;
  if target.id is null then return false; end if;
  audit_action := case p_status when 'FULFILLED' then 'PTP_FULFILLED' when 'BROKEN' then 'PTP_BROKEN' else 'PTP_UPDATED' end;
  insert into public.queue_audit_log (agent_id, campaign_id, customer_id, action, reason)
  values (p_agent_id, target.campaign_id, target.customer_id, audit_action, 'status:' || p_status);
  return true;
end;
$$;

-- Safe server-side fallback for section 26 (no background workers yet): recomputes
-- Today/Overdue buckets for pending follow-ups. Call this before listing follow-ups; a real
-- scheduler (pg_cron/Celery/RQ) can call it on an interval once one is introduced.
create or replace function public.refresh_followup_states()
returns integer language plpgsql security definer set search_path = public as $$
declare updated_count integer := 0; temp_count integer;
begin
  update public.follow_ups set status = 'OVERDUE' where status in ('PENDING', 'DUE') and scheduled_at::date < current_date;
  get diagnostics temp_count = row_count; updated_count := updated_count + temp_count;
  update public.follow_ups set status = 'DUE' where status = 'PENDING' and scheduled_at::date = current_date;
  get diagnostics temp_count = row_count; updated_count := updated_count + temp_count;
  return updated_count;
end;
$$;

-- Same fallback pattern for PTPs left unresolved long after their promise date.
create or replace function public.expire_stale_ptps(p_grace_days integer default 7)
returns integer language plpgsql security definer set search_path = public as $$
declare rec record; expired_count integer := 0;
begin
  for rec in
    select id, agent_id, campaign_id, customer_id from public.promise_to_pay
    where status in ('PENDING', 'PARTIALLY_PAID') and promised_date < current_date - greatest(p_grace_days, 0)
    for update skip locked
  loop
    update public.promise_to_pay set status = 'EXPIRED', version = version + 1 where id = rec.id;
    insert into public.queue_audit_log (agent_id, campaign_id, customer_id, action) values (rec.agent_id, rec.campaign_id, rec.customer_id, 'PTP_UPDATED');
    expired_count := expired_count + 1;
  end loop;
  return expired_count;
end;
$$;

revoke all on public.follow_ups from anon, authenticated;
revoke all on public.promise_to_pay from anon, authenticated;
grant all on public.follow_ups to service_role;
grant all on public.promise_to_pay to service_role;
grant execute on function public.record_disposition(text, text, text, numeric, text, text, text, text, timestamptz) to service_role;
grant execute on function public.create_ptp(uuid, uuid, text, uuid, numeric, date, text, text, boolean, integer) to service_role;
grant execute on function public.create_callback_followup(uuid, uuid, text, timestamptz, text, text) to service_role;
grant execute on function public.complete_follow_up(uuid, uuid, integer, text, text) to service_role;
grant execute on function public.reschedule_follow_up(uuid, uuid, integer, timestamptz, text, text) to service_role;
grant execute on function public.cancel_follow_up(uuid, uuid, integer, text) to service_role;
grant execute on function public.reassign_follow_up(uuid, uuid, integer, uuid, text) to service_role;
grant execute on function public.update_ptp_status(uuid, uuid, integer, text, numeric, text) to service_role;
grant execute on function public.refresh_followup_states() to service_role;
grant execute on function public.expire_stale_ptps(integer) to service_role;
