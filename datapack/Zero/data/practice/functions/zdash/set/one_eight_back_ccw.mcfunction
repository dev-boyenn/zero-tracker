scoreboard players set location settings 1
scoreboard players set direction settings 1
scoreboard players set rotation settings 1
function practice:gui/pages/home/enable_all_towers
function practice:zdash/apply_settings_visuals
scoreboard players set zd_force flags 0
data modify storage practice:zdash target_name set value "1/8"
tellraw @a [{"text":"[ZDASH] Target set: 1/8 | Back | Straight CCW","color":"gray"}]
