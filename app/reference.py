"""Lab reference data: what each test is, which specimen it needs, and plausible ranges.

Used by the rules engine and by the synthetic data generator (single source of truth).
In a real system this would come from a maintained terminology/lab-catalog service.
"""

SPECIMEN_NAMES = {"SER": "Serum", "BLD": "Whole blood"}

# LOINC code -> name, expected specimen, unit, plausible (min, max), reference (low, high)
# "plausible" = physiologically possible, so outside it is almost surely a data-entry error.
# "ref"       = normal range, so outside it is merely abnormal (Week 3, a warning not an error).
TESTS = {
    "2345-7": dict(name="Glucose [Mass/volume] in Serum or Plasma", specimen="SER", unit="mg/dL", plausible=(10, 1500), ref=(70, 99)),
    "2160-0": dict(name="Creatinine [Mass/volume] in Serum or Plasma", specimen="SER", unit="mg/dL", plausible=(0.1, 20), ref=(0.6, 1.3)),
    "2951-2": dict(name="Sodium [Moles/volume] in Serum or Plasma", specimen="SER", unit="mmol/L", plausible=(100, 180), ref=(135, 145)),
    "718-7": dict(name="Hemoglobin [Mass/volume] in Blood", specimen="BLD", unit="g/dL", plausible=(2, 25), ref=(12, 17.5)),
    "6690-2": dict(name="Leukocytes [#/volume] in Blood", specimen="BLD", unit="10*3/uL", plausible=(0.5, 100), ref=(4.0, 11.0)),
}
