# Verified engineering documentation

## Api Change Summary

Optional caller-provided future UTC expiration and HTTP 400 for nonfuture creation input are preserved; existing HTTP 201/404/410 and redirect behavior are covered by retained integration tests.

## Implementation Notes

Clock implementation is the exact human-reviewed, assistant-authored patch, not live AI source generation.

UrlService retains its Spring constructor, delegates to Clock.systemUTC(), and adds an explicit Clock constructor used by deterministic tests.

Verified implementation SHA-256: 3f1a3ef024c57d61b712cdc7d21ffadc5c8857143564643d5b9beab4106463a2.

The live model supplied constrained test-case JSON; a trusted deterministic renderer produced JUnit source.

## Testing Evidence

Original baseline: 8 passing tests.

Correct implementation: 12 tests, all passed, with original tests byte-identical.

Controlled equality-allowing fixture: 12 tests; only generated equalNowExpiration failed. This was deliberately seeded, not a spontaneous model defect.

## Limitations

Only the reviewed expiration profile and vetted renderer are demonstrated; unrestricted test synthesis and complete SDLC integration are not established.

The earlier four-file engineering plan remains unapproved. The Clock/test scope does not substitute for an approved implementation task plan.

## Release Considerations

A hash-bound human test-results decision is required. Documentation and test success alone cannot authorize release.

Deployment, merge, release authorization, security/compliance sign-off, and production operational readiness are not established by this demonstration.

