-- "Ship next" additions: geo, last_touched, audit on status changes.

alter table parcels add column if not exists lat double precision;
alter table parcels add column if not exists lng double precision;
alter table parcels add column if not exists year_built int;
alter table parcels add column if not exists property_class text;

alter table households add column if not exists last_touched_at timestamptz;

create or replace function bump_last_touched() returns trigger as $$
begin
  if (tg_op = 'UPDATE')
     and (new.status is distinct from old.status or new.notes is distinct from old.notes) then
    new.last_touched_at = now();
  end if;
  return new;
end;
$$ language plpgsql;

drop trigger if exists trg_households_touched on households;
create trigger trg_households_touched before update on households
  for each row execute function bump_last_touched();

-- Refresh the dashboard view to include lat/lng + last_touched.
drop view if exists v_dashboard;
create or replace view v_dashboard as
select
  h.id as household_id,
  h.org_id,
  h.display_name,
  h.surname_key,
  h.owner_names,
  h.status,
  h.notes,
  h.last_touched_at,
  p.situs_address,
  p.situs_city,
  p.situs_zip,
  p.sqft,
  p.market_value,
  p.last_sale_date,
  p.years_owned,
  p.lat,
  p.lng,
  p.year_built,
  p.property_class,
  s.total_score,
  s.tier,
  s.tenure_points,
  s.value_points,
  s.confirmation_points,
  s.window_multiplier,
  exists (
    select 1 from confirmed_seniors cs
    where cs.matched_household_id = h.id
      and cs.match_status in ('matched','manual')
  ) as senior_confirmed,
  (
    select cs.school from confirmed_seniors cs
    where cs.matched_household_id = h.id
      and cs.match_status in ('matched','manual')
    limit 1
  ) as senior_school,
  current_window_label() as current_window
from households h
join parcels p on p.id = h.parcel_id
left join scores s on s.household_id = h.id;

grant select on v_dashboard to authenticated;
