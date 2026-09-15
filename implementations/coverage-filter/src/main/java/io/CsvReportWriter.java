package io;

import jacoco.ClassDelta;
import jacoco.TestDelta;
import model.LineDeltaRow;

import java.io.BufferedWriter;
import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

public final class CsvReportWriter {

    public void writeKeptSelectors(File out, List<String> keptSelectors) throws IOException {
        ensureParent(out);
        try (BufferedWriter w = Files.newBufferedWriter(out.toPath(), StandardCharsets.UTF_8)) {
            w.write("test_selector\n");
            for (String s : keptSelectors) {
                w.write(csv(s));
                w.write("\n");
            }
        }
    }

    public void writeTestDeltas(File out, List<TestDelta> rows) throws IOException {
        ensureParent(out);
        try (BufferedWriter w = Files.newBufferedWriter(out.toPath(), StandardCharsets.UTF_8)) {
            w.write("test_selector,added_lines,added_methods,added_branches,added_instructions\n");
            for (TestDelta t : rows) {
                w.write(csv(t.getTestSelector())); w.write(",");
                w.write(Integer.toString(t.getAddedLines())); w.write(",");
                w.write(Integer.toString(t.getAddedMethods())); w.write(",");
                w.write(Integer.toString(t.getAddedBranches())); w.write(",");
                w.write(Integer.toString(t.getAddedInstructions()));
                w.write("\n");
            }
        }
    }

    public void writeClassDeltas(File out, List<ClassDelta> rows) throws IOException {
        ensureParent(out);
        try (BufferedWriter w = Files.newBufferedWriter(out.toPath(), StandardCharsets.UTF_8)) {
            w.write("class_name,added_lines,added_methods,added_branches,added_instructions\n");
            for (ClassDelta d : rows) {
                w.write(csv(d.getClassName())); w.write(",");
                w.write(Integer.toString(d.getAddedLines())); w.write(",");
                w.write(Integer.toString(d.getAddedMethods())); w.write(",");
                w.write(Integer.toString(d.getAddedBranches())); w.write(",");
                w.write(Integer.toString(d.getAddedInstructions()));
                w.write("\n");
            }
        }
    }

    public void writeLineDeltas(File out, List<LineDeltaRow> rows) throws IOException {
        ensureParent(out);
        try (BufferedWriter w = Files.newBufferedWriter(out.toPath(), StandardCharsets.UTF_8)) {
            w.write("test_selector,class_name,newly_covered_lines,upgraded_to_full_lines\n");
            for (LineDeltaRow r : rows) {
                w.write(csv(r.getTestSelector())); w.write(",");
                w.write(csv(r.getClassName())); w.write(",");
                w.write(csv(r.getNewlyCoveredRanges())); w.write(",");
                w.write(csv(r.getUpgradedToFullRanges()));
                w.write("\n");
            }
        }
    }

    public String toRanges(List<Integer> lines) {
        if (lines == null || lines.isEmpty()) return "";
        List<Integer> sorted = new ArrayList<>(lines);
        Collections.sort(sorted);

        StringBuilder sb = new StringBuilder();
        int start = sorted.get(0);
        int prev = start;

        for (int i = 1; i < sorted.size(); i++) {
            int cur = sorted.get(i);
            if (cur == prev + 1) {
                prev = cur;
                continue;
            }
            appendRange(sb, start, prev);
            sb.append(';');
            start = prev = cur;
        }
        appendRange(sb, start, prev);
        return sb.toString();
    }

    private void appendRange(StringBuilder sb, int start, int end) {
        if (start == end) sb.append(start);
        else sb.append(start).append('-').append(end);
    }

    private void ensureParent(File out) throws IOException {
        if (out.getParentFile() != null) {
            Files.createDirectories(out.toPath().getParent());
        }
    }

    private String csv(String s) {
        if (s == null) return "";
        boolean needsQuotes = s.contains(",") || s.contains("\"") || s.contains("\n") || s.contains("\r");
        if (!needsQuotes) return s;
        return "\"" + s.replace("\"", "\"\"") + "\"";
    }
}