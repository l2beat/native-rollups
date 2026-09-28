# Execution sharding

A more ambitious idea is to use native rollups explicitly as a form of execution sharding.

Even once verification is no longer the bottleneck, the next bottleneck is serial execution when generating the block witness, and one way to address it is parallel block building. This is what rollups already do: separate state and a separate block-building pipeline that communicates asynchronously with L1 through [messaging](./messaging.md). Native rollups would allow multiple instances that are identical to L1, trustless and fully proven, as the original sharding vision intended.

Two talks cover this in more detail:

- [Ethereum's roadmap to 10M TPS](https://www.youtube.com/watch?v=0O3JyJpMQLQ) (TOKEN2049 Singapore 2025): a higher-level talk on the intuitions behind parallel block building.
- [Execution sharding through native rollups](https://www.youtube.com/watch?v=69NKLnejppk) (ETHDenver 2026): a lower-level talk comparing sharding through native rollups with the live sharding implementations of Polkadot and Near.
