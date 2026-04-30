-- Rescore: the public Summit County REST layer doesn't expose sale dates,
-- so we score on what we CAN verify (building age, value, senior confirmation)
-- rather than fake a tenure number. Real years_owned data lands later via
-- the propertyaccess.summitoh.net enrichment scraper.

create or replace function recompute_scores(target_org uuid)
returns void language plpgsql as $$
declare
  wmult numeric := current_window_multiplier(current_date);
  v_p25 numeric;
  v_p75 numeric;
begin
  select percentile_cont(0.25) within group (order by p.market_value),
         percentile_cont(0.75) within group (order by p.market_value)
    into v_p25, v_p75
    from parcels p
   where p.org_id = target_org and p.market_value is not null;

  insert into scores (household_id, org_id, tenure_points, value_points, confirmation_points, window_multiplier, total_score, tier, updated_at)
  select
    h.id,
    h.org_id,
    -- Tenure component: prefer real years_owned if we have it (post-enrichment),
    -- otherwise fall back to building age, capped at 30 yrs (max 25 pts).
    -- Scoring weights stay roughly equivalent so an enriched record doesn't
    -- jump tier just from getting the same age signal twice.
    case
      when p.years_owned is not null then least(p.years_owned, 25) * (25.0 / 25.0)
      when p.year_built is not null then
        least(extract(year from current_date)::int - p.year_built, 30) * (25.0 / 30.0)
      else 0
    end as tenure_points,
    -- Value: 0..30 by quartile within org universe.
    case
      when p.market_value is null then 0
      when p.market_value < v_p25 then 5
      when p.market_value < v_p75 then 18
      else 30
    end as value_points,
    -- Senior confirmation: 0 or 45 (heaviest weight, since this is the realtor's
    -- own verification of a graduating senior in the household).
    case
      when exists (
        select 1 from confirmed_seniors cs
        where cs.org_id = h.org_id
          and cs.matched_household_id = h.id
          and cs.match_status in ('matched','manual')
      ) then 45 else 0
    end as confirmation_points,
    wmult,
    least(100,
      ((case
         when p.years_owned is not null then least(p.years_owned, 25) * 1.0
         when p.year_built is not null then
           least(extract(year from current_date)::int - p.year_built, 30) * (25.0 / 30.0)
         else 0
       end)
       + case when p.market_value is null then 0
              when p.market_value < v_p25 then 5
              when p.market_value < v_p75 then 18
              else 30 end
       + case when exists (
                select 1 from confirmed_seniors cs
                where cs.org_id = h.org_id
                  and cs.matched_household_id = h.id
                  and cs.match_status in ('matched','manual')
              ) then 45 else 0 end
      ) * wmult
    ) as total_score,
    case
      when ((case
              when p.years_owned is not null then least(p.years_owned, 25) * 1.0
              when p.year_built is not null then
                least(extract(year from current_date)::int - p.year_built, 30) * (25.0 / 30.0)
              else 0
             end)
       + case when p.market_value is null then 0
              when p.market_value < v_p25 then 5
              when p.market_value < v_p75 then 18
              else 30 end
       + case when exists (
                select 1 from confirmed_seniors cs
                where cs.org_id = h.org_id
                  and cs.matched_household_id = h.id
                  and cs.match_status in ('matched','manual')
              ) then 45 else 0 end
      ) * wmult >= 70 then 'A'
      when ((case
              when p.years_owned is not null then least(p.years_owned, 25) * 1.0
              when p.year_built is not null then
                least(extract(year from current_date)::int - p.year_built, 30) * (25.0 / 30.0)
              else 0
             end)
       + case when p.market_value is null then 0
              when p.market_value < v_p25 then 5
              when p.market_value < v_p75 then 18
              else 30 end
       + case when exists (
                select 1 from confirmed_seniors cs
                where cs.org_id = h.org_id
                  and cs.matched_household_id = h.id
                  and cs.match_status in ('matched','manual')
              ) then 45 else 0 end
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
