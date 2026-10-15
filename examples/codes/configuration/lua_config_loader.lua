-- Lua : chargement d'une configuration avec valeurs par défaut et validation.
local defaults = { host = "127.0.0.1", port = 8080, debug = false }

local function load_config(overrides)
    local config = {}
    for key, value in pairs(defaults) do config[key] = value end
    for key, value in pairs(overrides or {}) do
        if defaults[key] == nil then
            error("option inconnue : " .. tostring(key), 2)
        end
        config[key] = value
    end
    assert(type(config.port) == "number" and config.port > 0 and config.port < 65536, "port invalide")
    return config
end

local config = load_config({ port = 9000, debug = true })
print(config.host, config.port, config.debug)
