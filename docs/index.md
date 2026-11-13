# Unitree Go1

This package connects a pinned Unitree Go1 model and MuJoCo Playground walking
policy to Living Proof Labs simulation verification. Start with the
[existing package guide](../README.md) for configuration, setup commands,
expected evidence files, and limitations.

Only `playground` is offered. This is simulation evidence, not a hardware safety
claim. The package records observations; the separately pinned verifier and
reporter assess and grade them. A capability FAIL is not necessarily a CI failure.

- [Guides: package setup and behavior](../README.md)
- [Verification and manual preview](verification.md)
- [Contribute, help, and licensing](contribute.md)
- [Documentation home and related manuals](https://docs.livingprooflabs.com/)

The repository owns this manual. Upstream policies, schemas, simulation execution,
and grading remain with their respective projects linked from the package guide.
