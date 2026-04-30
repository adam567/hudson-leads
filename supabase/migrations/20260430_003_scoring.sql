-- Scoring + windowing.

-- Returns a multiplier reflecting where today falls in the senior-parent
-- engagement calendar. Tunable per the consultation:
--   Late-Jul → Sep (silent-house window)  → 1.6
--   Feb-Mar (FAFSA / planning window)     → 1.2
--   Mid-Nov → early-Jan (reactivation)    → 1.1
--   Mid-May → late-Jun (avoid)            → 0.7
--   everything else                       → 1.0
create or replace function current_window_multiplier(d date default current_date)
returns numeric language sql immutable as $$
  select case
    when (extract(month from d) = 7  and extract(day from d) >= 25)
      or  extract(month from d) = 8
      or  extract(month from d) = 9                              then 1.6
    when extract(month from d) in (2, 3)                          then 1.2
    when (extract(month from d) = 11 and extract(day from d) >= 15)
      or  extract(month from d) = 12
      or (extract(month from d) = 1  and extract(day from d) <= 7) then 1.1
    when (extract(month from d) = 5  and extract(day from d) >= 15)
      or (extract(month from d) = 6  and extract(day from d) <= 25) then 0.7
    else 1.0
  end;
$$;

create or replace function current_window_label(d date default current_date)
returns text language sql immutable as $$
  select case
    when current_window_multiplier(d) = 1.6 then 'silent-house (primary)'
    when current_window_multiplier(d) = 1.2 then 'planning'
    when current_window_multiplier(d) = 1.1 then 'reactivation'
    when current_window_multiplier(d) = 0.7 then 'avoid (graduation noise)'
    else 'off-window'
  end;
$$;

-- Recompute scores for an org. Idempotent; safe to run on every refresh.
create or replace function recompute_scores(target_org uuid)
returns void language plpgsql as $$
declare
  wmult numeric := current_window_multiplier(current_date);
  v_p25 numeric;
  v_p75 numeric;
  v_max numeric;
begin
  select percentile_cont(0.25) within group (order by p.market_value),
         percentile_cont(0.75) within group (order by p.market_value),
         max(p.market_value)
    into v_p25, v_p75, v_max
    from parcels p
   where p.org_id = target_org and p.market_value is not null;

  insert into scores (household_id, org_id, tenure_points, value_points, confirmation_points, window_multiplier, total_score, tier, updated_at)
  select
    h.id,
    h.org_id,
    -- tenure: 0..35 points, capped at 25 yrs owned
    least(coalesce(p.years_owned,0), 25) * (35.0 / 25.0) as tenure_points,
    -- value: 0..30 points, scaled by quartile relative to org universe
    case
      when p.market_value is null then 0
      when p.market_value < v_p25 then 5
      when p.market_value < v_p75 then 18
      else 30
    end as value_points,
    -- confirmation: 0..35 points
    case
      when exists (
        select 1 from confirmed_seniors cs
        where cs.org_id = h.org_id
          and cs.matched_household_id = h.id
          and cs.match_status in ('matched','manual')
      ) then 35 else 0
    end as confirmation_points,
    wmult,
    -- total = (tenure + value + confirm) * window_mult, clamped 0..100
    least(100,
      ((least(coalesce(p.years_owned,0), 25) * (35.0 / 25.0))
       + case when p.market_value is null then 0
              when p.market_value < v_p25 then 5
              when p.market_value < v_p75 then 18
              else 30 end
       + case when exists (
                select 1 from confirmed_seniors cs
                where cs.org_id = h.org_id
                  and cs.matched_household_id = h.id
                  and cs.match_status in ('matched','manual')
              ) then 35 else 0 end
      ) * wmult
    ) as total_score,
    case
      when ((least(coalesce(p.years_owned,0), 25) * (35.0 / 25.0))
       + case when p.market_value is null then 0
              when p.market_value < v_p25 then 5
              when p.market_value < v_p75 then 18
              else 30 end
       + case when exists (
                select 1 from confirmed_seniors cs
                where cs.org_id = h.org_id
                  and cs.matched_household_id = h.id
                  and cs.match_status in ('matched','manual')
              ) then 35 else 0 end
      ) * wmult >= 70 then 'A'
      when ((least(coalesce(p.years_owned,0), 25) * (35.0 / 25.0))
       + case when p.market_value is null then 0
              when p.market_value < v_p25 then 5
              when p.market_value < v_p75 then 18
              else 30 end
       + case when exists (
                select 1 from confirmed_seniors cs
                where cs.org_id = h.org_id
                  and cs.matched_household_id = h.id
                  and cs.match_status in ('matched','manual')
              ) then 35 else 0 end
      ) * wmult >= 45 then 'B'
      else 'C'
    end as tier,
    now()
  from households h
  join parcels p on p.id = h.parcel_id
  where h.org_id = target_org
  on conflict (household_id) do update set
    tenure_points       = excluded.tenure_points,
    value_points        = excluded.value_points,
    confirmation_points = excluded.confirmation_points,
    window_multiplier   = excluded.window_multiplier,
    total_score         = excluded.total_score,
    tier                = excluded.tier,
    updated_at          = now();
end;
$$;

-- Match confirmed-senior names against household last-names within an org.
-- Uses pg_trgm similarity. Auto-marks high-confidence matches; leaves rest pending.
create or replace function match_seniors(target_org uuid, threshold numeric default 0.45)
returns int language plpgsql as $$
declare
  matched_count int := 0;
begin
  with cand as (
    select
      cs.id as senior_id,
      h.id as household_id,
      greatest(
        coalesce(similarity(cs.last_name, h.surname_key), 0),
        coalesce(similarity(cs.senior_name_norm, ho.full_name_norm), 0)
      ) as score
    from confirmed_seniors cs
    join households h on h.org_id = cs.org_id
    left join household_owners ho on ho.household_id = h.id
    where cs.org_id = target_org
      and cs.match_status in ('pending', 'no_match')
  ),
  best as (
    select distinct on (senior_id) senior_id, household_id, score
    from cand
    order by senior_id, score desc
  )
  update confirmed_seniors cs
     set matched_household_id = b.household_id,
         match_confidence = b.score,
         match_status = case when b.score >= threshold then 'matched' else 'no_match' end,
         reviewed_at = now()
    from best b
   where cs.id = b.senior_id;

  get diagnostics matched_count = row_count;
  return matched_count;
end;
$$;

-- Convenience view for the dashboard.
create or replace view v_dashboard as
select
  h.id as household_id,
  h.org_id,
  h.display_name,
  h.surname_key,
  h.owner_names,
  h.status,
  h.notes,
  p.situs_address,
  p.situs_city,
  p.situs_zip,
  p.sqft,
  p.market_value,
  p.last_sale_date,
  p.years_owned,
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

-- Make sure RLS applies to the view by reusing the underlying tables.
grant select on v_dashboard to authenticated;
