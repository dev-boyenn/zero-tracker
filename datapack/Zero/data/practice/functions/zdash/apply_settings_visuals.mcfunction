execute store result storage practice:gui pages[1].entries[{tag:{index:0b}}].value byte 1 run scoreboard players get location settings
execute store result storage practice:gui pages[1].entries[{tag:{index:1b}}].value byte 1 run scoreboard players get direction settings
execute store result storage practice:gui pages[1].entries[{tag:{index:7b}}].value byte 1 run scoreboard players get rotation settings

data modify storage practice:gui pages[1].entries[{tag:{index:0b}}].tag.display.Lore set from storage practice:gui pages[1].entries[{tag:{index:0b}}].tag.display.LoreGray
execute if score location settings matches 0 run data modify storage practice:gui pages[1].entries[{tag:{index:0b}}].tag.display.Lore[0] set from storage practice:gui pages[1].entries[{tag:{index:0b}}].tag.display.LoreColor[0]
execute if score location settings matches 1 run data modify storage practice:gui pages[1].entries[{tag:{index:0b}}].tag.display.Lore[1] set from storage practice:gui pages[1].entries[{tag:{index:0b}}].tag.display.LoreColor[1]
execute if score location settings matches 2 run data modify storage practice:gui pages[1].entries[{tag:{index:0b}}].tag.display.Lore[2] set from storage practice:gui pages[1].entries[{tag:{index:0b}}].tag.display.LoreColor[2]

data modify storage practice:gui pages[1].entries[{tag:{index:1b}}].tag.display.Lore set from storage practice:gui pages[1].entries[{tag:{index:1b}}].tag.display.LoreGray
execute if score direction settings matches 0 run data modify storage practice:gui pages[1].entries[{tag:{index:1b}}].tag.display.Lore[0] set from storage practice:gui pages[1].entries[{tag:{index:1b}}].tag.display.LoreColor[0]
execute if score direction settings matches 1 run data modify storage practice:gui pages[1].entries[{tag:{index:1b}}].tag.display.Lore[1] set from storage practice:gui pages[1].entries[{tag:{index:1b}}].tag.display.LoreColor[1]
execute if score direction settings matches 2 run data modify storage practice:gui pages[1].entries[{tag:{index:1b}}].tag.display.Lore[2] set from storage practice:gui pages[1].entries[{tag:{index:1b}}].tag.display.LoreColor[2]

data modify storage practice:gui pages[1].entries[{tag:{index:7b}}].tag.display.Lore set from storage practice:gui pages[1].entries[{tag:{index:7b}}].tag.display.LoreGray
execute if score rotation settings matches 0 run data modify storage practice:gui pages[1].entries[{tag:{index:7b}}].tag.display.Lore[0] set from storage practice:gui pages[1].entries[{tag:{index:7b}}].tag.display.LoreColor[0]
execute if score rotation settings matches 1 run data modify storage practice:gui pages[1].entries[{tag:{index:7b}}].tag.display.Lore[1] set from storage practice:gui pages[1].entries[{tag:{index:7b}}].tag.display.LoreColor[1]
execute if score rotation settings matches 2 run data modify storage practice:gui pages[1].entries[{tag:{index:7b}}].tag.display.Lore[2] set from storage practice:gui pages[1].entries[{tag:{index:7b}}].tag.display.LoreColor[2]

function practice:gui/load
