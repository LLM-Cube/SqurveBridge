# Third-Party Sources and Research Provenance

SqurveBridge is built on the upstream Squrve framework and integrates ideas from
published Text-to-SQL methods. The repository MIT license applies only to
material for which the project can grant that license. Upstream notices,
licenses, and dataset terms continue to apply.

Candidate repositories are algorithm documentation, not vendored runtime
dependencies. A public integration must record its paper, upstream URL, exact
revision, license, local Actor/config mapping, and dataset terms. Small copied or
modified compatibility fragments must retain their original notices.

The original Squrve copyright line remains in `LICENSE`. Spider, BIRD, and every
other benchmark remain governed by their upstream distribution terms; see
[docs/BENCHMARKS.md](docs/BENCHMARKS.md).

The showcase replay (`demo-app/src/full-flow/showcaseFixture.js`) embeds 50
questions and gold SQL from the Spider dev set (Yu et al., EMNLP 2018,
https://github.com/taoyds/spider), distributed under CC BY-SA 4.0. They are
reproduced here with attribution and remain under that license; the model
predictions and timings next to them come from SqurveBridge runs.

The embedded Pi Agent runtime is consumed as the npm package
[`@earendil-works/pi-coding-agent`](https://github.com/earendil-works/pi),
pinned to an exact version in `demo/package.json` and installed into
`demo/node_modules` at build time. Pi is distributed under its upstream MIT
license; the package's own license and notices remain authoritative.
SqurveBridge previously vendored the Pi source tree under `pi/` at commit
`dcfe36c79702ec240b146c45f167ab75ecddd205` (packages `0.80.7`); that history
remains in Git. SqurveBridge integration code does not modify Pi internals and
lives in `demo/pi_agent_bridge.mjs`, `demo/pi_backend.py`, `demo/pi_api.py`,
`config/pi_models.json`, and `demo-app/src/AgentHarness.jsx`.
