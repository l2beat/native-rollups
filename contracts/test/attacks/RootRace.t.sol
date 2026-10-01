// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {L2MessengerTest} from "../L2Messenger.t.sol";

/// @notice Attack round: a newer proven root invalidates claims built
///         against the previous one. Left as is: the L2 node orders
///         transactions by arrival, so claims built against an older head
///         come first, and only a transaction proving the root of the
///         block's own anchor, ordered before them, makes them fail.
contract RootRaceTest is L2MessengerTest {
    function test_newerRootInvalidatesPendingClaims() public {
        // A claimer builds a claim against block 0's root.
        _prove(_rootProof(0));
        Claim memory c = _claim(0, 0);

        // Anyone proves block 1's newer root first, for instance in a
        // transaction the builder orders before the claim.
        _prove(_rootProof(1));

        // The claim, valid when built, now fails.
        vm.expectRevert(bytes("message not in root"));
        messenger.claimL1Message(c.m, c.path, CLAIMER);
    }
}
