scoreboard players set location settings 2
scoreboard players set direction settings 2
scoreboard players set rotation settings 2
function practice:gui/pages/home/enable_all_towers
function practice:zdash/apply_settings_visuals
scoreboard players set zd_force flags 0
data modify storage practice:zdash target_name set value "Full Random"
tellraw @a [{"text":"[ZDASH] Target set: Full Random","color":"gray"}]
