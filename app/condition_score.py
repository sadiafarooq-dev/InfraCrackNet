# A single 0-100 "PCI-inspired Condition Score" for Road reports.
#
# HONEST DISCLOSURE (see PROJECT_LOG.md section 71 for the full writeup):
# this is NOT a real ASTM D6433 Pavement Condition Index. The real PCI is
# computed from a full field survey of a whole pavement "sample unit" --
# every distress type present, at every severity level, across the entire
# surveyed area -- run through a set of published deduct-value curves. This
# project only ever has ONE photo/frame of ONE crack per report, so there's
# no way to do that real calculation here.
#
# What this genuinely is: a simple, disclosed, project-specific formula
# that reuses the REAL PCI 0-100 scale and the REAL PCI rating labels
# (Good/Satisfactory/Fair/Poor/Very Poor/Serious/Failed -- the actual
# published bands), built from our own two model outputs -- Model 3's
# Severity, and, for Road reports, Model 2's Crack Type. It's "inspired
# by" real PCI, not equal to it -- shown to the Inspector/Engineer with
# that exact wording so nobody mistakes it for a real, standards-based
# score.
#
# Same simple-math, no-training-needed spirit as Model 3's own Severity
# cutoffs (PROJECT_LOG.md section 60) -- these point values are our own
# disclosed judgment call, not something researched or trained.

# How many points to take off for Model 3's Severity finding.
SEVERITY_DEDUCTIONS = {
    "Low": 15,
    "Medium": 40,
    "High": 65,
}

# How many EXTRA points to take off for Model 2's Crack Type, Road reports
# only. Ordered by how structurally serious each distress generally is in
# real pavement-engineering distress guides: a Pothole is a full loss of
# pavement structure (most serious); Alligator Cracking signals structural
# fatigue in the base/subgrade; Transverse and Longitudinal cracking are
# usually surface-level (often thermal or reflective) and less serious --
# Longitudinal slightly less so than Transverse, since it often follows a
# paving-lane joint rather than a structural fault line. A photo where
# Model 2 found no confident damage type gets no extra deduction here --
# the Severity number above is still doing its job either way.
TYPE_DEDUCTIONS = {
    "Pothole": 25,
    "Alligator Crack": 20,
    "Transverse Crack": 10,
    "Longitudinal Crack": 5,
}

# The REAL published PCI rating bands (0-100), used as-is -- these are not
# our own invention, see PROJECT_LOG.md section 71 for the source.
RATING_BANDS = [
    (86, "Good"),
    (71, "Satisfactory"),
    (56, "Fair"),
    (41, "Poor"),
    (26, "Very Poor"),
    (11, "Serious"),
    (0, "Failed"),
]


def _rating_for_score(score: int) -> str:
    for threshold, label in RATING_BANDS:
        if score >= threshold:
            return label
    return "Failed"  # unreachable in practice (score is always >= 0), kept as a safe fallback


def compute_condition_score(severity_label, type_label=None):
    """Turns Model 3's Severity (and, for Road reports, Model 2's Crack
    Type) into (score, rating). Returns (None, None) whenever there's no
    real traced crack shape to score -- severity_label isn't one of
    "Low"/"Medium"/"High" (i.e. it's None, "No crack shape detected", or
    "Analysis error") -- same "don't fabricate a number" rule already used
    everywhere else in this project (e.g. model3_confidence).

    type_label should only ever be passed for Road reports (Model 2 never
    runs on Buildings -- PROJECT_LOG.md section 17) -- callers are
    responsible for not calling this at all for a Building report, since
    PCI is a pavement-specific metric.
    """
    if severity_label not in SEVERITY_DEDUCTIONS:
        return None, None

    deduction = SEVERITY_DEDUCTIONS[severity_label]
    if type_label in TYPE_DEDUCTIONS:
        deduction += TYPE_DEDUCTIONS[type_label]

    score = max(0, 100 - deduction)
    return score, _rating_for_score(score)
