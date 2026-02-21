package zdash.stronghold;

import java.io.BufferedReader;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

import kaptainwutax.biomeutils.source.OverworldBiomeSource;
import kaptainwutax.featureutils.structure.Stronghold;
import kaptainwutax.featureutils.structure.generator.piece.StructurePiece;
import kaptainwutax.featureutils.structure.generator.structure.StrongholdGenerator;
import kaptainwutax.mcutils.rand.ChunkRand;
import kaptainwutax.mcutils.state.Dimension;
import kaptainwutax.mcutils.util.block.BlockBox;
import kaptainwutax.mcutils.util.pos.CPos;
import kaptainwutax.mcutils.version.MCVersion;
import kaptainwutax.seedutils.rand.JRand;
import kaptainwutax.terrainutils.TerrainGenerator;

public final class StrongholdCrackerMain {
    private StrongholdCrackerMain() {
    }

    private static final class Sample {
        int gt;
        int x;
        int y;
        int z;
        int dim;
    }

    private static final class MappedSample {
        Sample sample;
        int roomId;
    }

    private static final class Visit {
        int roomId;
        int enterGt;
        int exitGt;

        int durationTicks() {
            return Math.max(0, exitGt - enterGt);
        }
    }

    private static final class CandidateResult {
        CPos start;
        List<Stronghold.Piece> pieces;
        List<MappedSample> mapped;
        int mappedHits;
        int strictHits;
        double startDist2;
    }

    public static void main(String[] args) throws Exception {
        Map<String, String> argv = parseArgs(args);
        if (!argv.containsKey("seed") || !argv.containsKey("samples") || !argv.containsKey("out")) {
            System.err.println("Usage: java ... StrongholdCrackerMain --seed <seed> --samples <samples.csv> --out <output.json>");
            System.exit(2);
            return;
        }

        long seed = Long.parseLong(argv.get("seed"));
        Path samplesPath = Path.of(argv.get("samples"));
        Path outPath = Path.of(argv.get("out"));
        List<Sample> allSamples = readSamples(samplesPath);
        if (allSamples.isEmpty()) {
            throw new IllegalStateException("No samples found in: " + samplesPath);
        }

        List<Sample> overworldSamples = new ArrayList<>();
        for (Sample sample : allSamples) {
            if (sample.dim == 0) {
                overworldSamples.add(sample);
            }
        }
        List<Sample> navSamples = overworldSamples.isEmpty() ? allSamples : overworldSamples;

        Sample anchor = navSamples.get(0);
        double anchorX = scaledToDouble(anchor.x);
        double anchorZ = scaledToDouble(anchor.z);

        MCVersion version = MCVersion.v1_16_1;
        OverworldBiomeSource biomeSource = new OverworldBiomeSource(version, seed);
        Stronghold stronghold = new Stronghold(version);
        CPos[] starts = stronghold.getAllStarts(biomeSource, new JRand(0L));
        if (starts.length == 0) {
            throw new IllegalStateException("FeatureUtils returned no stronghold starts.");
        }

        CandidateResult best = chooseBestCandidate(starts, navSamples, biomeSource, version, anchorX, anchorZ);
        CPos chosenStart = best.start;
        List<Stronghold.Piece> pieces = best.pieces;
        IdentityHashMap<StructurePiece<?>, Integer> pieceIndex = new IdentityHashMap<>();
        for (int i = 0; i < pieces.size(); i++) {
            pieceIndex.put(pieces.get(i), i);
        }

        List<int[]> edges = buildEdges(pieces, pieceIndex);
        List<MappedSample> mapped = best.mapped;
        List<Visit> visits = computeVisits(mapped, pieces);
        int starterRoomId = findStarterRoomId(pieces, visits);
        Map<Integer, Integer> roomTicks = computeRoomTicks(mapped);
        Set<Integer> distinctRooms = new HashSet<>();
        for (MappedSample mappedSample : mapped) {
            if (mappedSample.roomId >= 0) {
                distinctRooms.add(mappedSample.roomId);
            }
        }

        int starterTicks = starterRoomId >= 0 ? roomTicks.getOrDefault(starterRoomId, 0) : 0;

        String json = toJson(
            seed,
            chosenStart,
            anchorX,
            anchorZ,
            pieces,
            edges,
            mapped,
            visits,
            distinctRooms.size(),
            starterRoomId,
            starterTicks,
            allSamples.size(),
            navSamples.size()
        );
        if (outPath.getParent() != null) {
            Files.createDirectories(outPath.getParent());
        }
        Files.writeString(outPath, json, StandardCharsets.UTF_8);

        System.out.println("Wrote stronghold map: " + outPath);
        System.out.println("Stronghold start chunk: " + chosenStart.getX() + ", " + chosenStart.getZ());
        System.out.println("Start selection score: mapped_hits=" + best.mappedHits + " strict_hits=" + best.strictHits);
        System.out.println("Pieces: " + pieces.size() + " | Rooms entered: " + distinctRooms.size());
        System.out.println("Starter room id: " + starterRoomId + " | Starter ticks: " + starterTicks);
    }

    private static Map<String, String> parseArgs(String[] args) {
        Map<String, String> out = new HashMap<>();
        for (int i = 0; i < args.length; i++) {
            String key = args[i];
            if (!key.startsWith("--")) {
                continue;
            }
            String cleaned = key.substring(2).trim().toLowerCase(Locale.ROOT);
            if (cleaned.isEmpty()) {
                continue;
            }
            String value = "";
            if ((i + 1) < args.length) {
                value = args[i + 1];
                i++;
            }
            out.put(cleaned, value);
        }
        return out;
    }

    private static List<Sample> readSamples(Path path) throws IOException {
        List<Sample> out = new ArrayList<>();
        try (BufferedReader reader = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
            String line;
            while ((line = reader.readLine()) != null) {
                String trimmed = line.trim();
                if (trimmed.isEmpty() || trimmed.startsWith("#")) {
                    continue;
                }
                if (trimmed.toLowerCase(Locale.ROOT).startsWith("gt,")) {
                    continue;
                }
                String[] parts = trimmed.split("[,\\t ]+");
                if (parts.length < 4) {
                    continue;
                }
                Sample sample = new Sample();
                sample.gt = parseInt(parts[0], 0);
                sample.x = parseInt(parts[1], 0);
                sample.y = parseInt(parts[2], 0);
                sample.z = parseInt(parts[3], 0);
                sample.dim = parts.length >= 5 ? parseInt(parts[4], 0) : 0;
                out.add(sample);
            }
        }
        out.sort((a, b) -> Integer.compare(a.gt, b.gt));
        return out;
    }

    private static int parseInt(String raw, int fallback) {
        try {
            return Integer.parseInt(raw.trim());
        } catch (Exception ignored) {
            return fallback;
        }
    }

    private static CPos chooseNearestStart(CPos[] starts, double x, double z) {
        CPos best = starts[0];
        double bestDist2 = Double.MAX_VALUE;
        for (CPos candidate : starts) {
            double sx = (candidate.getX() << 4) + 8.0;
            double sz = (candidate.getZ() << 4) + 8.0;
            double dx = x - sx;
            double dz = z - sz;
            double d2 = dx * dx + dz * dz;
            if (d2 < bestDist2) {
                bestDist2 = d2;
                best = candidate;
            }
        }
        return best;
    }

    private static CandidateResult chooseBestCandidate(
        CPos[] starts,
        List<Sample> samples,
        OverworldBiomeSource biomeSource,
        MCVersion version,
        double anchorX,
        double anchorZ
    ) {
        List<CPos> sortedStarts = new ArrayList<>();
        for (CPos start : starts) {
            sortedStarts.add(start);
        }
        sortedStarts.sort(Comparator.comparingDouble(start -> distance2ToStart(start, anchorX, anchorZ)));
        int maxCandidates = Math.min(12, sortedStarts.size());

        CandidateResult best = null;
        for (int i = 0; i < maxCandidates; i++) {
            CPos candidateStart = sortedStarts.get(i);
            List<Stronghold.Piece> candidatePieces = generatePiecesForStart(version, biomeSource, candidateStart);
            List<MappedSample> candidateMapped = mapSamples(samples, candidatePieces);
            int mappedHits = 0;
            for (MappedSample mappedSample : candidateMapped) {
                if (mappedSample.roomId >= 0) {
                    mappedHits++;
                }
            }
            int strictHits = countStrictHits(samples, candidatePieces);
            double dist2 = distance2ToStart(candidateStart, anchorX, anchorZ);

            CandidateResult current = new CandidateResult();
            current.start = candidateStart;
            current.pieces = candidatePieces;
            current.mapped = candidateMapped;
            current.mappedHits = mappedHits;
            current.strictHits = strictHits;
            current.startDist2 = dist2;

            if (best == null) {
                best = current;
                continue;
            }
            boolean better = false;
            if (current.mappedHits > best.mappedHits) {
                better = true;
            } else if (current.mappedHits == best.mappedHits && current.strictHits > best.strictHits) {
                better = true;
            } else if (
                current.mappedHits == best.mappedHits
                    && current.strictHits == best.strictHits
                    && current.startDist2 < best.startDist2
            ) {
                better = true;
            }
            if (better) {
                best = current;
            }
        }
        if (best == null) {
            // Should never happen with non-empty starts; keep nearest fallback.
            CPos nearest = chooseNearestStart(starts, anchorX, anchorZ);
            CandidateResult fallback = new CandidateResult();
            fallback.start = nearest;
            fallback.pieces = generatePiecesForStart(version, biomeSource, nearest);
            fallback.mapped = mapSamples(samples, fallback.pieces);
            fallback.mappedHits = 0;
            fallback.strictHits = 0;
            fallback.startDist2 = distance2ToStart(nearest, anchorX, anchorZ);
            return fallback;
        }
        return best;
    }

    private static List<Stronghold.Piece> generatePiecesForStart(
        MCVersion version,
        OverworldBiomeSource biomeSource,
        CPos start
    ) {
        TerrainGenerator terrainGenerator = TerrainGenerator.of(Dimension.OVERWORLD, biomeSource);
        StrongholdGenerator generator = new StrongholdGenerator(version);
        generator.generate(terrainGenerator, start.getX(), start.getZ(), new ChunkRand());
        return new ArrayList<>(generator.pieceList);
    }

    private static double distance2ToStart(CPos start, double x, double z) {
        double sx = (start.getX() << 4) + 8.0;
        double sz = (start.getZ() << 4) + 8.0;
        double dx = x - sx;
        double dz = z - sz;
        return dx * dx + dz * dz;
    }

    private static int countStrictHits(List<Sample> samples, List<Stronghold.Piece> pieces) {
        int hits = 0;
        for (Sample sample : samples) {
            double x = scaledToDouble(sample.x);
            double y = scaledToDouble(sample.y);
            double z = scaledToDouble(sample.z);
            int bx = (int) Math.floor(x);
            int by = (int) Math.floor(y);
            int bz = (int) Math.floor(z);
            if (isInsideAnyBox3d(bx, by, bz, pieces)) {
                hits++;
            }
        }
        return hits;
    }

    private static boolean isInsideAnyBox3d(int bx, int by, int bz, List<Stronghold.Piece> pieces) {
        for (Stronghold.Piece piece : pieces) {
            BlockBox box = piece.getBoundingBox();
            if (box == null) {
                continue;
            }
            if (bx >= box.minX && bx <= box.maxX && by >= box.minY && by <= box.maxY && bz >= box.minZ && bz <= box.maxZ) {
                return true;
            }
        }
        return false;
    }

    private static List<int[]> buildEdges(
        List<Stronghold.Piece> pieces,
        IdentityHashMap<StructurePiece<?>, Integer> pieceIndex
    ) {
        List<int[]> edges = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        for (int i = 0; i < pieces.size(); i++) {
            Stronghold.Piece piece = pieces.get(i);
            for (Stronghold.Piece child : piece.children) {
                Integer childIndex = pieceIndex.get(child);
                if (childIndex == null) {
                    continue;
                }
                int a = Math.min(i, childIndex);
                int b = Math.max(i, childIndex);
                String key = a + ":" + b;
                if (seen.add(key)) {
                    edges.add(new int[] {a, b});
                }
            }
        }
        return edges;
    }

    private static List<MappedSample> mapSamples(List<Sample> samples, List<Stronghold.Piece> pieces) {
        List<MappedSample> out = new ArrayList<>();
        for (Sample sample : samples) {
            MappedSample mapped = new MappedSample();
            mapped.sample = sample;
            mapped.roomId = findRoomForSample(sample, pieces);
            out.add(mapped);
        }
        return out;
    }

    private static int findRoomForSample(Sample sample, List<Stronghold.Piece> pieces) {
        double x = scaledToDouble(sample.x);
        double y = scaledToDouble(sample.y);
        double z = scaledToDouble(sample.z);
        int bx = (int) Math.floor(x);
        int by = (int) Math.floor(y);
        int bz = (int) Math.floor(z);

        for (int i = 0; i < pieces.size(); i++) {
            BlockBox box = pieces.get(i).getBoundingBox();
            if (box == null) {
                continue;
            }
            if (bx >= box.minX && bx <= box.maxX && by >= box.minY && by <= box.maxY && bz >= box.minZ && bz <= box.maxZ) {
                return i;
            }
        }
        for (int i = 0; i < pieces.size(); i++) {
            BlockBox box = pieces.get(i).getBoundingBox();
            if (box == null) {
                continue;
            }
            if (bx >= box.minX && bx <= box.maxX && bz >= box.minZ && bz <= box.maxZ) {
                return i;
            }
        }
        int bestIdx = -1;
        double bestDist2 = Double.MAX_VALUE;
        for (int i = 0; i < pieces.size(); i++) {
            BlockBox box = pieces.get(i).getBoundingBox();
            if (box == null) {
                continue;
            }
            double nx = clamp(x, box.minX, box.maxX);
            double nz = clamp(z, box.minZ, box.maxZ);
            double dx = x - nx;
            double dz = z - nz;
            double d2 = dx * dx + dz * dz;
            if (d2 < bestDist2) {
                bestDist2 = d2;
                bestIdx = i;
            }
        }
        // Reject absurd mappings (typically outlier samples after leaving stronghold area).
        if (bestDist2 > (64.0 * 64.0)) {
            return -1;
        }
        return bestIdx;
    }

    private static Map<Integer, Integer> computeRoomTicks(List<MappedSample> mapped) {
        Map<Integer, Integer> ticks = new HashMap<>();
        for (int i = 0; i + 1 < mapped.size(); i++) {
            MappedSample current = mapped.get(i);
            MappedSample next = mapped.get(i + 1);
            if (current.roomId < 0) {
                continue;
            }
            int dt = Math.max(0, next.sample.gt - current.sample.gt);
            ticks.put(current.roomId, ticks.getOrDefault(current.roomId, 0) + dt);
        }
        return ticks;
    }

    private static List<Visit> computeVisits(List<MappedSample> mapped, List<Stronghold.Piece> pieces) {
        List<Visit> visits = new ArrayList<>();
        if (mapped.isEmpty()) {
            return visits;
        }
        int currentRoom = -2;
        int enterGt = mapped.get(0).sample.gt;
        for (MappedSample mappedSample : mapped) {
            int room = mappedSample.roomId;
            int gt = mappedSample.sample.gt;
            if (room == currentRoom) {
                continue;
            }
            if (currentRoom >= 0) {
                Visit visit = new Visit();
                visit.roomId = currentRoom;
                visit.enterGt = enterGt;
                visit.exitGt = gt;
                visits.add(visit);
            }
            currentRoom = room;
            enterGt = gt;
        }
        if (currentRoom >= 0) {
            Visit visit = new Visit();
            visit.roomId = currentRoom;
            visit.enterGt = enterGt;
            visit.exitGt = mapped.get(mapped.size() - 1).sample.gt;
            visits.add(visit);
        }
        // Noise cleanup: near End portal transition, last samples can snap to a neighboring
        // corridor due bbox edges. Keep the final portal segment contiguous in this case.
        if (visits.size() >= 2) {
            Visit last = visits.get(visits.size() - 1);
            Visit prev = visits.get(visits.size() - 2);
            String prevType = roomTypeForId(pieces, prev.roomId);
            String lastType = roomTypeForId(pieces, last.roomId);
            if ("PortalRoom".equals(prevType) && !"PortalRoom".equals(lastType) && last.durationTicks() <= 30) {
                prev.exitGt = last.exitGt;
                visits.remove(visits.size() - 1);
            }
        }
        return visits;
    }

    private static int findStarterRoomId(List<Stronghold.Piece> pieces, List<Visit> visits) {
        // In speedrun terminology "starter" is the first 5-way crossing visited,
        // not the tiny Start staircase piece.
        for (Visit visit : visits) {
            String type = roomTypeForId(pieces, visit.roomId);
            if ("FiveWayCrossing".equals(type)) {
                return visit.roomId;
            }
        }
        for (int i = 0; i < pieces.size(); i++) {
            if ("Start".equals(pieces.get(i).getClass().getSimpleName())) {
                return i;
            }
        }
        return pieces.isEmpty() ? -1 : 0;
    }

    private static String toJson(
        long seed,
        CPos startChunk,
        double anchorX,
        double anchorZ,
        List<Stronghold.Piece> pieces,
        List<int[]> edges,
        List<MappedSample> mapped,
        List<Visit> visits,
        int roomsEnteredCount,
        int starterRoomId,
        int starterTicks,
        int samplesTotal,
        int samplesUsed
    ) {
        StringBuilder sb = new StringBuilder(1024 * 64);
        sb.append("{\n");
        appendField(sb, "seed", Long.toString(seed), true, 2, false);
        appendField(sb, "version", quote("1.16.1"), true, 2, false);
        sb.append("  \"anchor\": {\"x\": ").append(formatDouble(anchorX)).append(", \"z\": ").append(formatDouble(anchorZ)).append("},\n");
        sb.append("  \"start_chunk\": {\"x\": ").append(startChunk.getX()).append(", \"z\": ").append(startChunk.getZ()).append("},\n");
        sb.append("  \"start_block\": {\"x\": ").append((startChunk.getX() << 4) + 8).append(", \"z\": ").append((startChunk.getZ() << 4) + 8).append("},\n");
        sb.append("  \"sample_stats\": {\n");
        appendField(sb, "total", Integer.toString(samplesTotal), true, 4, false);
        appendField(sb, "used_for_mapping", Integer.toString(samplesUsed), true, 4, false);
        appendField(sb, "mapped_rooms", Integer.toString(roomsEnteredCount), true, 4, true);
        sb.append("  },\n");
        sb.append("  \"starter\": {\n");
        appendField(sb, "room_id", Integer.toString(starterRoomId), true, 4, false);
        appendField(sb, "ticks", Integer.toString(starterTicks), true, 4, false);
        appendField(sb, "seconds", formatDouble(starterTicks / 20.0), true, 4, true);
        sb.append("  },\n");

        sb.append("  \"pieces\": [\n");
        for (int i = 0; i < pieces.size(); i++) {
            Stronghold.Piece piece = pieces.get(i);
            BlockBox box = piece.getBoundingBox();
            sb.append("    {");
            sb.append("\"id\": ").append(i).append(", ");
            sb.append("\"type\": ").append(quote(piece.getClass().getSimpleName())).append(", ");
            if (box != null) {
                sb.append("\"min_x\": ").append(box.minX).append(", ");
                sb.append("\"min_y\": ").append(box.minY).append(", ");
                sb.append("\"min_z\": ").append(box.minZ).append(", ");
                sb.append("\"max_x\": ").append(box.maxX).append(", ");
                sb.append("\"max_y\": ").append(box.maxY).append(", ");
                sb.append("\"max_z\": ").append(box.maxZ).append(", ");
                sb.append("\"center_x\": ").append(formatDouble((box.minX + box.maxX) / 2.0)).append(", ");
                sb.append("\"center_z\": ").append(formatDouble((box.minZ + box.maxZ) / 2.0));
            } else {
                sb.append("\"min_x\": null, \"min_y\": null, \"min_z\": null, ");
                sb.append("\"max_x\": null, \"max_y\": null, \"max_z\": null, ");
                sb.append("\"center_x\": null, \"center_z\": null");
            }
            sb.append("}");
            if (i + 1 < pieces.size()) {
                sb.append(",");
            }
            sb.append("\n");
        }
        sb.append("  ],\n");

        sb.append("  \"edges\": [\n");
        for (int i = 0; i < edges.size(); i++) {
            int[] edge = edges.get(i);
            sb.append("    {\"a\": ").append(edge[0]).append(", \"b\": ").append(edge[1]).append("}");
            if (i + 1 < edges.size()) {
                sb.append(",");
            }
            sb.append("\n");
        }
        sb.append("  ],\n");

        sb.append("  \"visits\": [\n");
        for (int i = 0; i < visits.size(); i++) {
            Visit visit = visits.get(i);
            sb.append("    {");
            sb.append("\"room_id\": ").append(visit.roomId).append(", ");
            sb.append("\"room_type\": ").append(quote(roomTypeForId(pieces, visit.roomId))).append(", ");
            sb.append("\"enter_gt\": ").append(visit.enterGt).append(", ");
            sb.append("\"exit_gt\": ").append(visit.exitGt).append(", ");
            sb.append("\"duration_ticks\": ").append(visit.durationTicks()).append(", ");
            sb.append("\"duration_seconds\": ").append(formatDouble(visit.durationTicks() / 20.0));
            sb.append("}");
            if (i + 1 < visits.size()) {
                sb.append(",");
            }
            sb.append("\n");
        }
        sb.append("  ],\n");

        sb.append("  \"path\": [\n");
        for (int i = 0; i < mapped.size(); i++) {
            MappedSample mappedSample = mapped.get(i);
            double sx = scaledToDouble(mappedSample.sample.x);
            double sy = scaledToDouble(mappedSample.sample.y);
            double sz = scaledToDouble(mappedSample.sample.z);
            sb.append("    {");
            sb.append("\"gt\": ").append(mappedSample.sample.gt).append(", ");
            sb.append("\"x\": ").append(formatDouble(sx)).append(", ");
            sb.append("\"y\": ").append(formatDouble(sy)).append(", ");
            sb.append("\"z\": ").append(formatDouble(sz)).append(", ");
            sb.append("\"dim\": ").append(mappedSample.sample.dim).append(", ");
            sb.append("\"room_id\": ").append(mappedSample.roomId).append(", ");
            sb.append("\"room_type\": ").append(quote(roomTypeForId(pieces, mappedSample.roomId)));
            sb.append("}");
            if (i + 1 < mapped.size()) {
                sb.append(",");
            }
            sb.append("\n");
        }
        sb.append("  ]\n");
        sb.append("}\n");
        return sb.toString();
    }

    private static String roomTypeForId(List<Stronghold.Piece> pieces, int roomId) {
        if (roomId < 0 || roomId >= pieces.size()) {
            return "Unknown";
        }
        return pieces.get(roomId).getClass().getSimpleName();
    }

    private static void appendField(
        StringBuilder sb,
        String name,
        String value,
        boolean quotedName,
        int indent,
        boolean last
    ) {
        for (int i = 0; i < indent; i++) {
            sb.append(' ');
        }
        if (quotedName) {
            sb.append(quote(name));
        } else {
            sb.append(name);
        }
        sb.append(": ").append(value);
        if (!last) {
            sb.append(",");
        }
        sb.append("\n");
    }

    private static String quote(String text) {
        StringBuilder sb = new StringBuilder(text.length() + 8);
        sb.append('"');
        for (int i = 0; i < text.length(); i++) {
            char ch = text.charAt(i);
            if (ch == '\\' || ch == '"') {
                sb.append('\\').append(ch);
            } else if (ch == '\n') {
                sb.append("\\n");
            } else if (ch == '\r') {
                sb.append("\\r");
            } else if (ch == '\t') {
                sb.append("\\t");
            } else if (ch < 32) {
                sb.append(String.format(Locale.ROOT, "\\u%04x", (int) ch));
            } else {
                sb.append(ch);
            }
        }
        sb.append('"');
        return sb.toString();
    }

    private static double scaledToDouble(int scaled) {
        return ((double) scaled) / 1000.0;
    }

    private static double clamp(double value, double min, double max) {
        if (value < min) {
            return min;
        }
        if (value > max) {
            return max;
        }
        return value;
    }

    private static String formatDouble(double value) {
        return String.format(Locale.ROOT, "%.3f", value);
    }
}
