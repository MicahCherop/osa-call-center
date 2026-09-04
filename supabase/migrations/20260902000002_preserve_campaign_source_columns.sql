alter table public.customers
  add column if not exists source_data jsonb not null default '{}'::jsonb;