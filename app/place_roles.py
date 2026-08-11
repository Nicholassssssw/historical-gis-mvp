ROUTE_ROLE_CHOICES = {"passed", "mentioned_only", "passed_and_mentioned"}

# Older extractions used more detailed physical-travel labels. They remain
# route locations when an existing project is opened after this UI change.
MAPPED_ROUTE_ROLES = {
    "passed",
    "passed_and_mentioned",
    "visited",
    "stayed",
    "departed",
    "arrived",
}


def normalize_route_role(value: str | None) -> str:
    if value == "passed_and_mentioned":
        return "passed_and_mentioned"
    if value in MAPPED_ROUTE_ROLES:
        return "passed"
    return "mentioned_only"
