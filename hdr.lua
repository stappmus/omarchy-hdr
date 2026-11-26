if o.display_hdr_loaded then return end
local tonemaps, hdr_outputs = {}, {}
local last_game_hdr
local function update_game_hdr()
  local monitor = hl.get_active_monitor()
  local value = monitor and hdr_outputs[monitor.name] and "1" or "0"
  if value == last_game_hdr then return end
  last_game_hdr = value
  hl.env("DXVK_HDR", value)
  hl.exec_cmd("dbus-update-activation-environment --systemd DXVK_HDR=" .. value)
end
function o.hdr_games(name, enabled)
  hdr_outputs[name] = enabled
  update_game_hdr()
end
hl.on("monitor.focused", update_game_hdr)
hl.on("monitor.removed", function(m)
  if not m then return end
  if tonemaps[m.name] then
    tonemaps[m.name]:set_enabled(false)
    -- The next device on this port may be an SDR-only monitor.
    hl.monitor({ output = m.name, cm = "srgb", supports_hdr = 0, supports_wide_color = 0,
      max_luminance = -1, max_avg_luminance = -1 })
  end
  o.hdr_games(m.name, false)
end)

function o.hdr_mode(name, enabled, peak, full, white)
  local rule = { output = name, cm = enabled and "hdr" or "srgb" }
  if enabled then
    rule.bitdepth, rule.supports_hdr, rule.supports_wide_color = 10, 1, 1
    rule.sdr_max_luminance, rule.sdr_min_luminance = white or 203, 0
    rule.sdrbrightness, rule.sdrsaturation = 1, 1
    if peak > 0 then rule.max_luminance = peak end
    if full > 0 then rule.max_avg_luminance = full end
    -- Preserve middle gray and diffuse white. Compress only the highlights.
    if not tonemaps[name] then
      tonemaps[name] = hl.window_rule({ name = "omarchy-hdr-" .. name,
        match = { workspace = "m[" .. name .. "]" }, tonemap = "limited" })
    end
  else
    rule.supports_hdr, rule.supports_wide_color = -1, -1
    rule.max_luminance, rule.max_avg_luminance = -1, -1
  end
  if tonemaps[name] then tonemaps[name]:set_enabled(enabled) end
  hl.monitor(rule)
end

o.display_hdr_loaded = true
