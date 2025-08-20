# The Native Rollups Book

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [What are native rollups?](#what-are-native-rollups)
- [Problem statement](#problem-statement)
  - [Governance risk](#governance-risk)
  - [Bug risk](#bug-risk)
- [The `EXECUTE` precompile](#the-execute-precompile)
- [Purpose of this book](#purpose-of-this-book)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## What are native rollups?

A native rollup is a new type of EVM-based rollup that directly makes use of Ethereum's execution environment for their own state transitions, removing the need for complex and hard-to-maintain proof systems.

## Problem statement

### Governance risk

Today, EVM-based rollups need to make a trade-off between security and L1 equivalence. Everytime Ethereum forks, EVM-based rollups need to go through bespoke governance processes to upgrade the contracts and maintain equivalence with L1 features. A rollup governance system cannot be forced to follow Ethereum's governance decisions, and thus is free to arbitrarily diverge from it. Because of this, the best that an EVM-based rollup that strives for L1 equivalence can do is to provide a long exit window for their users, to protect them from its governance going rogue.

Exit windows present themselves with yet another trade-off:
- They can be short and reduce the un-equivalence time for users, but reducing at the same time the cases in which the exit window is effective. All protocols that require long enough time delays (e.g. vesting contracts, staking contracts, timelocked governance) would not be protected by the exit window.
- They can be long to protect more cases, at the cost of increased un-equivalence time. It's important to remember though that no finite (and reasonable) exit window length can protect all possible applications.

The only way to avoid governance risk today is to give up upgrades, remain immutable and accept that the rollup will increasingly diverge from L1 over time.

### Bug risk

EVM-based rollups need to implement complex proof systems just to be able to support what Ethereum already provides on L1. Such proof systems, even though they are getting faster and cheaper over time, are still considered not safe to be used in production in a permissionless environment. Rollups today aim to reduce this problem by implementing multiple independent proof systems that need to agree before a state transition can be considered valid, which increases protocol costs and complexity.

## The `EXECUTE` precompile

Native rollups solve these problems by replacing complex proof systems with a call to the `EXECUTE` precompile, which under the hood implements a recursive call to Ethereum's own execution environment. As a consequence, every time Ethereum forks, native rollups automatically adopt the new features without the need for dedicated governance processes. Moreover, the `EXECUTE` precompile is "bug-free" by construction, in the sense that any bug in the precompile is also a bug in Ethereum itself which will always be forked and fixed by the Ethereum community.

## Purpose of this book

This book is designed to serve as a comprehensive resource for understanding and contributing to our work on native rollups.

Goals of this book include:

- Provide in-depth explanations of the inner workings of the `EXECUTE` precompile.
- Provide technical guidance on how native rollups can be built around the precompile.
- Educate readers on the benefits of native execution and how the proposal compares to other scalability solutions.
- Foster a community of like-minded individuals by providing resources, tools and best practices for collaboration.
