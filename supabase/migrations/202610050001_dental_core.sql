-- Dental production core schema.
-- Apply through Supabase migrations, then enable/extend RLS for authenticated doctor roles.

create extension if not exists pgcrypto;

create table if not exists public.dental_patients (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  phone text not null default '',
  age text not null default '',
  status text not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.dental_appointments (
  id uuid primary key default gen_random_uuid(),
  patient_id uuid not null references public.dental_patients(id) on delete cascade,
  starts_at timestamptz not null,
  treatment_type text not null,
  status text not null default 'scheduled',
  created_at timestamptz not null default now()
);

create table if not exists public.dental_chart_entries (
  id uuid primary key default gen_random_uuid(),
  patient_id uuid not null references public.dental_patients(id) on delete cascade,
  tooth_fdi text not null check (tooth_fdi ~ '^(1[1-8]|2[1-8]|3[1-8]|4[1-8])$'),
  status text not null,
  note text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.dental_ai_events (
  id uuid primary key default gen_random_uuid(),
  patient_id uuid references public.dental_patients(id) on delete set null,
  capability text not null,
  request_context jsonb not null default '{}'::jsonb,
  result jsonb not null default '{}'::jsonb,
  clinician_action text,
  created_at timestamptz not null default now()
);

create table if not exists public.dental_audit_events (
  id uuid primary key default gen_random_uuid(),
  actor_id uuid,
  patient_id uuid references public.dental_patients(id) on delete set null,
  action text not null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists dental_appointments_patient_idx on public.dental_appointments(patient_id);
create index if not exists dental_chart_patient_idx on public.dental_chart_entries(patient_id);
create index if not exists dental_ai_events_patient_idx on public.dental_ai_events(patient_id);
create index if not exists dental_audit_patient_idx on public.dental_audit_events(patient_id);

alter table public.dental_patients enable row level security;
alter table public.dental_appointments enable row level security;
alter table public.dental_chart_entries enable row level security;
alter table public.dental_ai_events enable row level security;
alter table public.dental_audit_events enable row level security;

-- Backend service-role access is deliberately separated from mobile-client access.
-- Authenticated client policies should be added once doctor/assistant roles are defined.
