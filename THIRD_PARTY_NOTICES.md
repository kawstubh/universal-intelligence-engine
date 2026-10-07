# Third-Party Notices

UIE depends on third-party software. These components are separate works and remain governed by their respective licenses.

## Direct runtime dependencies declared by UIE

| Package | License | Source |
|---|---|---|
| FastAPI | MIT | https://github.com/fastapi/fastapi |
| Uvicorn | BSD-3-Clause | https://github.com/Kludex/uvicorn |
| Pydantic | MIT | https://github.com/pydantic/pydantic |
| cryptography | Apache-2.0 OR BSD-3-Clause | https://github.com/pyca/cryptography |

The current project declares version ranges rather than pinning every transitive dependency in `pyproject.toml`. A release-specific software bill of materials (SBOM) and complete transitive dependency/license scan should therefore be generated before a commercial transaction or redistribution package.

## Separation of rights

Nothing in the UIE proprietary license grants rights to third-party components beyond the rights already provided by their respective licenses. Required copyright and license notices must be retained where applicable.

## Release due-diligence requirement

For each commercial release, record:

- exact dependency lock/installation set;
- direct and transitive dependency licenses;
- known security advisories;
- source URLs;
- notices required by each license;
- generated SBOM where practical.

The table above is a starting inventory, not a substitute for a release-specific legal/license audit.
