-- Pivot to property-prospecting only. No senior signal in scoring.
-- Keep the senior tables in the schema (no destructive drops), but rebuild
-- the score function and dashboard view to ignore them entirely.

-- Add fields used by the new owner-occupied filter and richer scoring.
alter table parcels add column if not exists last_sale_price numeric;

create or replace function recompute_scores(target_org uuid)
returns void language plpgsql as $$
declare
  v_p25 numeric;
  v_p50 numeric;
  v_p75 numeric;
begin
  select percentile_cont(0.25) within group (order by p.market_value),
         percentile_cont(0.50) within group (order by p.market_value),
         percentile_cont(0.75) within group (order by p.market_value)
    into v_p25, v_p50, v_p75
    from parcels p
   where p.org_id = target_org and p.market_value is not null;

  insert into scores (household_id, org_id, tenure_points, value_points,
                      confirmation_points, window_multiplier, total_score, tier, updated_at)
  select
    h.id,
    h.org_id,
    -- Tenure: real years_owned 0..40 capped, scaled to 0..50.
    -- Heaviest weight because tenure is the strongest "established homeowner" signal.
    case
      when p.years_owned is null then 0
      else least(p.years_owned, 40) * (50.0 / 40.0)
    end as tenure_points,
    -- Value (within-org quartile): 0..30 (raises confidence in equity).
    case
      when p.market_value is null then 0
      when p.market_value < v_p25 then 5
      when p.market_value < v_p50 then 12
      when p.market_value < v_p75 then 22
      else 30
    end as value_points,
    -- "Confirmation" slot retired: always 0. Kept for column compat.
    0 as confirmation_points,
    1 as window_multiplier,
    -- Total = tenure + value + size factor, clamped 0..100.
    least(100,
      (case when p.years_owned is null then 0
            else least(p.years_owned, 40) * (50.0 / 40.0) end)
      + (case when p.market_value is null then 0
              when p.market_value < v_p25 then 5
              when p.market_value < v_p50 then 12
              when p.market_value < v_p75 then 22
              else 30 end)
      + least(coalesce(p.sqft, 0) / 200.0, 20)  -- 0..20 from sqft, ~4000sqft → 20
    ) as total_score,
    case
      when (case when p.years_owned is null then 0
                 else least(p.years_owned, 40) * (50.0 / 40.0) end)
         + (case when p.market_value is null then 0
                 when p.market_value < v_p25 then 5
                 when p.market_value < v_p50 then 12
                 when p.market_value < v_p75 then 22
                 else 30 end)
         + least(coalesce(p.sqft, 0) / 200.0, 20)
        >= 70 then 'A'
      when (case when p.years_owned is null then 0
                 else least(p.years_owned, 40) * (50.0 / 40.0) end)
         + (case when p.market_value is null then 0
                 when p.market_value < v_p25 then 5
                 when p.market_value < v_p50 then 12
                 when p.market_value < v_p75 then 22
                 else 30 end)
         + least(coalesce(p.sqft, 0) / 200.0, 20)
        >= 45 then 'B'
      else 'C'
    end as tier,
    now()
  from households h
  join parcels p on p.id = h.parcel_id
  where h.org_id = target_org
  on conflict (household_id) do update set
    tenure_points       = excluded.tenure_points,
    value_points        = excluded.value_points,
    confirmation_points = 0,
    window_multiplier   = 1,
    total_score         = excluded.total_score,
    tier                = excluded.tier,
    updated_at          = now();
end;
$$;

-- Rebuild the dashboard view: no senior fields exposed.
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
  p.mailing_address,
  p.mailing_same_as_situs,
  p.sqft,
  p.market_value,
  p.last_sale_date,
  p.last_sale_price,
  p.years_owned,
  p.lat,
  p.lng,
  p.year_built,
  p.property_class,
  s.total_score,
  s.tier,
  s.tenure_points,
  s.value_points
from households h
join parcels p on p.id = h.parcel_id
left join scores s on s.household_id = h.id;

grant select on v_dashboard to authenticated;
