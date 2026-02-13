scoreboard players set zd_found flags 0
scoreboard players set tower settings -1

execute if data storage practice:zdash {target_name:"M-85"} run execute store result score tower settings run data get storage practice:towers towers[{name:"M-85"}].index
execute if data storage practice:zdash {target_name:"M-85"} run scoreboard players set zd_found flags 1
execute if data storage practice:zdash {target_name:"M-88"} run execute store result score tower settings run data get storage practice:towers towers[{name:"M-88"}].index
execute if data storage practice:zdash {target_name:"M-88"} run scoreboard players set zd_found flags 1
execute if data storage practice:zdash {target_name:"M-91"} run execute store result score tower settings run data get storage practice:towers towers[{name:"M-91"}].index
execute if data storage practice:zdash {target_name:"M-91"} run scoreboard players set zd_found flags 1
execute if data storage practice:zdash {target_name:"Small Boy"} run execute store result score tower settings run data get storage practice:towers towers[{name:"Small Boy"}].index
execute if data storage practice:zdash {target_name:"Small Boy"} run scoreboard players set zd_found flags 1
execute if data storage practice:zdash {target_name:"Small Cage"} run execute store result score tower settings run data get storage practice:towers towers[{name:"Small Cage"}].index
execute if data storage practice:zdash {target_name:"Small Cage"} run scoreboard players set zd_found flags 1
execute if data storage practice:zdash {target_name:"T-100"} run execute store result score tower settings run data get storage practice:towers towers[{name:"T-100"}].index
execute if data storage practice:zdash {target_name:"T-100"} run scoreboard players set zd_found flags 1
execute if data storage practice:zdash {target_name:"T-94"} run execute store result score tower settings run data get storage practice:towers towers[{name:"T-94"}].index
execute if data storage practice:zdash {target_name:"T-94"} run scoreboard players set zd_found flags 1
execute if data storage practice:zdash {target_name:"T-97"} run execute store result score tower settings run data get storage practice:towers towers[{name:"T-97"}].index
execute if data storage practice:zdash {target_name:"T-97"} run scoreboard players set zd_found flags 1
execute if data storage practice:zdash {target_name:"Tall Boy"} run execute store result score tower settings run data get storage practice:towers towers[{name:"Tall Boy"}].index
execute if data storage practice:zdash {target_name:"Tall Boy"} run scoreboard players set zd_found flags 1
execute if data storage practice:zdash {target_name:"Tall Cage"} run execute store result score tower settings run data get storage practice:towers towers[{name:"Tall Cage"}].index
execute if data storage practice:zdash {target_name:"Tall Cage"} run scoreboard players set zd_found flags 1

execute if score zd_found flags matches 0 run function practice:level/choose_tower

execute if score zd_found flags matches 1 run tag @e[tag=tower] remove selected
execute if score zd_found flags matches 1 if score location_act settings matches 0 run tag @e[tag=tower,tag=front] add selected
execute if score zd_found flags matches 1 if score location_act settings matches 1 run tag @e[tag=tower,tag=back] add selected
execute if score zd_found flags matches 1 if score direction_act settings matches 0 run tag @e[tag=tower,tag=!diagonal] remove selected
execute if score zd_found flags matches 1 if score direction_act settings matches 1 run tag @e[tag=tower,tag=!straight] remove selected
execute if score zd_found flags matches 1 run scoreboard players operation @e[tag=tower,tag=selected] tower_order = tower settings
execute if score zd_found flags matches 1 run data modify storage practice:towers active set from storage practice:zdash target_name
