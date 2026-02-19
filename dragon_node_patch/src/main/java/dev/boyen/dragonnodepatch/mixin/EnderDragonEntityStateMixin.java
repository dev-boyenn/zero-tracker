package dev.boyen.dragonnodepatch.mixin;

import dev.boyen.dragonnodepatch.DragonPatchState;
import net.minecraft.entity.boss.dragon.EnderDragonEntity;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Unique;

@Mixin(EnderDragonEntity.class)
public abstract class EnderDragonEntityStateMixin implements DragonPatchState {
    @Unique
    private boolean rankedDragonNodePatch$firstHeightRollConsumed;

    @Override
    public boolean rankedDragonNodePatch$firstHeightRollConsumed() {
        return this.rankedDragonNodePatch$firstHeightRollConsumed;
    }

    @Override
    public void rankedDragonNodePatch$setFirstHeightRollConsumed(boolean value) {
        this.rankedDragonNodePatch$firstHeightRollConsumed = value;
    }
}
