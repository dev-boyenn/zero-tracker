scoreboard players set #dragon_died zdi 1
data modify storage zdash:tracker run.dragon_died set value 1b
execute store result storage zdash:tracker run.dragon_died_gt long 1 run time query gametime
tellraw @a [{"text":"[zdash] dragon_died=1","color":"green"}]
