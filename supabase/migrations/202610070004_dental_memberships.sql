-- SaaS memberships and payment orders.
create table if not exists public.dental_memberships (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references public.dental_clinics(id) on delete cascade,
  plan_id text not null,
  status text not null check (status in ('trial','active','past_due','cancelled','expired')),
  started_at timestamptz not null default now(),
  expires_at timestamptz,
  created_at timestamptz not null default now()
);
create index if not exists idx_dental_memberships_clinic on public.dental_memberships(clinic_id);

create table if not exists public.dental_membership_orders (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references public.dental_clinics(id) on delete cascade,
  plan_id text not null,
  razorpay_order_id text not null unique,
  razorpay_payment_id text,
  amount_inr integer not null,
  status text not null default 'created',
  created_at timestamptz not null default now(),
  paid_at timestamptz
);
create index if not exists idx_dental_membership_orders_clinic on public.dental_membership_orders(clinic_id);

alter table public.dental_memberships enable row level security;
alter table public.dental_membership_orders enable row level security;
