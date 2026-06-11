# Supply chain verification

This page describes the release artifact checks that take effect starting with
the next release after this document lands.

## Trust model

piia-engram publishes to PyPI through OIDC Trusted Publishing, so the release
workflow does not need a long-lived PyPI API token. A published release then
passes through the existing on-main ancestry check, the release gate suite, the
SBOM hygiene check, GitHub artifact attestations, and finally PyPI publishing.

The attestation here is a build provenance attestation. It says the artifact was
produced by this repository's release workflow for that build. The SBOM is a
software bill of materials generated from an isolated environment where the
built wheel is installed.

## Verify provenance

Download the wheel you want to inspect, then run:

```bash
gh attestation verify piia_engram-<version>-py3-none-any.whl --repo Patdolitse/piia-engram
```

This verifies the build provenance attestation for the local artifact.

## Verify the SBOM attestation

Use the same wheel and require the CycloneDX predicate type:

```bash
gh attestation verify piia_engram-<version>-py3-none-any.whl --repo Patdolitse/piia-engram --predicate-type https://cyclonedx.org/bom
```

This verifies that an SBOM attestation is attached to the artifact.

## Get the SBOM

The release workflow uploads `dist/piia-engram-sbom.cdx.json` as the `sbom`
workflow artifact for manual inspection. GitHub Actions artifacts have a
retention period and should not be treated as permanent release assets.

The durable verification path is the SBOM attestation, where the SBOM is carried
as the attestation predicate.

## Boundary

These attestations prove a narrow release fact: the artifact came from this
repository's release workflow for that build. They do not prove that the code or
dependencies have no vulnerabilities, they are not a third-party security audit,
and they do not claim reproducible builds.
