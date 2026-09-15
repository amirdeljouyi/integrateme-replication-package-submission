package model;

import java.util.Collections;
import java.util.HashSet;
import java.util.Objects;
import java.util.Set;

public final class CoverageSet {
    private final Set<String> coveredUnits;

    public CoverageSet(Set<String> coveredUnits) {
        Objects.requireNonNull(coveredUnits, "coveredUnits");
        this.coveredUnits = Collections.unmodifiableSet(new HashSet<>(coveredUnits));
    }

    public Set<String> getCoveredUnits() {
        return coveredUnits;
    }

    public boolean addsAnythingBeyond(CoverageSet baseline) {
        for (String u : coveredUnits) {
            if (!baseline.coveredUnits.contains(u)) return true;
        }
        return false;
    }

    public CoverageSet union(CoverageSet other) {
        Set<String> u = new HashSet<>(this.coveredUnits);
        u.addAll(other.coveredUnits);
        return new CoverageSet(u);
    }

    public CoverageSet subtract(CoverageSet other) {
        Set<String> d = new HashSet<>(this.coveredUnits);
        d.removeAll(other.coveredUnits);
        return new CoverageSet(d);
    }
}