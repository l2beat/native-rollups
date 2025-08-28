# Open problems
<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [`apply_body` mutability](#apply_body-mutability)
- [Past blobs references](#past-blobs-references)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->
## `apply_body` mutability

The `apply_body` interface is not immutable, and has changed in the past with the addition of `withdrawals` and will change in the future with the addition of `inclusion_list_transactions`in [FOCIL](https://github.com/ethereum/execution-specs/pull/1349/files). Usage of `state_transition` instead of `apply_body` might appear to be more stable at first, as it just takes a `Blockchain` and a `Block`, but FOCIL adds `inclusion_list_transactions` there too.

Every time the `apply_body` interface changes, the `EXECUTE` precompile needs to be updated to potentially add "default values" that leave untouch the `EXECUTE` interface. As a consequence, native rollups would not be able to automatically benefit from new features that are added outside the `apply_body` function but that affect it too. It's an open question if is always possible to add default values for new parameters.

## Past blobs references

WIP.
