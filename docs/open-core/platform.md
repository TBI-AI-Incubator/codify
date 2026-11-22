---
title: The hosted Platform
description: What Codify Platform adds on top of the open core, how it depends on the core, and what stays the same whichever you run.
sidebar:
  order: 2
---

Codify Platform is a hosted product built on this core. What this page says about its
features comes from the public [Codify website](https://codify.centreai.global).

<p><a class="cta" href="https://centreai.global/interest?product=codify">Talk to us about Codify Platform</a></p>

## What stays the same

The website lists acquisition, OCR, structuring, translation and repair, the read API, MCP,
retrieval and comparison as open source, and this repository holds that code under the
core's licence. A government with its own engineers can run the core on its own
infrastructure without the Platform, with a model endpoint of its choice. [What is open](what-is-open.md) gives
the status of each part, including those that are Library only.

## What the Platform adds

| Area | What the Platform provides |
| --- | --- |
| Access | organisations, memberships and the jurisdictions each can reach; authentication and role-based access control; token issuance and the lifecycle of personal access tokens |
| Audit | a tamper-evident trail of who read and changed what |
| Assessment | lens detectors, including comparison against the EU acquis, and which organisations may use them |
| Analytics | comparison of patterns across collections of laws |
| Research | a research agent and its tools |
| Calibration | adapting Codify to a jurisdiction's documents and legal conventions; the core gives you the means to do it yourself ([Write a jurisdiction config](../guide/write-a-jurisdiction-config.md)) |
| Service | hosting, backup, upgrade, data residency, delivery and support |

It comes in three deployment forms: Cloud, Gov Cloud and On Prem.

## How the Platform depends on the core

The Platform installs the core as a package at a pinned revision and imports from it; the
core never imports the Platform
([decision record 006](../decisions/006-repository-topology.md)). Two checks here keep
that dependency visible:

- [`codify/contract/product_surface.txt`](../../codify/contract/product_surface.txt)
  lists every core symbol the Platform imports, and a test fails if one stops resolving.
- The `contract` job in
  [`.github/workflows/downstream-platform.yml`](../../.github/workflows/downstream-platform.yml)
  runs the Platform's own contract test against each pull request to the core.

Neither gives the Platform a different core. A change it needs lands here, in public, and
reaches the Platform when it moves its pin. [Versioning](../project/versioning.md) says
what the manifest does and does not promise.

## If you run the core alone

You provide what the table above lists, or go without it. Most deployments need these
first:

| You need | In the core | See |
| --- | --- | --- |
| Sign-in and roles | **Not in the core** | [Security](../project/security.md) |
| An audit trail | **Not in the core** | [Security](../project/security.md) |
| Durable, restartable ingest runs | **Not in the core**: `codify serve` keeps runs in memory | [Deployment](../operate/deployment.md) |
| Your own analysis over stored law | **Library only**: the lens interface | [What is open](what-is-open.md) |

## Status

| Capability | Status |
| --- | --- |
| Access, audit, assessment, analytics, research, calibration and service, as listed above | **Not in the core** |
| Jurisdiction configs, the means to calibrate yourself | **Available**: the `--jurisdiction` option of `ingest-one` |
| The core's pipeline, servers and web app | **Available**: [What is open](what-is-open.md) gives the status of each part |

## Next

[Contributing](../project/contributing.md): change the core itself.
