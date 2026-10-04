-- Doctor authentication / role foundation.
create table if not exists public.dental_staff (
  user_id uuid primary key references auth.users(id) on delete cascade,
  role text not null check (role in ('doctor','admin','assistant','reception')),
  display_name text not null default '',
  active boolean not null default true,
  created_at timestamptz not null default now()
);

alter table public.dental_staff enable row level security;

create policy "staff can read own profile"
on public.dental_staff for select
to authenticated
using (auth.uid() = user_id);

-- Mobile clients must not write clinical tables directly.
-- Writes remain behind the authenticated Dental API using the service role.
