scoreboard players set #fly_hit zdi 1
scoreboard players set #fly_mon zdi 0

data modify storage zdash:tracker run.flyaway.detected set value 1b
execute store result storage zdash:tracker run.flyaway.detected_gt long 1 run time query gametime
execute store result storage zdash:tracker run.flyaway.dragon_x int 1 run scoreboard players get #drx zdi
execute store result storage zdash:tracker run.flyaway.dragon_y int 1 run scoreboard players get #dry zdi
execute store result storage zdash:tracker run.flyaway.dragon_z int 1 run scoreboard players get #drz zdi
execute store result storage zdash:tracker run.flyaway.detected_dist2 int 1 run scoreboard players get #fly_dist2 zdi
scoreboard players set #fly_crystals_alive zdi 0
execute in minecraft:the_end as @e[type=minecraft:end_crystal] run scoreboard players add #fly_crystals_alive zdi 1
execute store result storage zdash:tracker run.flyaway.crystals_alive int 1 run scoreboard players get #fly_crystals_alive zdi

# Chat debug requested by user.
tellraw @a [{"text":"[zdash] flyaway node=(","color":"gold"},{"score":{"name":"#fly_node_x","objective":"zdi"},"color":"yellow"},{"text":",","color":"gold"},{"score":{"name":"#fly_node_z","objective":"zdi"},"color":"yellow"},{"text":") dragon=(","color":"gold"},{"score":{"name":"#drx","objective":"zdi"},"color":"yellow"},{"text":",","color":"gold"},{"score":{"name":"#dry","objective":"zdi"},"color":"yellow"},{"text":",","color":"gold"},{"score":{"name":"#drz","objective":"zdi"},"color":"yellow"},{"text":") dist2=","color":"gold"},{"score":{"name":"#fly_dist2","objective":"zdi"},"color":"yellow"},{"text":" crystals=","color":"gold"},{"score":{"name":"#fly_crystals_alive","objective":"zdi"},"color":"yellow"}]
