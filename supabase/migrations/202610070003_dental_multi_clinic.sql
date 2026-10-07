-- Multi-clinic SaaS foundation for the Dental Platform.
-- A clinic is an isolated tenant. Users can belong to one or more clinics
-- with explicit roles. Clinical records must be scoped to clinic_id in the
-- Dental API/database before production multi-clinic launch.

create table if not exists public.dental_clinics (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  slug text not null unique,
  logo_url text,
  phone text,
  email text,
  address text,
  active boolean not null default true,
  created_at timestamptz not null default now()
);

create table if not exists public.dental_clinic_memberships (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references public.dental_clinics(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role text not null check (role in ('owner','admin','doctor','assistant','reception')),
  active boolean not null default true,
  created_at timestamptz not null default now(),
  unique (clinic_id, user_id)
);

create index if not exists idx_dental_memberships_user
  on public.dental_clinic_memberships(user_id);
create index if not exists idx_dental_memberships_clinic
  on public.dental_clinic_memberships(clinic_id);

alter table public.dental_clinics enable row level security;
alter table public.dental_clinic_memberships enable row level security;

drop policy if exists "members can read their clinics" on public.dental_clinics;
create policy "members can read their clinics"
on public.dental_clinics for select
to authenticated
using (
  exists (
    select 1 from public.dental_clinic_memberships m
    where m.clinic_id = dental_clinics.id
      and m.user_id = auth.uid()
      and m.active = true
  )
);

drop policy if exists "members can read clinic memberships" on public.dental_clinic_memberships;
create policy "members can read clinic memberships"
on public.dental_clinic_memberships for select
to authenticated
using (user_id = auth.uid());

-- Keep the existing dental_staff table for backwards compatibility.
-- New production authorization should use clinic memberships.
