# Start stronghold tracking when standing near stronghold-unique blocks.
execute unless data storage zdash:tracker stronghold.eye_spy{logged:1b} if block ~ ~ ~ minecraft:end_portal_frame run function zdash_tracker:stronghold/on_eye_spy
execute unless data storage zdash:tracker stronghold.eye_spy{logged:1b} if block ~ ~ ~ minecraft:stone_bricks run function zdash_tracker:stronghold/on_eye_spy
execute unless data storage zdash:tracker stronghold.eye_spy{logged:1b} if block ~ ~ ~ minecraft:cracked_stone_bricks run function zdash_tracker:stronghold/on_eye_spy
execute unless data storage zdash:tracker stronghold.eye_spy{logged:1b} if block ~ ~ ~ minecraft:mossy_stone_bricks run function zdash_tracker:stronghold/on_eye_spy
execute unless data storage zdash:tracker stronghold.eye_spy{logged:1b} if block ~ ~ ~ minecraft:chiseled_stone_bricks run function zdash_tracker:stronghold/on_eye_spy
execute unless data storage zdash:tracker stronghold.eye_spy{logged:1b} if block ~ ~ ~ minecraft:infested_stone run function zdash_tracker:stronghold/on_eye_spy
execute unless data storage zdash:tracker stronghold.eye_spy{logged:1b} if block ~ ~ ~ minecraft:infested_stone_bricks run function zdash_tracker:stronghold/on_eye_spy
execute unless data storage zdash:tracker stronghold.eye_spy{logged:1b} if block ~ ~ ~ minecraft:infested_cracked_stone_bricks run function zdash_tracker:stronghold/on_eye_spy
execute unless data storage zdash:tracker stronghold.eye_spy{logged:1b} if block ~ ~ ~ minecraft:infested_mossy_stone_bricks run function zdash_tracker:stronghold/on_eye_spy
execute unless data storage zdash:tracker stronghold.eye_spy{logged:1b} if block ~ ~ ~ minecraft:infested_chiseled_stone_bricks run function zdash_tracker:stronghold/on_eye_spy
