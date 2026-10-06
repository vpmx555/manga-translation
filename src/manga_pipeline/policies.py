"""Versioned analysis contracts recorded in each run manifest."""

ESSENTIAL_POLICIES = {"essential-v1", "essential-v2", "essential-v3"}
DEFAULT_ANALYSIS_POLICY = "essential-v3"
DEFAULT_SPEAKER_POLICY = "individual-v1"


def is_essential_policy(policy):
    return policy in ESSENTIAL_POLICIES
