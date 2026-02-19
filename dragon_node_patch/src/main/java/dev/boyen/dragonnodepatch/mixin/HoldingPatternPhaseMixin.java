package dev.boyen.dragonnodepatch.mixin;

import dev.boyen.dragonnodepatch.DragonPatchState;
import net.minecraft.entity.boss.dragon.phase.HoldingPatternPhase;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Redirect;

import java.util.Random;

@Mixin(HoldingPatternPhase.class)
public abstract class HoldingPatternPhaseMixin {
    private static final Logger RANKED_DRAGON_NODE_PATCH_LOGGER = LogManager.getLogger("ranked-dragon-node-patch");

    @Redirect(method = "followPath", at = @At(value = "INVOKE", target = "Ljava/util/Random;nextFloat()F"))
    private float rankedDragonNodePatch$patchFirstTargetHeightRoll(Random random) {
        float roll = random.nextFloat();
        DragonPatchState state = (DragonPatchState) ((AbstractPhaseAccessor) this).rankedDragonNodePatch$getDragon();
        if (!state.rankedDragonNodePatch$firstHeightRollConsumed()) {
            state.rankedDragonNodePatch$setFirstHeightRollConsumed(true);
            // Vanilla multiplies this by 20.0f; scaling by 0.75f yields a 0..15 first-node range.
            float patchedRoll = roll * 0.75f;
            RANKED_DRAGON_NODE_PATCH_LOGGER.info(
                    "Overrode first dragon height roll: vanilla={} patched={} (0..15 ranked-like window)",
                    roll,
                    patchedRoll
            );
            return patchedRoll;
        }
        return roll;
    }
}
