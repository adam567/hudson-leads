-- Row-Level Security: org-scoped on every user-facing table.

create or replace function is_org_member(target_org uuid) returns boolean
language sql stable security definer as $$
  select exists (
    select 1 from org_members m
    where m.org_id = target_org and m.user_id = auth.uid()
  );
$$;

alter table orgs enable row level security;
alter table profiles enable row level security;
alter table org_members enable row level security;
alter table parcels enable row level security;
alter table households enable row level security;
alter table household_owners enable row level security;
alter table senior_batches enable row level security;
alter table confirmed_seniors enable row level security;
alter table scores enable row level security;

-- profiles: owner-only
drop policy if exists profile_self on profiles;
create policy profile_self on profiles
  for all using (user_id = auth.uid()) with check (user_id = auth.uid());

-- orgs: members can read their org
drop policy if exists org_read on orgs;
create policy org_read on orgs for select using (is_org_member(id));

-- org_members: read your own membership rows
drop policy if exists members_read on org_members;
create policy members_read on org_members for select using (user_id = auth.uid() or is_org_member(org_id));

-- parcels / households / scores / batches / seniors: org-scoped CRUD
drop policy if exists parcels_org on parcels;
create policy parcels_org on parcels for all using (is_org_member(org_id)) with check (is_org_member(org_id));

drop policy if exists households_org on households;
create policy households_org on households for all using (is_org_member(org_id)) with check (is_org_member(org_id));

drop policy if exists owners_org on household_owners;
create policy owners_org on household_owners for all
  using (exists (select 1 from households h where h.id = household_id and is_org_member(h.org_id)))
  with check (exists (select 1 from households h where h.id = household_id and is_org_member(h.org_id)));

drop policy if exists batches_org on senior_batches;
create policy batches_org on senior_batches for all using (is_org_member(org_id)) with check (is_org_member(org_id));

drop policy if exists seniors_org on confirmed_seniors;
create policy seniors_org on confirmed_seniors for all using (is_org_member(org_id)) with check (is_org_member(org_id));

drop policy if exists scores_org on scores;
create policy scores_org on scores for all using (is_org_member(org_id)) with check (is_org_member(org_id));
