"""Compatibility facade for the shared, standard-library-only KQS v1 contract."""

try:
    from kqs_publication.contract import (
        KqsContractError, MAX_SELECTED_ENTRIES, RECOMMENDATION_SCHEMA_VERSION,
        canonical_payload_digest, validate_recommendation_payload,
    )
except ModuleNotFoundError as exc:
    if exc.name != "kqs_publication":
        raise
    # Source checkout without installation; namespace import, no sys.path mutation.
    from engine.kqs.src.kqs_publication.contract import (
        KqsContractError, MAX_SELECTED_ENTRIES, RECOMMENDATION_SCHEMA_VERSION,
        canonical_payload_digest, validate_recommendation_payload,
    )
