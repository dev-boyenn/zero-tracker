# Capture once when player is in End: highest non-air block at player column.
scoreboard players set #entry_player_y zdi 0
scoreboard players set #entry_top_y zdi -1
scoreboard players set #entry_top_is_endstone zdi 0

execute as @a[limit=1] in minecraft:the_end store result score #entry_player_y zdi run data get entity @s Pos[1] 1
execute as @a[limit=1] in minecraft:the_end at @s run function zdash_tracker:scan_entry_column

data modify storage zdash:tracker run.end_entry set value {logged:0b,gt:0L,player_y:0,top_y:-1,top_is_endstone:0b}
execute store result storage zdash:tracker run.end_entry.gt long 1 run time query gametime
execute store result storage zdash:tracker run.end_entry.player_y int 1 run scoreboard players get #entry_player_y zdi
execute store result storage zdash:tracker run.end_entry.top_y int 1 run scoreboard players get #entry_top_y zdi
execute if score #entry_top_is_endstone zdi matches 1 run data modify storage zdash:tracker run.end_entry.top_is_endstone set value 1b
execute unless score #entry_top_is_endstone zdi matches 1 run data modify storage zdash:tracker run.end_entry.top_is_endstone set value 0b

data modify storage zdash:tracker run.end_entry.logged set value 1b
scoreboard players set #end_entry_logged zdi 1
