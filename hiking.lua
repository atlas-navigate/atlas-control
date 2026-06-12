-- OSRM Hiking Profile for Atlas Control (OSRM v6 compatible)
-- Based on the v6 foot.lua but tuned to prefer off-road trails,
-- paths, and tracks over paved roads.
--
-- Trail-preference logic:
--   Trails / paths / tracks          -> speed 6  (preferred)
--   Pedestrian / footway / sidewalk  -> speed 5  (neutral)
--   Residential / service roads      -> speed 3  (penalized)
--   Major roads                      -> speed 2  (strongly penalized)
--   Motorway / trunk                 -> excluded

api_version = 2

-- OSRM resolves `require()` relative to the profile path. Since this custom
-- profile lives outside the installed OSRM profiles directory, add OSRM's
-- shared profile library paths explicitly before loading helper modules.
package.path = table.concat({
  package.path,
  '/usr/local/share/osrm/profiles/?.lua',
  '/usr/local/share/osrm/profiles/?/init.lua',
  '/usr/share/osrm/profiles/?.lua',
  '/usr/share/osrm/profiles/?/init.lua',
}, ';')

Set        = require('lib/set')
Sequence   = require('lib/sequence')
Handlers   = require('lib/way_handlers')
find_access_tag = require('lib/access').find_access_tag

function setup()
  return {
    properties = {
      weight_name                   = 'duration',
      max_speed_for_map_matching    = 40/3.6,
      call_tagless_node_function    = false,
      traffic_signal_penalty        = 2,
      u_turn_penalty                = 2,
      continue_straight_at_waypoint = false,
      use_turn_restrictions         = false,
    },

    default_mode   = mode.walking,
    default_speed  = 5,
    oneway_handling = 'specific',

    barrier_blacklist = Set {
      'yes',
      'wall',
      'fence',
    },

    access_tag_whitelist = Set {
      'yes',
      'foot',
      'permissive',
      'designated',
      'public',
    },

    access_tag_blacklist = Set {
      'no',
      'private',
      'restricted',
      'military',
      'inaccessible',
      'agricultural',
      'forestry',
      'delivery',
    },

    restricted_access_tag_list = Set { },
    restricted_highway_whitelist = Set { },
    construction_whitelist = Set {},
    service_access_tag_blacklist = Set { },

    access_tags_hierarchy = Sequence { 'foot', 'access' },

    restrictions = Sequence { 'foot' },

    suffix_list = Set {
      'N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW', 'North', 'South', 'West', 'East'
    },

    avoid = Set {
      'motorway', 'motorway_link', 'trunk', 'trunk_link',
      'impassable', 'proposed',
    },

    speeds = Sequence {
      highway = {
        -- Trails and off-road ways — preferred
        path            = 6,
        track           = 6,
        bridleway       = 6,
        -- Pedestrian infrastructure — neutral
        footway         = 5,
        pedestrian      = 5,
        steps           = 2,
        corridor        = 5,
        living_street   = 4,
        pier            = 4,
        platform        = 5,
        -- Roads — heavily penalized so router avoids them when any trail exists.
        -- Service roads retain speed 2 for trailhead driveway access;
        -- all paved/named roads drop to 1 so a 1 km trail beats a 200 m road detour.
        service         = 2,
        residential     = 1,
        unclassified    = 1,
        road            = 1,
        tertiary        = 1,
        tertiary_link   = 1,
        secondary       = 1,
        secondary_link  = 1,
        primary         = 1,
        primary_link    = 1,
      },
      railway = {
        platform        = 5,
      },
      amenity = {
        parking         = 3,
        parking_entrance= 3,
      },
      man_made = {
        pier            = 4,
      },
      leisure = {
        track           = 6,
      },
    },

    route_speeds = {
      ferry = 5,
    },

    bridge_speeds = { },

    surface_speeds = {
      asphalt          = 4,
      paved            = 4,
      concrete         = 4,
      cobblestone      = 4,
      metal            = 4,
      sett             = 4,
      paving_stones    = 4,
      ground           = 6,
      dirt             = 6,
      earth            = 6,
      grass            = 6,
      grass_paver      = 5,
      gravel           = 6,
      fine_gravel      = 6,
      pebblestone      = 5,
      sand             = 4,
      mud              = 4,
      wood             = 5,
      boardwalk        = 5,
      compacted        = 6,
    },

    tracktype_speeds = {
      grade1 = 5,
      grade2 = 5,
      grade3 = 6,
      grade4 = 6,
      grade5 = 6,
    },

    smoothness_speeds = { },

    sac_scale_speeds = {
      hiking                    = 6,
      mountain_hiking           = 5,
      demanding_mountain_hiking = 4,
      alpine_hiking             = 3,
      demanding_alpine_hiking   = 2,
      difficult_alpine_hiking   = 1,
    },
  }
end

function process_node(profile, node, result)
  local access = find_access_tag(node, profile.access_tags_hierarchy)
  if access then
    if profile.access_tag_blacklist[access] then
      result.barrier = true
    end
  else
    local barrier = node:get_value_by_key("barrier")
    if barrier then
      local bollard = node:get_value_by_key("bollard")
      local rising_bollard = bollard and "rising" == bollard
      if profile.barrier_blacklist[barrier] and not rising_bollard then
        result.barrier = true
      end
    end
  end

  local tag = node:get_value_by_key("highway")
  if "traffic_signals" == tag then
    result.traffic_lights = true
  end
end

function process_way(profile, way, result)
  local data = {
    highway  = way:get_value_by_key('highway'),
    bridge   = way:get_value_by_key('bridge'),
    route    = way:get_value_by_key('route'),
    leisure  = way:get_value_by_key('leisure'),
    man_made = way:get_value_by_key('man_made'),
    railway  = way:get_value_by_key('railway'),
    platform = way:get_value_by_key('platform'),
    amenity  = way:get_value_by_key('amenity'),
    public_transport = way:get_value_by_key('public_transport'),
  }

  if next(data) == nil then return end

  -- Apply sac_scale speed override after standard handlers
  local handlers = Sequence {
    WayHandlers.default_mode,
    WayHandlers.blocked_ways,
    WayHandlers.access,
    WayHandlers.oneway,
    WayHandlers.destinations,
    WayHandlers.ferries,
    WayHandlers.movables,
    WayHandlers.speed,
    WayHandlers.surface,
    WayHandlers.classification,
    WayHandlers.roundabouts,
    WayHandlers.startpoint,
    WayHandlers.names,
    WayHandlers.weights,
  }

  WayHandlers.run(profile, way, result, data, handlers)

  if result.forward_speed > 0 then
    -- Boost ways explicitly designated for foot/hiking travel so the router
    -- strongly prefers them over roads even when no highway tag is set.
    -- Covers OSM tags: foot=designated, foot=yes, hiking=yes
    local foot   = way:get_value_by_key('foot')
    local hiking = way:get_value_by_key('hiking')
    if foot == 'designated' or foot == 'yes' or hiking == 'yes' or hiking == 'designated' then
      result.forward_speed  = math.max(result.forward_speed,  6)
      result.backward_speed = math.max(result.backward_speed, 6)
      result.forward_rate   = result.forward_speed  / 3.6
      result.backward_rate  = result.backward_speed / 3.6
    end

    -- Ways tagged designation=public_footpath / public_bridleway are officially
    -- public trails in many countries — treat them as preferred.
    local desig = way:get_value_by_key('designation')
    if desig == 'public_footpath' or desig == 'public_bridleway'
        or desig == 'national_trail' or desig == 'regional_trail' then
      result.forward_speed  = math.max(result.forward_speed,  6)
      result.backward_speed = math.max(result.backward_speed, 6)
      result.forward_rate   = result.forward_speed  / 3.6
      result.backward_rate  = result.backward_speed / 3.6
    end

    -- trail_visibility degrades speed — poor visibility means rough/unmaintained
    local vis_speeds = {
      excellent = 6, good = 6, intermediate = 5,
      bad = 3, horrible = 2, no = 1,
    }
    local vis = way:get_value_by_key('trail_visibility')
    if vis and vis_speeds[vis] then
      local capped = vis_speeds[vis]
      result.forward_speed  = math.min(result.forward_speed,  capped)
      result.backward_speed = math.min(result.backward_speed, capped)
      result.forward_rate   = result.forward_speed  / 3.6
      result.backward_rate  = result.backward_speed / 3.6
    end

    -- sac_scale: slow down on demanding terrain
    local sac_scale = way:get_value_by_key('sac_scale')
    if sac_scale and profile.sac_scale_speeds[sac_scale] then
      local capped = profile.sac_scale_speeds[sac_scale]
      result.forward_speed  = math.min(result.forward_speed,  capped)
      result.backward_speed = math.min(result.backward_speed, capped)
      result.forward_rate   = result.forward_speed  / 3.6
      result.backward_rate  = result.backward_speed / 3.6
    end
  end
end

function process_turn(profile, turn)
  turn.duration = 0.

  if turn.direction_modifier == direction_modifier.u_turn then
    turn.duration = turn.duration + profile.properties.u_turn_penalty
  end

  if turn.has_traffic_light then
    turn.duration = profile.properties.traffic_signal_penalty
  end
end

return {
  setup        = setup,
  process_way  = process_way,
  process_node = process_node,
  process_turn = process_turn,
}
