package io;

import jacoco.TestDelta;

import java.io.BufferedReader;
import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;

public final class TestDeltaCsvReader {

    public List<TestDelta> read(File csv) throws IOException {
        List<TestDelta> out = new ArrayList<>();

        try (BufferedReader br = Files.newBufferedReader(csv.toPath(), StandardCharsets.UTF_8)) {
            String header = br.readLine(); // skip header
            if (header == null) return out;

            String line;
            while ((line = br.readLine()) != null) {
                if (line.isBlank()) continue;

                // We wrote this CSV ourselves, so it's safe to split on comma (no quoted commas expected in selector)
                String[] parts = line.split(",", -1);
                if (parts.length < 5) continue;

                String selector = parts[0].trim();
                int addedLines = parseInt(parts[1]);
                int addedMethods = parseInt(parts[2]);
                int addedBranches = parseInt(parts[3]);
                int addedInstr = parseInt(parts[4]);

                out.add(new TestDelta(selector, addedLines, addedMethods, addedBranches, addedInstr));
            }
        }
        return out;
    }

    private int parseInt(String s) {
        try { return Integer.parseInt(s.trim()); }
        catch (Exception e) { return 0; }
    }
}