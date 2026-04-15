create extension if not exists pgcrypto;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = timezone('utc', now());
  return new;
end;
$$;

create table if not exists public.projects (
  id text primary key,
  slug text unique,
  name text not null,
  manifest_project_id text,
  owner_user_id uuid references auth.users(id) on delete set null,
  status text not null default 'draft',
  storage_prefix text not null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.project_memberships (
  id uuid primary key default gen_random_uuid(),
  project_id text not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role text not null default 'viewer',
  permissions jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique(project_id, user_id)
);

create table if not exists public.project_assets (
  id uuid primary key default gen_random_uuid(),
  project_id text not null references public.projects(id) on delete cascade,
  logical_path text not null,
  storage_bucket text not null,
  storage_object_path text not null,
  asset_kind text not null,
  file_name text not null,
  content_type text,
  byte_size bigint not null default 0,
  checksum_sha256 text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique(project_id, logical_path)
);

create table if not exists public.project_exports (
  id uuid primary key default gen_random_uuid(),
  project_id text not null references public.projects(id) on delete cascade,
  logical_path text not null,
  asset_id uuid references public.project_assets(id) on delete set null,
  storage_bucket text not null,
  storage_object_path text not null,
  status text not null default 'available',
  file_name text not null,
  export_format text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique(project_id, logical_path)
);

create table if not exists public.pipeline_jobs (
  id uuid primary key default gen_random_uuid(),
  project_id text not null references public.projects(id) on delete cascade,
  job_key text not null,
  status text not null default 'queued',
  target_phase integer,
  active_phase integer,
  progress integer not null default 0,
  message text not null default '',
  payload jsonb not null default '{}'::jsonb,
  result jsonb not null default '{}'::jsonb,
  claimed_by text,
  worker_name text,
  cancel_requested boolean not null default false,
  created_at timestamptz not null default timezone('utc', now()),
  started_at timestamptz,
  completed_at timestamptz,
  updated_at timestamptz not null default timezone('utc', now()),
  unique(project_id, job_key)
);

create table if not exists public.project_graph_snapshots (
  id uuid primary key default gen_random_uuid(),
  project_id text not null references public.projects(id) on delete cascade,
  snapshot_kind text not null default 'current',
  graph jsonb not null default '{}'::jsonb,
  graph_checksum text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique(project_id, snapshot_kind)
);

create table if not exists public.project_graph_ops (
  id uuid primary key default gen_random_uuid(),
  project_id text not null references public.projects(id) on delete cascade,
  operation text not null,
  node_type text not null,
  node_id text not null,
  actor text not null default 'system',
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now())
);

create index if not exists idx_project_assets_project_kind on public.project_assets(project_id, asset_kind);
create index if not exists idx_project_exports_project_status on public.project_exports(project_id, status);
create index if not exists idx_pipeline_jobs_project_status on public.pipeline_jobs(project_id, status);
create index if not exists idx_graph_ops_project_created on public.project_graph_ops(project_id, created_at desc);

create or replace function public.claim_pipeline_job(target_worker text)
returns setof public.pipeline_jobs
language plpgsql
security definer
as $$
declare
  claimed_id uuid;
begin
  with next_job as (
    select id
    from public.pipeline_jobs
    where cancel_requested = false
      and (
        status = 'queued'
        or (
          status = 'running'
          and updated_at < timezone('utc', now()) - interval '5 minutes'
        )
      )
    order by created_at asc
    for update skip locked
    limit 1
  )
  update public.pipeline_jobs job
  set status = 'running',
      claimed_by = target_worker,
      worker_name = target_worker,
      started_at = coalesce(job.started_at, timezone('utc', now())),
      updated_at = timezone('utc', now())
  from next_job
  where job.id = next_job.id
  returning job.id into claimed_id;

  if claimed_id is null then
    return;
  end if;

  return query
  select *
  from public.pipeline_jobs
  where id = claimed_id;
end;
$$;

drop trigger if exists set_projects_updated_at on public.projects;
create trigger set_projects_updated_at
before update on public.projects
for each row execute function public.set_updated_at();

drop trigger if exists set_project_memberships_updated_at on public.project_memberships;
create trigger set_project_memberships_updated_at
before update on public.project_memberships
for each row execute function public.set_updated_at();

drop trigger if exists set_project_assets_updated_at on public.project_assets;
create trigger set_project_assets_updated_at
before update on public.project_assets
for each row execute function public.set_updated_at();

drop trigger if exists set_project_exports_updated_at on public.project_exports;
create trigger set_project_exports_updated_at
before update on public.project_exports
for each row execute function public.set_updated_at();

drop trigger if exists set_pipeline_jobs_updated_at on public.pipeline_jobs;
create trigger set_pipeline_jobs_updated_at
before update on public.pipeline_jobs
for each row execute function public.set_updated_at();

drop trigger if exists set_project_graph_snapshots_updated_at on public.project_graph_snapshots;
create trigger set_project_graph_snapshots_updated_at
before update on public.project_graph_snapshots
for each row execute function public.set_updated_at();

alter table public.projects enable row level security;
alter table public.project_memberships enable row level security;
alter table public.project_assets enable row level security;
alter table public.project_exports enable row level security;
alter table public.pipeline_jobs enable row level security;
alter table public.project_graph_snapshots enable row level security;
alter table public.project_graph_ops enable row level security;

create or replace function public.is_project_member(target_project_id text, target_user_id uuid)
returns boolean
language sql
stable
as $$
  select exists (
    select 1
    from public.project_memberships membership
    where membership.project_id = target_project_id
      and membership.user_id = target_user_id
  )
  or exists (
    select 1
    from public.projects project
    where project.id = target_project_id
      and project.owner_user_id = target_user_id
  );
$$;

drop policy if exists "project members can read projects" on public.projects;
create policy "project members can read projects"
on public.projects
for select
to authenticated
using (public.is_project_member(id, auth.uid()));

drop policy if exists "project members can read memberships" on public.project_memberships;
create policy "project members can read memberships"
on public.project_memberships
for select
to authenticated
using (public.is_project_member(project_id, auth.uid()));

drop policy if exists "project members can read assets" on public.project_assets;
create policy "project members can read assets"
on public.project_assets
for select
to authenticated
using (public.is_project_member(project_id, auth.uid()));

drop policy if exists "project members can read exports" on public.project_exports;
create policy "project members can read exports"
on public.project_exports
for select
to authenticated
using (public.is_project_member(project_id, auth.uid()));

drop policy if exists "project members can read jobs" on public.pipeline_jobs;
create policy "project members can read jobs"
on public.pipeline_jobs
for select
to authenticated
using (public.is_project_member(project_id, auth.uid()));

drop policy if exists "project members can read graph snapshots" on public.project_graph_snapshots;
create policy "project members can read graph snapshots"
on public.project_graph_snapshots
for select
to authenticated
using (public.is_project_member(project_id, auth.uid()));

drop policy if exists "project members can read graph ops" on public.project_graph_ops;
create policy "project members can read graph ops"
on public.project_graph_ops
for select
to authenticated
using (public.is_project_member(project_id, auth.uid()));

insert into storage.buckets (id, name, public)
values ('project-assets', 'project-assets', false)
on conflict (id) do update set public = excluded.public;

insert into storage.buckets (id, name, public)
values ('project-exports', 'project-exports', false)
on conflict (id) do update set public = excluded.public;

drop policy if exists "project members can read stored project assets" on storage.objects;
create policy "project members can read stored project assets"
on storage.objects
for select
to authenticated
using (
  bucket_id in ('project-assets', 'project-exports')
  and public.is_project_member((storage.foldername(name))[1], auth.uid())
);
