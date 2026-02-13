scoreboard players set location settings 1
scoreboard players set direction settings 0
scoreboard players set rotation settings 1
scoreboard players set tower settings 6
function practice:zdash/select_tower_by_score
function practice:zdash/apply_settings_visuals
scoreboard players set zd_force flags 0
data modify storage practice:zdash target_name set value "T-94"
tellraw @a [{"text":"[ZDASH] Target set: T-94 | Back | Diagonal CCW","color":"gray"}]
